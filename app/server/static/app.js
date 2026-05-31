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
let activeTagFilter = null;

function chip(label, active, onclick) {
  const s = document.createElement("span");
  s.className = "chip" + (active ? " active" : "");
  s.textContent = label;
  if (onclick) s.onclick = onclick;
  return s;
}

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
      $("live-notes").innerHTML = ""; $("live-notes").classList.add("hidden");
      $("live-flag").textContent = "● recording " + ev.title;
      break;
    case "segment":
      if (ev.meeting_id === liveMeetingId) appendLive(ev);
      break;
    case "live_notes":
      if (ev.meeting_id === liveMeetingId) renderLiveNotes(ev);
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
      renderAllActions();
      openMeeting(ev.meeting_id);
      break;
    case "error":
      $("rec-status").textContent = "Error: " + ev.message; break;
  }
}

function segEl(s, withStar) {
  const div = document.createElement("div");
  const hi = highlightedIds.has(s.id) ? " hi" : "";
  const lowc = (typeof s.confidence === "number" && s.confidence < 0.5) ? " low-conf" : "";
  div.className = "seg" + (s.source === "live" ? " live" : "") + hi + lowc;
  if (typeof s.confidence === "number") {
    div.title = "ASR confidence: " + Math.round(s.confidence * 100) + "%";
  }
  div.dataset.start = s.start;
  div.dataset.end = s.end;
  div.dataset.id = s.id;
  const mm = String(Math.floor(s.start / 60)).padStart(2, "0");
  const ss = String(Math.floor(s.start % 60)).padStart(2, "0");
  const me = s.speaker === "Me" ? " me" : "";
  div.innerHTML = `<span class="ts">${mm}:${ss}</span>` +
    `<span class="who${me}">${s.speaker}</span>` +
    `<span class="txt">${escapeHtml(s.text)}</span>`;
  if (withStar && s.id != null) {
    const txt = div.querySelector(".txt");
    txt.title = "Double-click to edit";
    txt.ondblclick = (e) => { e.stopPropagation(); editSegment(s.id, txt); };
    const star = document.createElement("span");
    star.className = "star";
    star.textContent = highlightedIds.has(s.id) ? "★" : "☆";
    star.title = "Highlight this line";
    star.onclick = (e) => { e.stopPropagation(); toggleHighlight(s.id, div, star); };
    div.appendChild(star);
    const clip = document.createElement("span");
    clip.className = "clip"; clip.textContent = "⬇"; clip.title = "Download this clip";
    clip.onclick = (e) => { e.stopPropagation();
      window.open(`/api/meetings/${currentMeeting}/clip?start=${s.start}&end=${s.end}`, "_blank"); };
    div.appendChild(clip);
  }
  div.onclick = () => seekTo(s.start);
  return div;
}

function editSegment(segId, txtEl) {
  const orig = txtEl.textContent;
  txtEl.contentEditable = "true";
  txtEl.classList.add("editing");
  txtEl.focus();
  const finish = async (save) => {
    txtEl.contentEditable = "false";
    txtEl.classList.remove("editing");
    txtEl.onkeydown = txtEl.onblur = null;
    const val = txtEl.textContent.trim();
    if (save && val && val !== orig) {
      try {
        await api(`/api/segments/${segId}`, { method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: val }) });
      } catch { txtEl.textContent = orig; }
    } else { txtEl.textContent = orig; }
  };
  txtEl.onkeydown = (e) => {
    if (e.key === "Enter") { e.preventDefault(); finish(true); }
    else if (e.key === "Escape") { e.preventDefault(); finish(false); }
  };
  txtEl.onblur = () => finish(true);
}

async function toggleHighlight(segId, div, star) {
  const r = await post(`/api/meetings/${currentMeeting}/highlight/${segId}`);
  if (r.highlighted) { highlightedIds.add(segId); star.textContent = "★"; div.classList.add("hi"); }
  else { highlightedIds.delete(segId); star.textContent = "☆"; div.classList.remove("hi"); }
  annotations = await api(`/api/meetings/${currentMeeting}/annotations`);
  renderAnnotations();
}

