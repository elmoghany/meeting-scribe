cmake_minimum_required(VERSION 3.16)
project(meetingscribe_bot CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Path to the unpacked Zoom Linux Meeting SDK (override with -DZOOMSDK_ROOT=...)
if(NOT DEFINED ZOOMSDK_ROOT)
    set(ZOOMSDK_ROOT "/opt/zoom-meeting-sdk-linux")
endif()

if(NOT EXISTS "${ZOOMSDK_ROOT}/h/zoom_sdk.h")
    message(FATAL_ERROR
        "Zoom Meeting SDK headers not found at ${ZOOMSDK_ROOT}/h/.\n"
        "Set -DZOOMSDK_ROOT=/path/to/zoom-meeting-sdk-linux on cmake invocation.")
endif()

add_executable(meetingscribe-bot main.cpp)

target_include_directories(meetingscribe-bot PRIVATE
    "${ZOOMSDK_ROOT}/h"
    "${ZOOMSDK_ROOT}/h/raw_data"
)

target_link_directories(meetingscribe-bot PRIVATE "${ZOOMSDK_ROOT}")
target_link_libraries(meetingscribe-bot PRIVATE
    meetingsdk
    pthread
    dl
)

# rpath so the binary finds libmeetingsdk.so next to itself at runtime
set_target_properties(meetingscribe-bot PROPERTIES
    INSTALL_RPATH "$ORIGIN"
    BUILD_WITH_INSTALL_RPATH TRUE
)

install(TARGETS meetingscribe-bot RUNTIME DESTINATION bin)
