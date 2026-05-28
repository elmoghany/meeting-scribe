// meetingscribe-bot — Zoom Linux Meeting SDK headless bot.
//
// Authenticates with a Meeting SDK JWT, joins a meeting as a participant,
// subscribes to the mixed audio raw-data stream, writes 32 kHz mono PCM16 to
// the requested WAV file, and exits when the meeting ends (or --max-sec).
//
// Compile with the Zoom Linux Meeting SDK headers + libmeetingsdk.so.
// See bot/README.md for build + run instructions.
//
// NOTE: Zoom Meeting SDK class names have shifted across versions. This file
// targets the 5.x/6.x Linux SDK ("zoom-meeting-sdk-linux"). If a header symbol
// differs in your downloaded SDK, the comment near each use points to the
// canonical name to grep for.
//
// SPDX-License-Identifier: MIT
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <string>
#include <thread>

#include <getopt.h>

// Zoom SDK headers
#include "zoom_sdk.h"
#include "auth_service_interface.h"
#include "meeting_service_interface.h"
#include "setting_service_interface.h"
#include "rawdata/rawdata_audio_helper_interface.h"
#include "rawdata/zoom_rawdata_api.h"

using namespace ZOOM_SDK_NAMESPACE;

// ----- args ---------------------------------------------------------------
struct Args {
    std::string meeting_number;
    std::string passcode;
    std::string name = "MeetingScribe Bot";
    std::string jwt;
    std::string audio_out = "system.wav";
    int max_sec = 14400;
};

static Args parseArgs(int argc, char** argv) {
    Args a;
    static const option opts[] = {
        {"meeting",    required_argument, nullptr, 'm'},
        {"passcode",   required_argument, nullptr, 'p'},
        {"name",       required_argument, nullptr, 'n'},
        {"jwt",        required_argument, nullptr, 'j'},
        {"audio-out",  required_argument, nullptr, 'o'},
        {"max-sec",    required_argument, nullptr, 's'},
        {nullptr,      0,                 nullptr, 0},
    };
    int c;
    while ((c = getopt_long(argc, argv, "m:p:n:j:o:s:", opts, nullptr)) != -1) {
        switch (c) {
            case 'm': a.meeting_number = optarg; break;
            case 'p': a.passcode       = optarg; break;
            case 'n': a.name           = optarg; break;
            case 'j': a.jwt            = optarg; break;
            case 'o': a.audio_out      = optarg; break;
            case 's': a.max_sec        = std::atoi(optarg); break;
            default:
                std::cerr << "usage: meetingscribe-bot --meeting N --passcode P "
                             "--name S --jwt T --audio-out F --max-sec N\n";
                std::exit(2);
        }
    }
    if (a.jwt.empty() && std::getenv("BOT_JWT")) a.jwt = std::getenv("BOT_JWT");
    if (a.meeting_number.empty() || a.jwt.empty()) {
        std::cerr << "missing --meeting or --jwt\n";
        std::exit(2);
    }
    return a;
}

// ----- WAV writer (PCM16, 32 kHz mono) -----------------------------------
class WavWriter {
    std::ofstream out_;
    uint32_t      data_bytes_ = 0;
    static constexpr uint32_t SR = 32000;
    static constexpr uint16_t CH = 1;

    static void w32(std::ofstream& s, uint32_t v) {
        s.write(reinterpret_cast<const char*>(&v), 4);
    }
    static void w16(std::ofstream& s, uint16_t v) {
        s.write(reinterpret_cast<const char*>(&v), 2);
    }

public:
    bool open(const std::string& path) {
        out_.open(path, std::ios::binary | std::ios::out | std::ios::trunc);
        if (!out_) return false;
        // 44-byte RIFF header; sizes patched in close()
        out_.write("RIFF\x00\x00\x00\x00WAVEfmt ", 16);
        w32(out_, 16);                       // PCM fmt chunk size
        w16(out_, 1);                        // PCM format
        w16(out_, CH);                       // channels
        w32(out_, SR);                       // sample rate
        w32(out_, SR * CH * 2);              // byte rate
        w16(out_, CH * 2);                   // block align
        w16(out_, 16);                       // bits/sample
        out_.write("data\x00\x00\x00\x00", 8);
        return true;
    }
    void write(const int16_t* pcm, size_t n_samples) {
        if (!out_ || !pcm || !n_samples) return;
        out_.write(reinterpret_cast<const char*>(pcm), n_samples * sizeof(int16_t));
        data_bytes_ += n_samples * sizeof(int16_t);
    }
    void close() {
        if (!out_) return;
        out_.flush();
        out_.seekp(4); w32(out_, 36 + data_bytes_);    // RIFF size
        out_.seekp(40); w32(out_, data_bytes_);        // data size
        out_.close();
    }
};

