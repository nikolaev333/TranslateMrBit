(() => {
  const jobId = window.__QUALITY_JOB_ID__;
  if (!jobId) return;

  const errEl = document.getElementById("quality-error");
  const statusEl = document.getElementById("quality-status");
  const btnAnalyze = document.getElementById("btn-analyze");
  const linkReport = document.getElementById("link-report");
  const filterStatus = document.getElementById("filter-status");
  const reviewQueue = document.getElementById("review-queue");
  const timeline = document.getElementById("qa-timeline");
  const evalResult = document.getElementById("eval-result");
  const btnEvaluate = document.getElementById("btn-evaluate");

  let pollTimer = null;
  let warnings = [];
  let durationHint = 0;

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function fetchJson(url, options) {
    const res = await fetch(url, { credentials: "same-origin", ...options });
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

  function showError(msg) {
    errEl.hidden = !msg;
    errEl.textContent = msg || "";
  }

  function countByPrefix(byRule, prefixes) {
    let n = 0;
    Object.entries(byRule || {}).forEach(([k, v]) => {
      if (prefixes.some((p) => k.startsWith(p))) n += v;
    });
    return n;
  }

  function renderOverview(data) {
    const s = data.summary || {};
    const byRule = s.by_rule || {};
    document.getElementById("stat-segments").textContent = s.segments_analyzed ?? "—";
    document.getElementById("stat-open").textContent = s.open_reviews ?? "—";
    document.getElementById("stat-rep").textContent = countByPrefix(byRule, ["REP_"]);
    document.getElementById("stat-audio").textContent = countByPrefix(byRule, ["AUD_"]);
    document.getElementById("stat-ts").textContent = countByPrefix(byRule, ["TS_"]);
    document.getElementById("stat-term").textContent = byRule.TERM_GLOSSARY || 0;
    document.getElementById("stat-done").textContent =
      (s.reviewed || 0) + (s.confirmed_errors || 0) + (s.false_positives || 0);
    const run = data.run;
    if (!run) {
      statusEl.textContent = "No analysis yet. Click Run analysis.";
      document.getElementById("priority-line").textContent = "";
      return;
    }
    statusEl.textContent = `Run ${run.status} · version ${run.analysis_version || "—"}`;
    document.getElementById("priority-line").textContent = run.overall_priority
      ? `Overall review priority: ${run.overall_priority} (heuristic — not accuracy %)`
      : "";
    linkReport.href = `/api/jobs/${jobId}/quality/report`;
  }

  function renderTimeline(list) {
    const withTime = list.filter((w) => w.start_sec != null);
    const maxT = Math.max(durationHint, ...withTime.map((w) => Number(w.end_sec || w.start_sec) || 0), 1);
    durationHint = maxT;
    if (!withTime.length) {
      timeline.innerHTML = `<div class="qa-timeline-track"><span class="muted small">No timed warnings</span></div>`;
      return;
    }
    const markers = withTime
      .map((w) => {
        const pct = Math.min(100, (Number(w.start_sec) / maxT) * 100);
        const cls = `prio-${(w.priority || "LOW").toLowerCase()}`;
        return `<button type="button" class="qa-marker ${cls}" style="left:${pct}%" data-id="${escapeHtml(w.id)}" title="${escapeHtml(w.rule_id)} @ ${Number(w.start_sec).toFixed(1)}s"></button>`;
      })
      .join("");
    timeline.innerHTML = `<div class="qa-timeline-track">${markers}</div>`;
  }

  function renderQueue(list) {
    const filter = filterStatus.value;
    const rows = filter ? list.filter((w) => w.review_status === filter) : list;
    if (!rows.length) {
      reviewQueue.innerHTML = `<p class="muted">No warnings in this filter.</p>`;
      return;
    }
    reviewQueue.innerHTML = rows
      .map((w) => {
        const t =
          w.start_sec != null
            ? `${Number(w.start_sec).toFixed(1)}s–${w.end_sec != null ? Number(w.end_sec).toFixed(1) + "s" : "?"}`
            : "—";
        const studioLink =
          w.segment_ids && w.segment_ids[0]
            ? `<a class="btn btn-ghost" href="/studio/${jobId}">Open segment</a>`
            : `<a class="btn btn-ghost" href="/studio/${jobId}">Open Studio</a>`;
        return `<article class="review-card" id="warn-${escapeHtml(w.id)}" data-id="${escapeHtml(w.id)}">
          <div class="review-card-head">
            <span class="badge prio-badge prio-${escapeHtml((w.priority || "").toLowerCase())}">${escapeHtml(w.priority)}</span>
            <strong>${escapeHtml(w.rule_id)}</strong>
            <span class="muted mono-cell">${escapeHtml(t)}</span>
          </div>
          <p>${escapeHtml(w.explanation)}</p>
          <p class="muted small">${escapeHtml(w.description)}</p>
          <label class="field">
            <span>Reviewer note</span>
            <input type="text" class="note-input" data-id="${escapeHtml(w.id)}" value="${escapeHtml(w.reviewer_note || "")}" maxlength="2000" />
          </label>
          <div class="toolbar-group">
            <button type="button" class="btn btn-ghost" data-action="reviewed" data-id="${escapeHtml(w.id)}">Mark reviewed</button>
            <button type="button" class="btn btn-ghost" data-action="confirmed_error" data-id="${escapeHtml(w.id)}">Confirm error</button>
            <button type="button" class="btn btn-ghost" data-action="false_positive" data-id="${escapeHtml(w.id)}">Dismiss FP</button>
            <button type="button" class="btn btn-ghost" data-action="open" data-id="${escapeHtml(w.id)}">Reopen</button>
            ${studioLink}
          </div>
          <div class="muted small">Status: ${escapeHtml(w.review_status)}</div>
        </article>`;
      })
      .join("");
  }

  async function refresh() {
    showError("");
    const overview = await fetchJson(`/api/jobs/${jobId}/quality`);
    renderOverview(overview);
    const run = overview.run;
    if (run && (run.status === "queued" || run.status === "running")) {
      statusEl.textContent = `Analysis ${run.status}…`;
      ensurePoll();
    } else {
      stopPoll();
    }
    const wdata = await fetchJson(`/api/jobs/${jobId}/quality/warnings`);
    warnings = wdata.warnings || [];
    renderTimeline(warnings);
    renderQueue(warnings);
  }

  function stopPoll() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function ensurePoll() {
    if (pollTimer) return;
    pollTimer = setInterval(() => {
      refresh().catch((e) => showError(e.message));
    }, 1500);
  }

  btnAnalyze.addEventListener("click", async () => {
    try {
      showError("");
      await fetchJson(`/api/jobs/${jobId}/quality/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ include_audio: true }),
      });
      await refresh();
      ensurePoll();
    } catch (e) {
      showError(e.message);
    }
  });

  filterStatus.addEventListener("change", () => renderQueue(warnings));

  timeline.addEventListener("click", (e) => {
    const btn = e.target.closest(".qa-marker");
    if (!btn) return;
    const id = btn.getAttribute("data-id");
    const el = document.getElementById(`warn-${id}`);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
  });

  reviewQueue.addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-action]");
    if (!btn) return;
    const id = btn.getAttribute("data-id");
    const action = btn.getAttribute("data-action");
    const noteInput = reviewQueue.querySelector(`input.note-input[data-id="${CSS.escape(id)}"]`);
    const note = noteInput ? noteInput.value : "";
    const statusMap = {
      reviewed: "reviewed",
      confirmed_error: "confirmed_error",
      false_positive: "false_positive",
      open: "open",
    };
    try {
      await fetchJson(`/api/jobs/${jobId}/quality/warnings/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          review_status: statusMap[action] || "open",
          reviewer_note: note || null,
        }),
      });
      await refresh();
    } catch (err) {
      showError(err.message);
    }
  });

  btnEvaluate.addEventListener("click", async () => {
    const reference = (document.getElementById("ref-text").value || "").trim();
    if (!reference) {
      evalResult.hidden = false;
      evalResult.textContent = "Paste a human reference transcript first.";
      return;
    }
    try {
      const data = await fetchJson(`/api/jobs/${jobId}/quality/evaluate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          reference_transcript: reference,
          compare_against: document.getElementById("ref-compare").value,
        }),
      });
      evalResult.hidden = false;
      evalResult.textContent = [
        `WER: ${(data.wer * 100).toFixed(2)}%`,
        `CER: ${(data.cer * 100).toFixed(2)}%`,
        `Sub ${data.substitutions} · Del ${data.deletions} · Ins ${data.insertions}`,
        data.note,
      ].join("\n");
    } catch (err) {
      evalResult.hidden = false;
      evalResult.textContent = err.message;
    }
  });

  refresh().catch((e) => {
    showError(e.message);
    statusEl.textContent = "Failed to load";
  });
})();
