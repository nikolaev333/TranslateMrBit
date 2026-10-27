(() => {
  const jobId = window.__STUDIO_JOB_ID__;
  if (!jobId) return;

  const audio = document.getElementById("studio-audio");
  const btnPlay = document.getElementById("btn-play");
  const btnBack5 = document.getElementById("btn-back5");
  const btnFwd5 = document.getElementById("btn-fwd5");
  const rateSelect = document.getElementById("playback-rate");
  const seekBar = document.getElementById("seek-bar");
  const timeCurrent = document.getElementById("time-current");
  const timeTotal = document.getElementById("time-total");
  const playerError = document.getElementById("player-error");
  const segmentsList = document.getElementById("segments-list");
  const statusMsg = document.getElementById("segments-status-msg");
  const saveIndicator = document.getElementById("save-indicator");
  const subtitle = document.getElementById("studio-subtitle");
  const autoScrollEl = document.getElementById("auto-scroll");
  const searchEl = document.getElementById("studio-search");
  const btnReplace = document.getElementById("btn-replace");
  const btnUndo = document.getElementById("btn-undo");
  const btnRedo = document.getElementById("btn-redo");
  const btnRestore = document.getElementById("btn-restore");
  const btnSplit = document.getElementById("btn-split");
  const btnMerge = document.getElementById("btn-merge");
  const revisionsList = document.getElementById("revisions-list");
  const btnRefreshRev = document.getElementById("btn-refresh-revisions");
  const exportSource = document.getElementById("export-source");
  const vocabBanner = document.getElementById("vocab-banner");

  let segments = [];
  let revision = 0;
  let activeId = null;
  let selectedId = null;
  let dirty = new Map(); // id -> edited_text
  let editingId = null;
  let seekDragging = false;
  let saveTimer = null;
  let undoStack = [];
  let redoStack = [];
  let autoScroll = true;
  let lastRenderedActive = null;

  async function fetchJson(url, options) {
    const res = await fetch(url, {
      credentials: "same-origin",
      ...options,
    });
    let data = null;
    try {
      data = await res.json();
    } catch (_) {
      data = null;
    }
    if (res.status === 401) {
      window.location.href = "/login";
      throw new Error("Authentication required");
    }
    if (!res.ok) {
      const detail = data && data.detail ? data.detail : `Request failed (${res.status})`;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data;
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function formatTime(sec) {
    if (!Number.isFinite(sec) || sec < 0) sec = 0;
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m}:${String(s).padStart(2, "0")}`;
  }

  function setSaveState(state, label) {
    saveIndicator.dataset.state = state;
    saveIndicator.textContent = label || state;
  }

  function markDirty() {
    window.__studioDirty = dirty.size > 0;
  }

  function pushUndo(snapshot) {
    undoStack.push(snapshot);
    if (undoStack.length > 100) undoStack.shift();
    redoStack = [];
  }

  function currentText(seg) {
    if (dirty.has(seg.id)) return dirty.get(seg.id);
    return seg.edited_text;
  }

  function findSegmentIndexAt(t) {
    if (!segments.length) return -1;
    let lo = 0;
    let hi = segments.length - 1;
    let ans = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      const s = segments[mid];
      if (t < s.start) {
        hi = mid - 1;
      } else if (t >= s.end && mid < segments.length - 1) {
        lo = mid + 1;
      } else {
        ans = mid;
        break;
      }
    }
    if (ans < 0) {
      // Clamp to nearest
      if (t < segments[0].start) return 0;
      return segments.length - 1;
    }
    return ans;
  }

  function updateActiveFromTime(t) {
    const idx = findSegmentIndexAt(t);
    if (idx < 0) return;
    const seg = segments[idx];
    if (!seg || seg.id === lastRenderedActive) return;
    lastRenderedActive = seg.id;
    activeId = seg.id;
    const prev = segmentsList.querySelector(".segment-row.active");
    if (prev) prev.classList.remove("active");
    const row = segmentsList.querySelector(`[data-id="${CSS.escape(seg.id)}"]`);
    if (row) {
      row.classList.add("active");
      if (autoScroll && !editingId) {
        row.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
    }
  }

  function renderSegments() {
    const q = (searchEl.value || "").trim().toLowerCase();
    if (!segments.length) {
      segmentsList.innerHTML = `<p class="muted">No timestamped segments.</p>`;
      return;
    }
    const html = segments
      .map((seg) => {
        const text = currentText(seg);
        if (q && !text.toLowerCase().includes(q) && !(seg.original_text || "").toLowerCase().includes(q)) {
          return "";
        }
        const edited = seg.is_edited || dirty.has(seg.id);
        const selected = seg.id === selectedId ? " selected" : "";
        const active = seg.id === activeId ? " active" : "";
        return `<div class="segment-row${selected}${active}" data-id="${escapeHtml(seg.id)}" role="listitem">
          <button type="button" class="segment-time mono-cell" data-seek="${seg.start}" title="Jump to ${formatTime(seg.start)}">
            ${formatTime(seg.start)}–${formatTime(seg.end)}
          </button>
          <div class="segment-body">
            <textarea class="segment-text" data-id="${escapeHtml(seg.id)}" rows="2" spellcheck="true">${escapeHtml(text)}</textarea>
            <div class="segment-meta">
              ${edited ? '<span class="badge muted-badge">edited</span>' : ""}
              ${seg.edit_source === "vocabulary" ? '<span class="badge muted-badge">vocabulary</span>' : ""}
            </div>
          </div>
        </div>`;
      })
      .join("");
    segmentsList.innerHTML = html || `<p class="muted">No matching segments.</p>`;
  }

  function updateExportLinks() {
    const src = exportSource.value || "edited";
    const base = `/api/jobs/${jobId}/export?source=${encodeURIComponent(src)}&format=`;
    document.getElementById("export-txt").href = `${base}txt`;
    document.getElementById("export-srt").href = `${base}srt`;
    document.getElementById("export-vtt").href = `${base}vtt`;
    document.getElementById("export-json").href = `${base}json`;
  }

  async function loadSegments() {
    const data = await fetchJson(`/api/jobs/${jobId}/segments`);
    revision = data.transcript_revision || 0;
    segments = (data.segments || []).map((s) => ({
      id: s.id,
      start: s.start,
      end: s.end,
      position: s.position,
      original_text: s.original_text,
      edited_text: s.edited_text,
      is_edited: s.is_edited,
      edit_source: s.edit_source,
    }));
    dirty.clear();
    markDirty();
    setSaveState("idle", "Saved");

    if (data.segments_status !== "ready") {
      statusMsg.hidden = false;
      statusMsg.textContent =
        data.segments_status === "unsupported"
          ? "This job has no usable Whisper timestamps. Studio seeking/editing by segment is unavailable (legacy TXT-only)."
          : `Segments status: ${data.segments_status}`;
    } else {
      statusMsg.hidden = true;
    }

    if (data.audio_available) {
      audio.src = `/api/jobs/${jobId}/audio`;
    } else {
      playerError.hidden = false;
      playerError.textContent = "Uploaded audio is no longer available for playback (retention cleanup).";
    }

    if (data.duration_seconds) {
      seekBar.max = data.duration_seconds;
      timeTotal.textContent = formatTime(data.duration_seconds);
    }

    subtitle.textContent = `Job ${jobId.slice(0, 8)}… · rev ${revision}`;
    renderSegments();
    updateExportLinks();
  }

  async function loadRevisions() {
    try {
      const data = await fetchJson(`/api/jobs/${jobId}/revisions?limit=50`);
      const rows = data.revisions || [];
      if (!rows.length) {
        revisionsList.innerHTML = `<li class="muted">No revisions yet.</li>`;
        return;
      }
      revisionsList.innerHTML = rows
        .map(
          (r) => `<li>
            <div class="rev-head"><strong>#${r.revision_no}</strong> · ${escapeHtml(r.action)}</div>
            <div class="muted small">${escapeHtml(r.created_at)}</div>
            <div class="rev-diff mono-cell">${escapeHtml((r.new_text || "").slice(0, 120))}</div>
          </li>`
        )
        .join("");
    } catch (_) {
      revisionsList.innerHTML = `<li class="muted">Could not load revisions.</li>`;
    }
  }

  async function softLoadVocabulary() {
    try {
      const res = await fetch(`/api/jobs/${jobId}/analyze`, { credentials: "same-origin" });
      if (!res.ok) {
        vocabBanner.hidden = true;
        return;
      }
      const data = await res.json();
      const matches = data.matches || [];
      if (!matches.length) {
        vocabBanner.hidden = true;
        return;
      }
      vocabBanner.hidden = false;
      vocabBanner.innerHTML = `<strong>Smart Vocabulary:</strong> ${matches.length} suggestion(s).
        <button type="button" class="btn btn-ghost" id="btn-apply-vocab">Apply all to editor</button>
        <button type="button" class="btn btn-ghost" id="btn-dismiss-vocab">Dismiss</button>`;
      document.getElementById("btn-dismiss-vocab").onclick = () => {
        vocabBanner.hidden = true;
      };
      document.getElementById("btn-apply-vocab").onclick = () => {
        applyVocabularySuggestions(data);
      };
    } catch (_) {
      vocabBanner.hidden = true;
    }
  }

  function applyVocabularySuggestions(analysis) {
    const suggested = analysis.suggested_text || "";
    if (!suggested || !segments.length) return;
    // Map full-text suggestion onto segments by replacing matching original spans in edited text
    pushUndo(segments.map((s) => ({ id: s.id, text: currentText(s) })));
    const matches = analysis.matches || [];
    segments.forEach((seg) => {
      let text = currentText(seg);
      let changed = false;
      matches.forEach((m) => {
        if (text.includes(m.matched_text)) {
          text = text.split(m.matched_text).join(m.correct);
          changed = true;
        }
      });
      if (changed) {
        dirty.set(seg.id, text);
        seg.edit_source = "vocabulary";
      }
    });
    markDirty();
    renderSegments();
    scheduleSave();
    vocabBanner.hidden = true;
  }

  async function flushSave() {
    if (!dirty.size) {
      setSaveState("idle", "Saved");
      return;
    }
    setSaveState("saving", "Saving…");
    const changes = [...dirty.entries()].map(([id, edited_text]) => {
      const seg = segments.find((s) => s.id === id);
      return {
        id,
        edited_text,
        edit_source: (seg && seg.edit_source) || "manual",
      };
    });
    try {
      const data = await fetchJson(`/api/jobs/${jobId}/segments/save`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expected_revision: revision, changes }),
      });
      revision = data.transcript_revision;
      segments = (data.segments || []).map((s) => ({
        id: s.id,
        start: s.start,
        end: s.end,
        position: s.position,
        original_text: s.original_text,
        edited_text: s.edited_text,
        is_edited: s.is_edited,
        edit_source: s.edit_source,
      }));
      dirty.clear();
      markDirty();
      setSaveState("idle", "Saved");
      subtitle.textContent = `Job ${jobId.slice(0, 8)}… · rev ${revision}`;
      loadRevisions();
    } catch (err) {
      setSaveState("error", err.message || "Save failed");
    }
  }

  function scheduleSave() {
    setSaveState("saving", "Unsaved…");
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      flushSave();
    }, 800);
  }

  // Player events
  btnPlay.addEventListener("click", () => {
    if (audio.paused) audio.play().catch((e) => {
      playerError.hidden = false;
      playerError.textContent = e.message || "Playback failed";
    });
    else audio.pause();
  });
  audio.addEventListener("play", () => {
    btnPlay.textContent = "Pause";
  });
  audio.addEventListener("pause", () => {
    btnPlay.textContent = "Play";
  });
  audio.addEventListener("loadedmetadata", () => {
    seekBar.max = audio.duration || seekBar.max;
    timeTotal.textContent = formatTime(audio.duration || 0);
  });
  audio.addEventListener("timeupdate", () => {
    if (!seekDragging) {
      seekBar.value = audio.currentTime;
      timeCurrent.textContent = formatTime(audio.currentTime);
    }
    updateActiveFromTime(audio.currentTime);
  });
  audio.addEventListener("error", () => {
    playerError.hidden = false;
    playerError.textContent = "Could not load audio stream.";
  });

  btnBack5.addEventListener("click", () => {
    audio.currentTime = Math.max(0, audio.currentTime - 5);
  });
  btnFwd5.addEventListener("click", () => {
    audio.currentTime = Math.min(audio.duration || audio.currentTime + 5, audio.currentTime + 5);
  });
  rateSelect.addEventListener("change", () => {
    audio.playbackRate = Number(rateSelect.value) || 1;
  });
  seekBar.addEventListener("pointerdown", () => {
    seekDragging = true;
  });
  seekBar.addEventListener("pointerup", () => {
    seekDragging = false;
    audio.currentTime = Number(seekBar.value);
  });
  seekBar.addEventListener("input", () => {
    timeCurrent.textContent = formatTime(Number(seekBar.value));
  });

  autoScrollEl.addEventListener("change", () => {
    autoScroll = autoScrollEl.checked;
  });

  segmentsList.addEventListener("click", (e) => {
    const seekBtn = e.target.closest("[data-seek]");
    if (seekBtn) {
      const t = Number(seekBtn.getAttribute("data-seek"));
      audio.currentTime = t;
      const row = seekBtn.closest(".segment-row");
      if (row) {
        selectedId = row.getAttribute("data-id");
        renderSegments();
      }
      return;
    }
    const row = e.target.closest(".segment-row");
    if (row && !e.target.closest("textarea")) {
      selectedId = row.getAttribute("data-id");
      const seg = segments.find((s) => s.id === selectedId);
      if (seg) audio.currentTime = seg.start;
      const prev = segmentsList.querySelector(".segment-row.selected");
      if (prev) prev.classList.remove("selected");
      row.classList.add("selected");
    }
  });

  segmentsList.addEventListener("focusin", (e) => {
    const ta = e.target.closest("textarea.segment-text");
    if (!ta) return;
    editingId = ta.getAttribute("data-id");
    selectedId = editingId;
    autoScroll = false;
    autoScrollEl.checked = false;
  });

  segmentsList.addEventListener("focusout", (e) => {
    const ta = e.target.closest("textarea.segment-text");
    if (!ta) return;
    if (editingId === ta.getAttribute("data-id")) editingId = null;
  });

  segmentsList.addEventListener("input", (e) => {
    const ta = e.target.closest("textarea.segment-text");
    if (!ta) return;
    const id = ta.getAttribute("data-id");
    const seg = segments.find((s) => s.id === id);
    if (!seg) return;
    if (!dirty.has(id)) {
      pushUndo([{ id, text: currentText(seg) }]);
    }
    dirty.set(id, ta.value);
    seg.edit_source = "manual";
    markDirty();
    scheduleSave();
  });

  searchEl.addEventListener("input", () => renderSegments());

  btnReplace.addEventListener("click", () => {
    const find = (searchEl.value || "").trim();
    if (!find) return;
    const replacement = window.prompt(`Replace all "${find}" with:`, "");
    if (replacement === null) return;
    if (!window.confirm(`Replace all occurrences of "${find}"?`)) return;
    pushUndo(segments.map((s) => ({ id: s.id, text: currentText(s) })));
    segments.forEach((seg) => {
      const cur = currentText(seg);
      if (!cur.includes(find)) return;
      dirty.set(seg.id, cur.split(find).join(replacement));
      seg.edit_source = "manual";
    });
    markDirty();
    renderSegments();
    scheduleSave();
  });

  btnUndo.addEventListener("click", () => {
    const snap = undoStack.pop();
    if (!snap) return;
    redoStack.push(segments.map((s) => ({ id: s.id, text: currentText(s) })));
    snap.forEach((item) => dirty.set(item.id, item.text));
    markDirty();
    renderSegments();
    scheduleSave();
  });

  btnRedo.addEventListener("click", () => {
    const snap = redoStack.pop();
    if (!snap) return;
    undoStack.push(segments.map((s) => ({ id: s.id, text: currentText(s) })));
    snap.forEach((item) => dirty.set(item.id, item.text));
    markDirty();
    renderSegments();
    scheduleSave();
  });

  btnRestore.addEventListener("click", async () => {
    if (!selectedId) {
      alert("Select a segment first.");
      return;
    }
    await flushSave();
    const data = await fetchJson(
      `/api/jobs/${jobId}/segments/${selectedId}/restore?expected_revision=${revision}`,
      { method: "POST" }
    );
    revision = data.transcript_revision;
    segments = (data.segments || []).map((s) => ({
      id: s.id,
      start: s.start,
      end: s.end,
      position: s.position,
      original_text: s.original_text,
      edited_text: s.edited_text,
      is_edited: s.is_edited,
      edit_source: s.edit_source,
    }));
    dirty.delete(selectedId);
    markDirty();
    renderSegments();
    loadRevisions();
    setSaveState("idle", "Saved");
  });

  btnSplit.addEventListener("click", async () => {
    if (!selectedId) {
      alert("Select a segment first.");
      return;
    }
    const seg = segments.find((s) => s.id === selectedId);
    if (!seg) return;
    const t = audio.currentTime;
    if (!(t > seg.start && t < seg.end)) {
      alert(`Move the playhead inside this segment (${formatTime(seg.start)}–${formatTime(seg.end)}). Timestamps are never invented.`);
      return;
    }
    await flushSave();
    const data = await fetchJson(`/api/jobs/${jobId}/segments/split`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        segment_id: selectedId,
        split_at: t,
        expected_revision: revision,
      }),
    });
    revision = data.transcript_revision;
    segments = (data.segments || []).map((s) => ({
      id: s.id,
      start: s.start,
      end: s.end,
      position: s.position,
      original_text: s.original_text,
      edited_text: s.edited_text,
      is_edited: s.is_edited,
      edit_source: s.edit_source,
    }));
    dirty.clear();
    markDirty();
    renderSegments();
    loadRevisions();
  });

  btnMerge.addEventListener("click", async () => {
    if (!selectedId) {
      alert("Select a segment first.");
      return;
    }
    const idx = segments.findIndex((s) => s.id === selectedId);
    if (idx < 0 || idx >= segments.length - 1) {
      alert("Select a segment that has a next neighbor.");
      return;
    }
    await flushSave();
    const data = await fetchJson(`/api/jobs/${jobId}/segments/merge`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        left_id: segments[idx].id,
        right_id: segments[idx + 1].id,
        expected_revision: revision,
      }),
    });
    revision = data.transcript_revision;
    segments = (data.segments || []).map((s) => ({
      id: s.id,
      start: s.start,
      end: s.end,
      position: s.position,
      original_text: s.original_text,
      edited_text: s.edited_text,
      is_edited: s.is_edited,
      edit_source: s.edit_source,
    }));
    dirty.clear();
    markDirty();
    renderSegments();
    loadRevisions();
  });

  exportSource.addEventListener("change", updateExportLinks);
  btnRefreshRev.addEventListener("click", () => loadRevisions());

  document.addEventListener("keydown", (e) => {
    const tag = (e.target && e.target.tagName) || "";
    if (tag === "TEXTAREA" || tag === "INPUT" || tag === "SELECT") {
      if (e.key === "s" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        flushSave();
      }
      return;
    }
    if (e.code === "Space") {
      e.preventDefault();
      btnPlay.click();
    } else if (e.key === "ArrowLeft") {
      e.preventDefault();
      btnBack5.click();
    } else if (e.key === "ArrowRight") {
      e.preventDefault();
      btnFwd5.click();
    } else if (e.key === "[") {
      const opts = [...rateSelect.options].map((o) => Number(o.value));
      const i = opts.indexOf(Number(rateSelect.value));
      if (i > 0) {
        rateSelect.value = String(opts[i - 1]);
        rateSelect.dispatchEvent(new Event("change"));
      }
    } else if (e.key === "]") {
      const opts = [...rateSelect.options].map((o) => Number(o.value));
      const i = opts.indexOf(Number(rateSelect.value));
      if (i >= 0 && i < opts.length - 1) {
        rateSelect.value = String(opts[i + 1]);
        rateSelect.dispatchEvent(new Event("change"));
      }
    }
  });

  window.addEventListener("beforeunload", (e) => {
    if (dirty.size > 0) {
      e.preventDefault();
      e.returnValue = "";
    }
  });

  loadSegments()
    .then(() => {
      loadRevisions();
      softLoadVocabulary();
    })
    .catch((err) => {
      statusMsg.hidden = false;
      statusMsg.textContent = err.message || "Failed to load studio";
    });
})();