// ----- audio delegate -----------------------------------------------------
class AudioDelegate : public IZoomSDKAudioRawDataDelegate {
    WavWriter& wav_;
public:
    explicit AudioDelegate(WavWriter& w) : wav_(w) {}
    void onMixedAudioRawDataReceived(AudioRawData* data) override {
        if (!data || !data->GetBuffer()) return;
        wav_.write(reinterpret_cast<const int16_t*>(data->GetBuffer()),
                   data->GetBufferLen() / sizeof(int16_t));
    }
    void onOneWayAudioRawDataReceived(AudioRawData*, uint32_t) override {}
    void onShareAudioRawDataReceived(AudioRawData*) override {}
};

// ----- meeting + auth sinks -----------------------------------------------
static std::atomic<bool> g_done{false};
static IMeetingService*  g_meeting = nullptr;
static IZoomSDKAudioRawDataDelegate* g_audio_del = nullptr;

class MeetingSink : public IMeetingServiceEvent {
public:
    void onMeetingStatusChanged(MeetingStatus status, int result) override {
        std::cerr << "[bot] meeting status=" << status << " result=" << result << "\n";
        if (status == MEETING_STATUS_INMEETING) {
            // Subscribe to mixed audio once we're actually in.
            if (auto* h = GetAudioRawdataHelper(); h && g_audio_del) {
                h->subscribe(g_audio_del);
            }
        } else if (status == MEETING_STATUS_ENDED ||
                   status == MEETING_STATUS_FAILED ||
                   status == MEETING_STATUS_DISCONNECTING) {
            g_done = true;
        }
    }
    void onMeetingParameterNotification(const MeetingParameter*) override {}
    void onMeetingStatisticsWarningNotification(StatisticsWarningType) override {}
};

class AuthSink : public IAuthServiceEvent {
    Args& a_;
public:
    explicit AuthSink(Args& a) : a_(a) {}
    void onAuthenticationReturn(AuthResult ret) override {
        if (ret != AUTHRET_SUCCESS) {
            std::cerr << "[bot] auth failed: " << ret << "\n";
            g_done = true;
            return;
        }
        std::cerr << "[bot] auth ok; joining " << a_.meeting_number << "\n";
        JoinParam jp;
        jp.userType = SDK_UT_WITHOUT_LOGIN;
        JoinParam4WithoutLogin& wp = jp.param.withoutloginuserJoin;
        wp.meetingNumber = std::strtoull(a_.meeting_number.c_str(), nullptr, 10);
        wp.userName      = a_.name.c_str();
        wp.psw           = a_.passcode.c_str();
        wp.vanityID      = nullptr;
        wp.customer_key  = nullptr;
        wp.webinarToken  = nullptr;
        wp.isVideoOff    = true;
        wp.isAudioOff    = true;   // don't broadcast audio; we only receive
        if (g_meeting) g_meeting->Join(jp);
    }
    void onLoginReturnWithReason(LOGINSTATUS, IAccountInfo*, LoginFailReason) override {}
    void onLogout() override {}
    void onZoomIdentityExpired() override {}
    void onZoomAuthIdentityExpired() override {}
};

// ----- main ---------------------------------------------------------------
int main(int argc, char** argv) {
    Args a = parseArgs(argc, argv);

    InitParam init;
    init.strWebDomain      = "https://zoom.us";
    init.strSupportUrl     = "https://zoom.us";
    init.enableGenerateDump= true;
    init.enableLogByDefault= true;
    init.uiLogFileSize     = 5;
    if (SDKError e = InitSDK(init); e != SDKERR_SUCCESS) {
        std::cerr << "InitSDK failed: " << e << "\n"; return 1;
    }

    IAuthService* auth = nullptr;
    CreateAuthService(&auth);
    AuthSink authSink(a);
    auth->SetEvent(&authSink);

    CreateMeetingService(&g_meeting);
    MeetingSink mSink;
    g_meeting->SetEvent(&mSink);

    WavWriter wav;
    if (!wav.open(a.audio_out)) {
        std::cerr << "cannot open " << a.audio_out << "\n"; return 1;
    }
    AudioDelegate audioDel(wav);
    g_audio_del = &audioDel;

    AuthContext ctx;
    ctx.jwt_token = a.jwt.c_str();
    auth->SDKAuth(ctx);

    const auto t0 = std::chrono::steady_clock::now();
    while (!g_done) {
        std::this_thread::sleep_for(std::chrono::milliseconds(200));
        const auto el = std::chrono::duration_cast<std::chrono::seconds>(
            std::chrono::steady_clock::now() - t0).count();
        if (el > a.max_sec) {
            std::cerr << "[bot] max-sec reached, leaving\n"; break;
        }
    }

    if (auto* h = GetAudioRawdataHelper()) h->unSubscribe();
    if (g_meeting) g_meeting->Leave(LEAVE_MEETING);
    wav.close();
    if (g_meeting) { DestroyMeetingService(g_meeting); g_meeting = nullptr; }
    if (auth)      DestroyAuthService(auth);
    CleanUPSDK();

    std::cerr << "[bot] wrote " << a.audio_out << "\n";
    return 0;
}
