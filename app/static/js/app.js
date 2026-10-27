(() => {
  const form = document.getElementById("job-form");
  const dropzone = document.getElementById("dropzone");
  const fileInput = document.getElementById("file-input");
  const fileMeta = document.getElementById("file-meta");
  const fileName = document.getElementById("file-name");
  const fileSize = document.getElementById("file-size");
  const clearFile = document.getElementById("clear-file");
  const startBtn = document.getElementById("start-btn");
  const uploadProgress = document.getElementById("upload-progress");
  const uploadProgressBar = document.getElementById("upload-progress-bar");
  const uploadProgressLabel = document.getElementById("upload-progress-label");
  const statusPanel = document.getElementById("status-panel");
  const statusBadge = document.getElementById("status-badge");
  const statusFilename = document.getElementById("status-filename");
  const statusModel = document.getElementById("status-model");
  const statusLanguage = document.getElementById("status-language");
  const statusProgress = document.getElementById("status-progress");
  const statusError = document.getElementById("status-error");
  const jobMeters = document.getElementById("job-meters");
  const jobProgressBar = document.getElementById("job-progress-bar");
  const jobProgressPct = document.getElementById("job-progress-pct");
  const jobProgressLabel = document.getElementById("job-progress-label");
  const cpuMeter = document.getElementById("cpu-meter");
  const jobCpuBar = document.getElementById("job-cpu-bar");
  const jobCpuPct = document.getElementById("job-cpu-pct");
  const resultPanel = document.getElementById("result-panel");
  const resultText = document.getElementById("result-text");
  const copyBtn = document.getElementById("copy-btn");
  const toast = document.getElementById("toast");
  const downloadTxt = document.getElementById("download-txt");
  const downloadSrt = document.getElementById("download-srt");
  const downloadVtt = document.getElementById("download-vtt");
  const downloadJson = document.getElementById("download-json");
  const correctionsPanel = document.getElementById("corrections-panel");
  const originalTextEl = document.getElementById("original-text");
  const suggestedTextEl = document.getElementById("suggested-text");
  const matchesList = document.getElementById("matches-list");
  const correctionsEmpty = document.getElementById("corrections-empty");
  const analyzeBtn = document.getElementById("analyze-btn");
  const restoreOriginalBtn = document.getElementById("restore-original-btn");
  const saveReviewedBtn = document.getElementById("save-reviewed-btn");
  const downloadReviewed = document.getElementById("download-reviewed");
  const referenceTextEl = document.getElementById("reference-text");
  const werOriginalBtn = document.getElementById("wer-original-btn");
  const werReviewedBtn = document.getElementById("wer-reviewed-btn");
  const werResult = document.getElementById("wer-result");
  const studioEntry = document.getElementById("studio-entry");
  const openStudioLink = document.getElementById("open-studio-link");

  let selectedFile = null;
  let pollTimer = null;
  let currentJobId = null;
  let currentAnalysis = null;
  let matchDecisions = {};
  const ACTIVE_POLL_MS = 1000;

  function formatBytes(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
  }

  function setFile(file) {
    selectedFile = file || null;
    if (!selectedFile) {
      fileMeta.hidden = true;
      startBtn.disabled = true;
      fileInput.value = "";
      return;
    }
    fileName.textContent = selectedFile.name;
    fileSize.textContent = ` · ${formatBytes(selectedFile.size)}`;
    fileMeta.hidden = false;
    startBtn.disabled = false;
  }

  function setUploadProgress(pct, label) {
    uploadProgress.hidden = false;
    uploadProgressLabel.hidden = false;
    uploadProgressBar.style.width = `${Math.max(0, Math.min(100, pct))}%`;
    uploadProgressLabel.textContent = label || "";
  }

  function hideUploadProgress() {
    uploadProgress.hidden = true;
    uploadProgressLabel.hidden = true;
    uploadProgressBar.style.width = "0%";
  }

  function setBadge(status) {
    statusBadge.textContent = status;
    statusBadge.className = `badge ${status}`;
  }

  function showMeters(job) {
    const pct =
      job.progress_percent == null || job.progress_percent === ""
        ? null
        : Math.max(0, Math.min(100, Number(job.progress_percent)));
    const cpu =
      job.cpu_percent == null || job.cpu_percent === ""
        ? null
        : Math.max(0, Math.min(100, Number(job.cpu_percent)));
    const active = [
      "queued",
      "waiting_for_resources",
      "preparing",
      "processing",
      "finalizing",
    ].includes(job.status);
    const done = job.status === "completed";

    if (!active && !done) {
      jobMeters.hidden = true;
      return;
    }

    jobMeters.hidden = false;
    const displayPct = done ? 100 : pct == null ? (job.status === "queued" ? 0 : 1) : pct;
    jobProgressBar.style.width = `${displayPct}%`;
    jobProgressPct.textContent = `${Math.round(displayPct)}%`;
    jobProgressLabel.textContent = job.progress_message || "";

    if (active && cpu != null && !Number.isNaN(cpu)) {
      cpuMeter.hidden = false;
      jobCpuBar.style.width = `${cpu}%`;
      jobCpuPct.textContent = `${Math.round(cpu)}%`;
    } else if (done) {
      cpuMeter.hidden = true;
    } else {
      cpuMeter.hidden = true;
    }
  }

  function showStatus(job) {
    statusPanel.hidden = false;
    setBadge(job.status);
    statusFilename.textContent = job.original_filename;
    statusModel.textContent = job.model_name;
    statusLanguage.textContent = job.language;
    const pct =
      job.progress_percent == null ? null : Math.round(Number(job.progress_percent));
    if (pct != null && !Number.isNaN(pct) && job.progress_message) {
      statusProgress.textContent = `${pct}% · ${job.progress_message}`;
    } else {
      statusProgress.textContent = job.progress_message || "—";
    }
    showMeters(job);
    if (job.status === "failed" && job.error_message) {
      statusError.hidden = false;
      statusError.textContent = job.error_message;
    } else {
      statusError.hidden = true;
      statusError.textContent = "";
    }
  }

  function setDownloads(jobId, formats) {
    const base = `/api/jobs/${jobId}/download?format=`;
    downloadTxt.href = `${base}txt`;
    downloadSrt.href = `${base}srt`;
    downloadVtt.href = `${base}vtt`;
    downloadJson.href = `${base}json`;
    const map = {
      txt: downloadTxt,
      srt: downloadSrt,
      vtt: downloadVtt,
      json: downloadJson,
    };
    Object.entries(map).forEach(([fmt, el]) => {
      el.style.display = !formats || formats.includes(fmt) ? "" : "none";
    });
  }

  async function fetchJson(url, options) {
    const res = await fetch(url, options);
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

  function renderMatches(analysis) {
    const matches = (analysis && analysis.matches) || [];
    if (!matches.length) {
      matchesList.innerHTML = "";
      correctionsEmpty.hidden = false;
      return;
    }
    correctionsEmpty.hidden = true;
    matchesList.innerHTML = matches
      .map((m, idx) => {
        const decision = matchDecisions[idx] || { action: "accept" };
        return `<div class="match-card" data-index="${idx}">
          <div class="match-main">
            <span class="match-from">${escapeHtml(m.matched_text)}</span>
            <span class="match-arrow" aria-hidden="true">→</span>
            <input class="match-to" data-index="${idx}" value="${escapeHtml(decision.replacement || m.correct)}" />
            <span class="badge muted-badge">${escapeHtml(m.category || "")}</span>
          </div>
          <div class="match-actions">
            <label><input type="radio" name="match-${idx}" value="accept" ${decision.action === "accept" ? "checked" : ""}/> Accept</label>
            <label><input type="radio" name="match-${idx}" value="edit" ${decision.action === "edit" ? "checked" : ""}/> Edit</label>
            <label><input type="radio" name="match-${idx}" value="reject" ${decision.action === "reject" ? "checked" : ""}/> Reject</label>
          </div>
        </div>`;
      })
      .join("");
  }

  function rebuildSuggestedFromDecisions() {
    if (!currentAnalysis) return;
    const original = currentAnalysis.original_text || "";
    const matches = currentAnalysis.matches || [];
    let out = "";
    let cursor = 0;
    matches.forEach((m, idx) => {
      const d = matchDecisions[idx] || { action: "accept" };
      if (m.start < cursor) return;
      out += original.slice(cursor, m.start);
      if (d.action === "reject") {
        out += original.slice(m.start, m.end);
      } else if (d.action === "edit") {
        out += d.replacement || m.correct;
      } else {
        out += m.correct;
      }
      cursor = m.end;
    });
    out += original.slice(cursor);
    suggestedTextEl.value = out;
  }

  function showCorrections(analysis, reviewedText) {
    correctionsPanel.hidden = false;
    currentAnalysis = analysis;
    matchDecisions = {};
    (analysis.matches || []).forEach((_, idx) => {
      matchDecisions[idx] = { action: "accept" };
    });
    originalTextEl.value = analysis.original_text || "";
    suggestedTextEl.value = reviewedText || analysis.suggested_text || analysis.original_text || "";
    renderMatches(analysis);
    if (reviewedText) {
      downloadReviewed.hidden = false;
      downloadReviewed.href = `/api/jobs/${currentJobId}/download-reviewed`;
    } else {
      downloadReviewed.hidden = true;
    }
  }

  async function loadAnalysis(jobId, force) {
    try {
      const analysis = force
        ? await fetchJson(`/api/jobs/${jobId}/analyze`, { method: "POST" })
        : await fetchJson(`/api/jobs/${jobId}/analyze`).catch(async () =>
            fetchJson(`/api/jobs/${jobId}/analyze`, { method: "POST" })
          );
      const result = await fetchJson(`/api/jobs/${jobId}/result`);
      showCorrections(analysis, result.reviewed_text || null);
    } catch (err) {
      correctionsPanel.hidden = false;
      correctionsEmpty.hidden = false;
      correctionsEmpty.textContent = err.message || "Smart Vocabulary analysis unavailable";
      matchesList.innerHTML = "";
      originalTextEl.value = resultText.value || "";
      suggestedTextEl.value = resultText.value || "";
    }
  }

  async function pollJob(jobId) {
    const job = await fetchJson(`/api/jobs/${jobId}`);
    showStatus(job);
    if (job.status === "completed") {
      stopPolling();
      const result = await fetchJson(`/api/jobs/${jobId}/result`);
      resultPanel.hidden = false;
      resultText.value = result.transcription_text || "";
      setDownloads(jobId, result.available_formats || ["txt"]);
      if (studioEntry && openStudioLink) {
        studioEntry.hidden = false;
        openStudioLink.href = `/studio/${jobId}`;
      }
      startBtn.disabled = !selectedFile;
      await loadAnalysis(jobId, false);
      return;
    }
    if (job.status === "failed") {
      stopPolling();
      startBtn.disabled = !selectedFile;
      return;
    }
    // Keep a faster poll while the job is still active.
    ensurePolling(jobId, ACTIVE_POLL_MS);
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function ensurePolling(jobId, intervalMs) {
    if (pollTimer && pollTimer._intervalMs === intervalMs) return;
    stopPolling();
    pollTimer = setInterval(() => {
      pollJob(jobId).catch((err) => {
        statusError.hidden = false;
        statusError.textContent = err.message;
      });
    }, intervalMs);
    pollTimer._intervalMs = intervalMs;
  }

  function startPolling(jobId) {
    currentJobId = jobId;
    stopPolling();
    pollJob(jobId).catch((err) => {
      statusError.hidden = false;
      statusError.textContent = err.message;
    });
    ensurePolling(jobId, ACTIVE_POLL_MS);
  }

  function uploadJob(file, model, language, extras = {}) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      const data = new FormData();
      data.append("file", file);
      data.append("model", model);
      data.append("language", language);
      if (extras.inference_backend) {
        data.append("inference_backend", extras.inference_backend);
      }
      if (extras.priority != null && extras.priority !== "") {
        data.append("priority", String(extras.priority));
      }
      if (extras.allow_unsafe_model) {
        data.append("allow_unsafe_model", "true");
      }

      xhr.open("POST", "/api/jobs");
      xhr.withCredentials = true;

      xhr.upload.onprogress = (event) => {
        if (!event.lengthComputable) {
          setUploadProgress(0, "Uploading…");
          return;
        }
        const pct = Math.round((event.loaded / event.total) * 100);
        setUploadProgress(pct, `Uploading… ${pct}%`);
      };

      xhr.onload = () => {
        let body = null;
        try {
          body = JSON.parse(xhr.responseText);
        } catch (_) {
          body = null;
        }
        if (xhr.status === 401) {
          window.location.href = "/login";
          reject(new Error("Authentication required"));
          return;
        }
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(body);
        } else {
          const detail = body && body.detail ? body.detail : `Upload failed (${xhr.status})`;
          reject(new Error(typeof detail === "string" ? detail : JSON.stringify(detail)));
        }
      };

      xhr.onerror = () => reject(new Error("Network error during upload"));
      xhr.send(data);
    });
  }

  dropzone.addEventListener("click", () => fileInput.click());
  dropzone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      fileInput.click();
    }
  });

  ["dragenter", "dragover"].forEach((evt) => {
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropzone.classList.add("dragover");
    });
  });
  ["dragleave", "drop"].forEach((evt) => {
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropzone.classList.remove("dragover");
    });
  });
  dropzone.addEventListener("drop", (e) => {
    const file = e.dataTransfer.files && e.dataTransfer.files[0];
    if (file) setFile(file);
  });

  fileInput.addEventListener("change", () => {
    const file = fileInput.files && fileInput.files[0];
    setFile(file || null);
  });

  clearFile.addEventListener("click", () => setFile(null));

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!selectedFile) return;

    startBtn.disabled = true;
    resultPanel.hidden = true;
    correctionsPanel.hidden = true;
    if (studioEntry) studioEntry.hidden = true;
    resultText.value = "";
    werResult.hidden = true;
    statusError.hidden = true;
    setUploadProgress(0, "Starting upload…");

    const model = document.getElementById("model-select").value;
    const language = document.getElementById("language-select").value;
    const backendEl = document.getElementById("backend-select");
    const priorityEl = document.getElementById("priority-input");
    const unsafeEl = document.getElementById("allow-unsafe");
    const extras = {
      inference_backend: backendEl ? backendEl.value : "openai-whisper",
      priority: priorityEl ? priorityEl.value : 100,
      allow_unsafe_model: !!(unsafeEl && unsafeEl.checked),
    };

    try {
      const created = await uploadJob(selectedFile, model, language, extras);
      setUploadProgress(100, "Upload complete · queued");
      showStatus({
        status: created.status,
        original_filename: created.original_filename,
        model_name: created.model_name,
        language: created.language,
        progress_message: "Queued for transcription",
        progress_percent: 0,
        cpu_percent: null,
      });
      startPolling(created.id);
      setTimeout(hideUploadProgress, 1200);
    } catch (err) {
      hideUploadProgress();
      statusPanel.hidden = false;
      setBadge("failed");
      statusError.hidden = false;
      statusError.textContent = err.message || "Upload failed";
      startBtn.disabled = !selectedFile;
    }
  });

  copyBtn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(resultText.value || "");
      toast.hidden = false;
      setTimeout(() => {
        toast.hidden = true;
      }, 1800);
    } catch (_) {
      resultText.select();
      document.execCommand("copy");
      toast.hidden = false;
      setTimeout(() => {
        toast.hidden = true;
      }, 1800);
    }
  });

  matchesList.addEventListener("change", (e) => {
    const radio = e.target.closest('input[type="radio"]');
    if (!radio) return;
    const idx = Number(radio.name.replace("match-", ""));
    matchDecisions[idx] = matchDecisions[idx] || {};
    matchDecisions[idx].action = radio.value;
    const input = matchesList.querySelector(`input.match-to[data-index="${idx}"]`);
    if (input) matchDecisions[idx].replacement = input.value;
    rebuildSuggestedFromDecisions();
  });

  matchesList.addEventListener("input", (e) => {
    const input = e.target.closest("input.match-to");
    if (!input) return;
    const idx = Number(input.getAttribute("data-index"));
    matchDecisions[idx] = matchDecisions[idx] || { action: "edit" };
    matchDecisions[idx].replacement = input.value;
    if (matchDecisions[idx].action === "accept") {
      matchDecisions[idx].action = "edit";
      const editRadio = matchesList.querySelector(`input[name="match-${idx}"][value="edit"]`);
      if (editRadio) editRadio.checked = true;
    }
    rebuildSuggestedFromDecisions();
  });

  analyzeBtn.addEventListener("click", () => {
    if (!currentJobId) return;
    loadAnalysis(currentJobId, true).catch((err) => {
      statusError.hidden = false;
      statusError.textContent = err.message;
    });
  });

  restoreOriginalBtn.addEventListener("click", async () => {
    if (!currentJobId) return;
    await fetchJson(`/api/jobs/${currentJobId}/corrections`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ restore_original: true, decisions: [] }),
    });
    suggestedTextEl.value = originalTextEl.value;
    downloadReviewed.hidden = true;
    if (currentAnalysis) {
      (currentAnalysis.matches || []).forEach((_, idx) => {
        matchDecisions[idx] = { action: "reject" };
      });
      renderMatches(currentAnalysis);
    }
  });

  saveReviewedBtn.addEventListener("click", async () => {
    if (!currentJobId) return;
    const decisions = Object.keys(matchDecisions).map((k) => {
      const idx = Number(k);
      const d = matchDecisions[idx];
      return {
        match_index: idx,
        action: d.action || "reject",
        replacement: d.replacement || null,
      };
    });
    const saved = await fetchJson(`/api/jobs/${currentJobId}/corrections`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        decisions,
        reviewed_text: suggestedTextEl.value,
      }),
    });
    suggestedTextEl.value = saved.reviewed_text;
    downloadReviewed.hidden = false;
    downloadReviewed.href = `/api/jobs/${currentJobId}/download-reviewed`;
    toast.hidden = false;
    toast.textContent = "Reviewed transcription saved";
    setTimeout(() => {
      toast.hidden = true;
      toast.textContent = "Copied to clipboard";
    }, 1800);
  });

  async function runWer(compareAgainst) {
    if (!currentJobId) return;
    const reference = (referenceTextEl.value || "").trim();
    if (!reference) {
      werResult.hidden = false;
      werResult.textContent = "Paste a reference transcript first.";
      return;
    }
    const data = await fetchJson(`/api/jobs/${currentJobId}/evaluate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        reference_transcript: reference,
        compare_against: compareAgainst,
      }),
    });
    werResult.hidden = false;
    werResult.textContent = [
      `Compare: ${data.compare_against}`,
      `WER: ${(data.wer * 100).toFixed(2)}% (${data.edits} edits / ${data.reference_words} ref words)`,
      `Substitutions: ${data.substitutions} · Deletions: ${data.deletions} · Insertions: ${data.insertions}`,
      `Suggestions accepted: ${data.suggestions_accepted} · rejected: ${data.suggestions_rejected}`,
    ].join("\n");
  }

  werOriginalBtn.addEventListener("click", () => {
    runWer("original").catch((err) => {
      werResult.hidden = false;
      werResult.textContent = err.message;
    });
  });
  werReviewedBtn.addEventListener("click", () => {
    runWer("reviewed").catch((err) => {
      werResult.hidden = false;
      werResult.textContent = err.message;
    });
  });
})();
