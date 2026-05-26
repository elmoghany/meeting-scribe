"use strict";
const $ = (id) => document.getElementById(id);
const api = async (url, opts) => {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.headers.get("content-type")?.includes("json") ? r.json() : r.text();
};
const post = (url, body) =>
  api(url, { method: "POST", headers: { "Content-Type": "application/json" },
             body: body ? JSON.stringify(body) : null });

let currentMeeting = null;
let liveMeetingId = null;

// ---------- live websocket ----------
function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => $("conn").classList.add("on");
  ws.onclose = () => { $("conn").classList.remove("on"); setTimeout(connectWS, 1500); };
  ws.onmessage = (e) => handleEvent(JSON.parse(e.data));
}

function handleEvent(ev) {
  switch (ev.type) {
    case "started":
      liveMeetingId = ev.id; $("live").innerHTML = "";
      $("live-flag").textContent = "● recording " + ev.title;
      break;
    case "segment":
      if (ev.meeting_id === liveMeetingId) appendLive(ev);
      break;
    case "stopped":
      $("live-flag").textContent = "processing…"; break;
    case "progress":
      $("rec-status").textContent = ev.message; break;
    case "processed":
      $("rec-status").textContent =
        `Done: ${ev.segments} segments, ${ev.action_items} action items (${ev.backend}).`;
      $("live-flag").textContent = "";
      refreshMeetings();
      if (currentMeeting === ev.meeting_id) openMeeting(ev.meeting_id);
      else openMeeting(ev.meeting_id);
      break;
    case "error":
      $("rec-status").textContent = "Error: " + ev.message; break;
  }
}

function segEl(s) {
  const div = document.createElement("div");
  div.className = "seg" + (s.source === "live" ? " live" : "");
  const mm = String(Math.floor(s.start / 60)).padStart(2, "0");
  const ss = String(Math.floor(s.start % 60)).padStart(2, "0");
  const me = s.speaker === "Me" ? " me" : "";
  div.innerHTML = `<span class="ts">${mm}:${ss}</span>` +
    `<span class="who${me}">${s.speaker}</span>${escapeHtml(s.text)}`;
  return div;
}
function appendLive(s) {
  const live = $("live");
  live.appendChild(segEl(s));
  live.scrollTop = live.scrollHeight;
}
const escapeHtml = (t) => t.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

// ---------- recording ----------
$("btn-start").onclick = async () => {
  try {
    const m = await post("/api/record/start", {
      title: $("title").value || "Untitled meeting",
      platform: $("platform").value,
      capture_mic: $("cap-mic").checked,
      capture_system: $("cap-sys").checked,
    });
    $("btn-start").disabled = true; $("btn-stop").disabled = false;
    $("rec-status").textContent = "Recording " + m.id;
  } catch (e) { $("rec-status").textContent = "Could not start: " + e.message; }
};
$("btn-stop").onclick = async () => {
  $("btn-stop").disabled = true; $("btn-start").disabled = false;
  $("rec-status").textContent = "Stopping & processing…";
  try { await post("/api/record/stop"); } catch (e) { $("rec-status").textContent = e.message; }
};

// ---------- meetings list + detail ----------
async function refreshMeetings() {
  const list = await api("/api/meetings");
  const ul = $("meeting-list"); ul.innerHTML = "";
  for (const m of list) {
    const li = document.createElement("li");
    if (m.id === currentMeeting) li.className = "active";
    const when = m.started_at ? new Date(m.started_at * 1000).toLocaleString() : "";
    li.innerHTML = `<div class="mtitle">${escapeHtml(m.title)}</div>` +
      `<div class="muted">${when} · <span class="badge">${m.platform}</span> ` +
      `<span class="badge">${m.status}</span></div>`;
    li.onclick = () => openMeeting(m.id);
    ul.appendChild(li);
  }
}

