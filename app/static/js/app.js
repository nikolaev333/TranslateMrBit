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

  let selectedFile = null;
  let pollTimer = null;
  let currentJobId = null;
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
    const active = job.status === "queued" || job.status === "processing";
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

  async function pollJob(jobId) {
    const job = await fetchJson(`/api/jobs/${jobId}`);
    showStatus(job);
    if (job.status === "completed") {
      stopPolling();
      const result = await fetchJson(`/api/jobs/${jobId}/result`);
      resultPanel.hidden = false;
      resultText.value = result.transcription_text || "";
      setDownloads(jobId, result.available_formats || ["txt"]);
      startBtn.disabled = !selectedFile;
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

  function uploadJob(file, model, language) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      const data = new FormData();
      data.append("file", file);
      data.append("model", model);
      data.append("language", language);

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
    resultText.value = "";
    statusError.hidden = true;
    setUploadProgress(0, "Starting upload…");

    const model = document.getElementById("model-select").value;
    const language = document.getElementById("language-select").value;

    try {
      const created = await uploadJob(selectedFile, model, language);
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
})();