function renderSummaryView(sm) {
  const sd = $("summary"); sd.innerHTML = "";
  const head = document.createElement("div"); head.className = "row between";
  head.innerHTML = "<span></span>";
  const edit = document.createElement("span");
  edit.className = "ai-toggle"; edit.textContent = "✎ edit"; edit.title = "Edit notes";
  edit.onclick = () => renderSummaryEdit(sm);
  head.appendChild(edit); sd.appendChild(head);
  const body = document.createElement("div");
  let html = "";
  if (sm.overview) html += `<p>${escapeHtml(sm.overview)}</p>`;
  if (sm.key_points && sm.key_points.length) html += "<h3>Key points</h3>" +
    sm.key_points.map((p) => `<div class="kp">• ${escapeHtml(p)}</div>`).join("");
  if (sm.decisions && sm.decisions.length) html += "<h3>Decisions</h3>" +
    sm.decisions.map((p) => `<div class="kp">• ${escapeHtml(p)}</div>`).join("");
  body.innerHTML = html || `<p class="muted">No summary yet.</p>`;
  sd.appendChild(body);
}

function renderSummaryEdit(sm) {
  const sd = $("summary"); sd.innerHTML = "";
  const mk = (label, value) => {
    const wrap = document.createElement("div");
    wrap.innerHTML = `<div class="ln-sub">${label}</div>`;
    const ta = document.createElement("textarea");
    ta.rows = label === "Overview" ? 2 : 3; ta.value = value;
    wrap.appendChild(ta); sd.appendChild(wrap); return ta;
  };
  const ov = mk("Overview", sm.overview || "");
  const kp = mk("Key points (one per line)", (sm.key_points || []).join("\n"));
  const dc = mk("Decisions (one per line)", (sm.decisions || []).join("\n"));
  const row = document.createElement("div"); row.className = "row";
  const save = document.createElement("button"); save.className = "primary"; save.textContent = "Save notes";
  const cancel = document.createElement("button"); cancel.className = "ghost"; cancel.textContent = "Cancel";
  cancel.onclick = () => renderSummaryView(sm);
  save.onclick = async () => {
    const body = { overview: ov.value,
      key_points: kp.value.split("\n").map((x) => x.trim()).filter(Boolean),
      decisions: dc.value.split("\n").map((x) => x.trim()).filter(Boolean) };
    const updated = await api(`/api/meetings/${currentMeeting}/summary`,
      { method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body) });
    renderSummaryView(updated);
  };
  row.appendChild(save); row.appendChild(cancel); sd.appendChild(row);
  ov.focus();
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

$("t-search").addEventListener("input", () => {
  const q = $("t-search").value.trim().toLowerCase();
  let first = null, n = 0;
  $("transcript").querySelectorAll(".seg").forEach((el) => {
    el.classList.remove("match");
    if (q && el.textContent.toLowerCase().includes(q)) {
      el.classList.add("match"); n++; if (!first) first = el;
    }
  });
  $("t-search-n").textContent = q ? `${n} match${n === 1 ? "" : "es"}` : "";
  if (first) first.scrollIntoView({ block: "center" });
});

async function saveSpeakerNames() {
  const mapping = {};
  $("speakers").querySelectorAll(".spk-in").forEach((i) => {
    const o = i.dataset.old, n = i.value.trim();
    if (n && n !== o) mapping[o] = n;
  });
  if (!Object.keys(mapping).length) return;
  await post(`/api/meetings/${currentMeeting}/rename-speakers`, { mapping });
  openMeeting(currentMeeting);
  renderProfiles();  // a rename may enroll a voice profile
}