async function openMeeting(id) {
  currentMeeting = id;
  refreshMeetings();
  const d = await api("/api/meetings/" + id);
  $("detail-card").classList.remove("hidden");
  $("detail-title").textContent = d.meeting.title;
  const mins = (d.meeting.duration_sec || 0) / 60;
  $("detail-meta").textContent =
    `${d.meeting.platform} · ${d.meeting.status} · ${mins.toFixed(0)} min · ${d.meeting.language || ""}`;

  // summary
  const sm = d.summary; const sd = $("summary"); sd.innerHTML = "";
  if (sm) {
    if (sm.overview) sd.innerHTML += `<p>${escapeHtml(sm.overview)}</p>`;
    if (sm.key_points?.length) {
      sd.innerHTML += "<h3>Key points</h3>" +
        sm.key_points.map((p) => `<div class="kp">• ${escapeHtml(p)}</div>`).join("");
    }
    if (sm.decisions?.length) {
      sd.innerHTML += "<h3>Decisions</h3>" +
        sm.decisions.map((p) => `<div class="kp">• ${escapeHtml(p)}</div>`).join("");
    }
  } else sd.innerHTML = `<p class="muted">No summary yet (still processing?).</p>`;

  // action items
  const al = $("actions"); al.innerHTML = "";
  for (const a of d.action_items) {
    const li = document.createElement("li");
    const cb = document.createElement("input");
    cb.type = "checkbox"; cb.checked = a.done;
    cb.onchange = () => post(`/api/action/${a.id}?done=${cb.checked}`);
    const meta = [a.owner, a.due].filter(Boolean).join(" · ");
    li.appendChild(cb);
    li.insertAdjacentHTML("beforeend",
      `${escapeHtml(a.text)} ${meta ? `<span class="badge">${escapeHtml(meta)}</span>` : ""}`);
    al.appendChild(li);
  }
  if (!d.action_items.length) al.innerHTML = `<li class="muted">None detected.</li>`;

  // transcript
  const t = $("transcript"); t.innerHTML = "";
  for (const s of d.segments) t.appendChild(segEl(s));
  $("chat-answer").innerHTML = "";
}

$("btn-md").onclick = async () => {
  const md = await api(`/api/meetings/${currentMeeting}/markdown`);
  const w = window.open("", "_blank");
  w.document.write("<pre>" + escapeHtml(md) + "</pre>");
};
$("btn-reprocess").onclick = () => post(`/api/meetings/${currentMeeting}/reprocess`);
$("btn-delete").onclick = async () => {
  if (!confirm("Delete this meeting?")) return;
  await api(`/api/meetings/${currentMeeting}`, { method: "DELETE" });
  currentMeeting = null; $("detail-card").classList.add("hidden"); refreshMeetings();
};

// ---------- chat ----------
$("btn-ask").onclick = async () => {
  const q = $("chat-q").value.trim(); if (!q) return;
  $("chat-answer").innerHTML = `<div class="answer muted">Thinking…</div>`;
  try {
    const r = await post(`/api/meetings/${currentMeeting}/chat`, { question: q });
    $("chat-answer").innerHTML = `<div class="answer">${escapeHtml(r.answer)}</div>`;
  } catch (e) { $("chat-answer").innerHTML = `<div class="answer">${e.message}</div>`; }
};
$("chat-q").addEventListener("keydown", (e) => { if (e.key === "Enter") $("btn-ask").click(); });

// ---------- search ----------
let searchTimer = null;
$("search").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  const q = e.target.value.trim();
  searchTimer = setTimeout(async () => {
    const box = $("search-results"); box.innerHTML = "";
    if (!q) return;
    const hits = await api("/api/search?q=" + encodeURIComponent(q));
    for (const h of hits) {
      const div = document.createElement("div");
      div.className = "hit";
      const mm = String(Math.floor(h.start / 60)).padStart(2, "0");
      const ss = String(Math.floor(h.start % 60)).padStart(2, "0");
      div.innerHTML = `<b>${escapeHtml(h.title)}</b> · ${h.speaker} ${mm}:${ss}<br>` +
        escapeHtml(h.snippet).replace(/\[/g, "<mark>").replace(/\]/g, "</mark>");
      div.onclick = () => openMeeting(h.meeting_id);
      box.appendChild(div);
    }
    if (!hits.length) box.innerHTML = `<p class="muted">No matches.</p>`;
  }, 250);
});

connectWS();
refreshMeetings();
