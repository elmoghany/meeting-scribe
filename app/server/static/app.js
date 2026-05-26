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
let highlightedIds = new Set();
let annotations = [];

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

function segEl(s, withStar) {
  const div = document.createElement("div");
  const hi = highlightedIds.has(s.id) ? " hi" : "";
  div.className = "seg" + (s.source === "live" ? " live" : "") + hi;
  div.dataset.start = s.start;
  div.dataset.end = s.end;
  div.dataset.id = s.id;
  const mm = String(Math.floor(s.start / 60)).padStart(2, "0");
  const ss = String(Math.floor(s.start % 60)).padStart(2, "0");
  const me = s.speaker === "Me" ? " me" : "";
  div.innerHTML = `<span class="ts">${mm}:${ss}</span>` +
    `<span class="who${me}">${s.speaker}</span>${escapeHtml(s.text)}`;
  if (withStar && s.id != null) {
    const star = document.createElement("span");
    star.className = "star";
    star.textContent = highlightedIds.has(s.id) ? "★" : "☆";
    star.title = "Highlight this line";
    star.onclick = (e) => { e.stopPropagation(); toggleHighlight(s.id, div, star); };
    div.appendChild(star);
  }
  div.onclick = () => seekTo(s.start);
  return div;
}

async function toggleHighlight(segId, div, star) {
  const r = await post(`/api/meetings/${currentMeeting}/highlight/${segId}`);
  if (r.highlighted) { highlightedIds.add(segId); star.textContent = "★"; div.classList.add("hi"); }
  else { highlightedIds.delete(segId); star.textContent = "☆"; div.classList.remove("hi"); }
  annotations = await api(`/api/meetings/${currentMeeting}/annotations`);
  renderAnnotations();
}

function renderAnnotations() {
  const box = $("annotations"); box.innerHTML = "";
  const segById = {};
  $("transcript").querySelectorAll(".seg").forEach((el) => { segById[el.dataset.id] = el; });
  const items = annotations.filter((a) => a.kind === "comment" || a.kind === "highlight");
  if (!items.length) { box.innerHTML = `<p class="muted">No notes yet. Star a line or add a comment.</p>`; return; }
  for (const a of items) {
    const row = document.createElement("div");
    row.className = "annot";
    const tag = a.kind === "highlight" ? "★" : "💬";
    let ctx = "";
    if (a.segment_id != null && segById[a.segment_id]) {
      const el = segById[a.segment_id];
      ctx = ` <span class="muted">— ${el.querySelector(".ts").textContent} ${escapeHtml(el.querySelector(".who").textContent)}</span>`;
    }
    row.innerHTML = `<span>${tag} ${escapeHtml(a.text || (a.kind === "highlight" ? "(highlight)" : ""))}${ctx}</span>`;
    const del = document.createElement("span");
    del.className = "annot-del"; del.textContent = "✕"; del.title = "Delete";
    del.onclick = async () => { await api(`/api/annotations/${a.id}`, { method: "DELETE" });
      annotations = await api(`/api/meetings/${currentMeeting}/annotations`);
      if (a.kind === "highlight") { highlightedIds.delete(a.segment_id);
        const el = segById[a.segment_id]; if (el) { el.classList.remove("hi"); const st = el.querySelector(".star"); if (st) st.textContent = "☆"; } }
      renderAnnotations(); };
    row.appendChild(del);
    box.appendChild(row);
  }
}

async function saveSpeakerNames() {
  const mapping = {};
  $("speakers").querySelectorAll(".spk-in").forEach((i) => {
    const o = i.dataset.old, n = i.value.trim();
    if (n && n !== o) mapping[o] = n;
  });
  if (!Object.keys(mapping).length) return;
  await post(`/api/meetings/${currentMeeting}/rename-speakers`, { mapping });
  openMeeting(currentMeeting);
}

$("btn-comment").onclick = async () => {
  const t = $("comment-input").value.trim();
  if (!t || !currentMeeting) return;
  await post(`/api/meetings/${currentMeeting}/comment`, { text: t });
  $("comment-input").value = "";
  annotations = await api(`/api/meetings/${currentMeeting}/annotations`);
  renderAnnotations();
};
$("comment-input").addEventListener("keydown", (e) => { if (e.key === "Enter") $("btn-comment").click(); });

function seekTo(t) {
  const p = $("player");
  if (!p.getAttribute("src")) return;
  p.currentTime = t;
  p.play().catch(() => {});
}

// highlight the transcript line currently playing
let _lastActive = null;
function highlightPlaying() {
  const t = $("player").currentTime;
  const segs = $("transcript").querySelectorAll(".seg");
  let active = null;
  for (const el of segs) {
    if (t >= parseFloat(el.dataset.start) && t < parseFloat(el.dataset.end)) { active = el; break; }
  }
  if (active === _lastActive) return;
  if (_lastActive) _lastActive.classList.remove("playing");
  if (active) { active.classList.add("playing"); active.scrollIntoView({ block: "nearest" }); }
  _lastActive = active;
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
  const player = $("player");
  player.src = "/api/meetings/" + id + "/audio";
  _lastActive = null;
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

  // speakers (rename inline — reuses /rename-speakers)
  const sp = $("speakers"); sp.innerHTML = "";
  const names = [...new Set(d.segments.map((s) => s.speaker))];
  if (names.length) {
    const wrap = document.createElement("div"); wrap.className = "spk-wrap";
    names.forEach((n) => {
      const inp = document.createElement("input");
      inp.className = "spk-in"; inp.value = n; inp.dataset.old = n;
      inp.addEventListener("keydown", (e) => { if (e.key === "Enter") saveSpeakerNames(); });
      wrap.appendChild(inp);
    });
    sp.appendChild(wrap);
    const btn = document.createElement("button");
    btn.className = "ghost"; btn.textContent = "Save names"; btn.onclick = saveSpeakerNames;
    sp.appendChild(btn);
  } else sp.innerHTML = `<p class="muted">No speakers yet.</p>`;

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

  // analytics (talk time)
  const an = $("analytics"); an.innerHTML = "";
  try {
    const a = await api(`/api/meetings/${id}/analytics`);
    if (a.speakers && a.speakers.length) {
      for (const s of a.speakers) {
        const row = document.createElement("div");
        row.className = "tt-row";
        row.innerHTML =
          `<span class="tt-name">${escapeHtml(s.speaker)}</span>` +
          `<span class="tt-bar"><span style="width:${s.time_pct}%"></span></span>` +
          `<span class="tt-val">${s.time_pct}% · ${Math.round(s.seconds)}s · ${s.words}w</span>`;
        an.appendChild(row);
      }
    } else an.innerHTML = `<p class="muted">No data.</p>`;
  } catch { an.innerHTML = ""; }

  // annotations (highlights + comments) — fetch before transcript so stars render
  annotations = await api(`/api/meetings/${id}/annotations`).catch(() => []);
  highlightedIds = new Set(annotations.filter((a) => a.kind === "highlight").map((a) => a.segment_id));

  // transcript (with highlight stars)
  const t = $("transcript"); t.innerHTML = "";
  for (const s of d.segments) t.appendChild(segEl(s, true));
  renderAnnotations();
  $("chat-answer").innerHTML = "";
}

$("export-fmt").onchange = (e) => {
  const fmt = e.target.value;
  if (!fmt || !currentMeeting) return;
  window.open(`/api/meetings/${currentMeeting}/export?fmt=${fmt}`, "_blank");
  e.target.value = "";
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

$("player").addEventListener("timeupdate", highlightPlaying);
connectWS();
refreshMeetings();