async function renderProfiles() {
  const box = $("profiles");
  const ps = await api("/api/speakers").catch(() => []);
  box.innerHTML = "";
  if (!ps.length) {
    box.innerHTML = `<p class="muted">None yet — rename a speaker to remember their voice across meetings.</p>`;
    return;
  }
  for (const p of ps) {
    const row = document.createElement("div"); row.className = "ai-row";
    const name = document.createElement("span");
    name.className = "ai-txt prof-name"; name.textContent = p.name;
    name.title = "Click to see meetings with this voice";
    const meta = document.createElement("span"); meta.className = "ai-meta"; meta.textContent = `${p.n_samples}×`;
    const del = document.createElement("span"); del.className = "annot-del"; del.textContent = "✕";
    del.title = "Forget this voice"; del.style.marginLeft = "6px";
    del.onclick = async (e) => {
      e.stopPropagation();
      await api("/api/speakers/" + encodeURIComponent(p.name), { method: "DELETE" });
      renderProfiles();
    };
    const sub = document.createElement("div"); sub.className = "prof-sub hidden";
    row.appendChild(name); row.appendChild(meta); row.appendChild(del);
    box.appendChild(row); box.appendChild(sub);

    name.onclick = async () => {
      if (!sub.classList.contains("hidden")) { sub.classList.add("hidden"); return; }
      sub.classList.remove("hidden");
      if (!sub.dataset.loaded) {
        const ms = await api(`/api/speakers/${encodeURIComponent(p.name)}/meetings`).catch(() => []);
        sub.innerHTML = ms.length
          ? ms.map((m) => `<div class="prof-mtg" data-id="${m.meeting_id}">${escapeHtml(m.title)} <span class="muted">${new Date(m.started_at * 1000).toLocaleDateString()}</span></div>`).join("")
          : '<div class="muted prof-mtg">(no meetings yet)</div>';
        sub.querySelectorAll(".prof-mtg[data-id]").forEach((el) => {
          el.onclick = () => openMeeting(el.dataset.id);
        });
        sub.dataset.loaded = "1";
      }
    };
  }
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

function renderLiveNotes(ev) {
  const box = $("live-notes");
  box.classList.remove("hidden");
  let html = "<div class='ln-title'>📝 Live notes (updating…)</div>";
  if (ev.overview) html += `<div class="ln-ov">${escapeHtml(ev.overview)}</div>`;
  if (ev.action_items && ev.action_items.length) {
    html += "<div class='ln-sub'>Action items so far</div><ul class='ln-ul'>";
    ev.action_items.forEach((a) => {
      html += `<li>${escapeHtml(a.text)}` +
        (a.owner ? ` <span class="badge">${escapeHtml(a.owner)}</span>` : "") + "</li>";
    });
    html += "</ul>";
  }
  box.innerHTML = html;
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
      language: $("language").value || null,
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
  // tag filter bar
  const tags = await api("/api/tags").catch(() => []);
  const tf = $("tag-filter"); tf.innerHTML = "";
  if (tags.length) {
    tf.appendChild(chip("all", !activeTagFilter, () => { activeTagFilter = null; refreshMeetings(); }));
    tags.forEach((t) => tf.appendChild(
      chip(`${t.tag} ${t.count}`, activeTagFilter === t.tag,
           () => { activeTagFilter = activeTagFilter === t.tag ? null : t.tag; refreshMeetings(); })));
  }
  const q = activeTagFilter ? "?tag=" + encodeURIComponent(activeTagFilter) : "";
  const list = await api("/api/meetings" + q);
  const ul = $("meeting-list"); ul.innerHTML = "";
  for (const m of list) {
    const li = document.createElement("li");
    if (m.id === currentMeeting) li.className = "active";
    const when = m.started_at ? new Date(m.started_at * 1000).toLocaleString() : "";
    const tagHtml = (m.tags || []).map((t) => `<span class="badge tagb">${escapeHtml(t)}</span>`).join(" ");
    const mins = m.duration_sec ? `${Math.round(m.duration_sec / 60)} min` : "";
    const words = m.stats && m.stats.words ? `${m.stats.words} words` : "";
    const stat = [mins, words].filter(Boolean).join(" · ");
    li.innerHTML = `<div class="mtitle">${escapeHtml(m.title)}</div>` +
      `<div class="muted">${when} · <span class="badge">${m.platform}</span> ` +
      `<span class="badge">${m.status}</span> ${tagHtml}</div>` +
      (stat ? `<div class="muted mstat">${stat}</div>` : "");
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
  await loadTemplates();
  if (d.meeting.template) $("tmpl-sel").value = d.meeting.template;

  // tags
  const tg = $("tags"); tg.innerHTML = "";
  (d.tags || []).forEach((t) => {
    const c = chip(t, false);
    c.classList.add("removable");
    const x = document.createElement("span");
    x.className = "chip-x"; x.textContent = "×";
    x.onclick = async (e) => {
      e.stopPropagation();
      await api(`/api/meetings/${id}/tags/${encodeURIComponent(t)}`, { method: "DELETE" });
      openMeeting(id); refreshMeetings();
    };
    c.appendChild(x); tg.appendChild(c);
  });
  const addTag = document.createElement("input");
  addTag.className = "tag-add"; addTag.placeholder = "+ tag";
  addTag.addEventListener("keydown", async (e) => {
    if (e.key === "Enter" && addTag.value.trim()) {
      await post(`/api/meetings/${id}/tags`, { text: addTag.value.trim() });
      openMeeting(id); refreshMeetings();
    }
  });
  tg.appendChild(addTag);

  // summary (with an inline edit toggle)
  const sm = d.summary || { overview: "", key_points: [], decisions: [] };
  renderSummaryView(sm);

  // topics (auto keywords) + sentiment chip
  const tp = $("topics"); tp.innerHTML = "";
  if (d.sentiment && (d.sentiment.positive || d.sentiment.negative)) {
    const s = d.sentiment;
    const emoji = s.label === "positive" ? "🙂" : s.label === "negative" ? "🙁" : "😐";
    const c = chip(`${emoji} ${s.label} (${s.score > 0 ? "+" : ""}${s.score})`, false);
    c.classList.add("sentiment-" + s.label);
    c.title = `${s.positive} positive · ${s.negative} negative cue words`;
    tp.appendChild(c);
  }
  (d.topics || []).forEach((t) => { const c = chip(t, false); c.classList.add("topic"); tp.appendChild(c); });

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
    cb.onchange = async () => { await post(`/api/action/${a.id}?done=${cb.checked}`); renderAllActions(); };
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
  $("t-search").value = ""; $("t-search-n").textContent = "";
  $("chat-answer").innerHTML = "";
}

$("btn-copy").onclick = async () => {
  const b = $("btn-copy"); const orig = b.textContent;
  try {
    const md = await api(`/api/meetings/${currentMeeting}/export?fmt=md`);
    await navigator.clipboard.writeText(md);
    b.textContent = "Copied!";
  } catch { b.textContent = "Copy failed"; }
  setTimeout(() => { b.textContent = orig; }, 1300);
};
$("export-fmt").onchange = (e) => {
  const fmt = e.target.value;
  if (!fmt || !currentMeeting) return;
  window.open(`/api/meetings/${currentMeeting}/export?fmt=${fmt}`, "_blank");
  e.target.value = "";
};
$("detail-title").title = "Double-click to rename";
$("detail-title").ondblclick = async () => {
  const nt = prompt("Meeting title:", $("detail-title").textContent);
  if (nt && nt.trim()) {
    await post(`/api/meetings/${currentMeeting}/title`, { title: nt.trim() });
    openMeeting(currentMeeting); refreshMeetings();
  }
};
$("btn-free-audio").onclick = async () => {
  if (!confirm("Delete the audio recordings for this meeting? Notes are kept.")) return;
  const r = await post(`/api/meetings/${currentMeeting}/delete-audio`);
  alert(`Freed ${r.freed_mb} MB (${r.removed.length} file(s)).`);
};
async function loadTemplates() {
  if ($("tmpl-sel").options.length) return;  // once
  const t = await api("/api/templates").catch(() => ({ templates: ["general"] }));
  $("tmpl-sel").innerHTML = t.templates
    .map((x) => `<option value="${x}">${x.replace(/_/g, " ")}</option>`).join("");
}
$("btn-regen").onclick = async () => {
  const b = $("btn-regen"); b.textContent = "Regenerating…"; b.disabled = true;
  const tmpl = $("tmpl-sel").value || "general";
  try {
    await post(`/api/meetings/${currentMeeting}/regenerate-notes?template=${encodeURIComponent(tmpl)}`);
    await openMeeting(currentMeeting); renderAllActions();
  } finally { b.textContent = "Regen notes"; b.disabled = false; }
};
$("btn-reel").onclick = () => {
  if (!highlightedIds.size) { alert("Star some transcript lines first to build a reel."); return; }
  window.open(`/api/meetings/${currentMeeting}/highlight-reel`, "_blank");
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

// ---------- ask all meetings (cross-meeting Q&A) ----------
$("btn-ask-all").onclick = async () => {
  const q = $("ask-q").value.trim();
  if (!q) return;
  $("ask-answer").innerHTML = `<div class="answer muted">Searching all meetings…</div>`;
  try {
    const r = await post("/api/ask", { question: q });
    let html = `<div class="answer">${escapeHtml(r.answer)}</div>`;
    if (r.sources && r.sources.length) {
      html += `<div class="ask-src">sources:</div>`;
      for (const s of r.sources) {
        const mm = String(Math.floor(s.start / 60)).padStart(2, "0");
        const ss = String(Math.floor(s.start % 60)).padStart(2, "0");
        html += `<div class="hit" data-id="${s.meeting_id}"><b>${escapeHtml(s.title)}</b> ` +
          `· ${escapeHtml(s.speaker)} ${mm}:${ss}</div>`;
      }
    }
    $("ask-answer").innerHTML = html;
    $("ask-answer").querySelectorAll(".hit[data-id]").forEach((el) =>
      { el.onclick = () => openMeeting(el.dataset.id); });
  } catch (e) { $("ask-answer").innerHTML = `<div class="answer">${e.message}</div>`; }
};
$("ask-q").addEventListener("keydown", (e) => { if (e.key === "Enter") $("btn-ask-all").click(); });

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

let aiOpenOnly = true;
async function renderAllActions() {
  const box = $("all-actions");
  const items = await api("/api/action-items?open_only=" + aiOpenOnly).catch(() => []);
  box.innerHTML = "";
  if (!items.length) {
    box.innerHTML = `<p class="muted">No ${aiOpenOnly ? "open " : ""}action items.</p>`;
    return;
  }
  for (const a of items) {
    const row = document.createElement("div");
    row.className = "ai-row";
    const cb = document.createElement("input");
    cb.type = "checkbox"; cb.checked = !!a.done;
    cb.onchange = async () => { await post(`/api/action/${a.id}?done=${cb.checked}`); renderAllActions(); };
    const txt = document.createElement("span");
    txt.className = "ai-txt"; txt.textContent = a.text;
    if (a.done) txt.style.textDecoration = "line-through";
    const meta = document.createElement("span");
    meta.className = "ai-meta";
    meta.textContent = a.meeting_title + (a.owner ? " · " + a.owner : "") + (a.due ? " · " + a.due : "");
    meta.title = "Open meeting";
    meta.onclick = () => openMeeting(a.meeting_id);
    row.appendChild(cb); row.appendChild(txt); row.appendChild(meta);
    box.appendChild(row);
  }
}
$("ai-toggle").onclick = () => {
  aiOpenOnly = !aiOpenOnly;
  $("ai-toggle").textContent = aiOpenOnly ? "(open only)" : "(all)";
  renderAllActions();
};
$("ai-csv").onclick = () => window.open("/api/action-items.csv?open_only=" + aiOpenOnly, "_blank");

// theme toggle (persisted)
function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem("ms-theme", t); } catch {}
}
$("theme-btn").onclick = () =>
  applyTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light");
applyTheme((() => { try { return localStorage.getItem("ms-theme"); } catch { return null; } })() || "dark");

// keyboard shortcuts: "/" focus search · space play/pause · r start/stop
document.addEventListener("keydown", (e) => {
  const el = document.activeElement;
  if (el && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)) return;
  if (e.key === "/") { e.preventDefault(); $("search").focus(); }
  else if (e.key === " " && currentMeeting && $("player").getAttribute("src")) {
    e.preventDefault();
    const p = $("player"); p.paused ? p.play() : p.pause();
  } else if (e.key.toLowerCase() === "r") {
    if (!$("btn-start").disabled) $("btn-start").click();
    else if (!$("btn-stop").disabled) $("btn-stop").click();
  }
});

// ---------- custom vocabulary ----------
async function loadVocab() {
  const v = await api("/api/vocab").catch(() => ({ terms: [] }));
  $("vocab").value = (v.terms || []).join("\n");
}
$("vocab-save").onclick = async () => {
  const el = $("vocab-save"); el.textContent = "· saving…";
  try {
    await api("/api/vocab", { method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: $("vocab").value }) });
    el.textContent = "· saved";
  } catch { el.textContent = "· error"; }
  setTimeout(() => { el.textContent = "· save"; }, 1200);
};

$("player").addEventListener("timeupdate", highlightPlaying);
loadVocab();
loadTemplates();
connectWS();
refreshMeetings();
renderAllActions();
renderProfiles();
