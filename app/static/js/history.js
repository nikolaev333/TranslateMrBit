(() => {
  const body = document.getElementById("history-body");
  const countEl = document.getElementById("history-count");
  const errorEl = document.getElementById("history-error");
  const refreshBtn = document.getElementById("refresh-history");
  const dialog = document.getElementById("view-dialog");
  const dialogTitle = document.getElementById("dialog-title");
  const dialogText = document.getElementById("dialog-text");
  const dialogCopy = document.getElementById("dialog-copy");
  const dialogDownload = document.getElementById("dialog-download");

  function formatDate(iso) {
    if (!iso) return "—";
    try {
      return new Date(iso).toLocaleString();
    } catch (_) {
      return iso;
    }
  }

  function formatBytes(bytes) {
    if (bytes == null) return "";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
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

  function renderRows(jobs) {
    if (!jobs.length) {
      body.innerHTML = `<tr><td colspan="6" class="muted">No jobs yet.</td></tr>`;
      return;
    }
    body.innerHTML = jobs
      .map((job) => {
        const canDownload = job.status === "completed";
        const canDelete = job.status !== "processing";
        return `
        <tr data-id="${job.id}">
          <td>
            <div>${escapeHtml(job.original_filename)}</div>
            <div class="muted">${formatBytes(job.file_size)}</div>
          </td>
          <td><span class="badge ${job.status}">${escapeHtml(job.status)}</span></td>
          <td>${escapeHtml(job.model_name)}</td>
          <td>${escapeHtml(job.language)}</td>
          <td>${escapeHtml(formatDate(job.created_at))}</td>
          <td>
            <div class="row-actions">
              <button type="button" class="btn btn-secondary btn-view" ${canDownload ? "" : "disabled"}>View</button>
              <a class="btn btn-secondary" ${canDownload ? `href="/studio/${job.id}"` : "aria-disabled=true tabindex=-1 style=\"opacity:.45;pointer-events:none\""}>Studio</a>
              <a class="btn btn-ghost" ${canDownload ? `href="/quality/${job.id}"` : "aria-disabled=true tabindex=-1 style=\"opacity:.45;pointer-events:none\""}>Quality</a>
              <a class="btn btn-ghost" ${canDownload ? `href="/api/jobs/${job.id}/download?format=txt"` : "aria-disabled=true tabindex=-1 style=\"opacity:.45;pointer-events:none\""}>TXT</a>
              <a class="btn btn-ghost" ${canDownload ? `href="/api/jobs/${job.id}/download?format=srt"` : "aria-disabled=true tabindex=-1 style=\"opacity:.45;pointer-events:none\""}>SRT</a>
              <button type="button" class="btn btn-danger btn-delete" ${canDelete ? "" : "disabled"}>Delete</button>
            </div>
          </td>
        </tr>`;
      })
      .join("");
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function loadHistory() {
    errorEl.hidden = true;
    if (countEl) countEl.textContent = "Loading…";
    try {
      const data = await fetchJson("/api/jobs?limit=100");
      if (countEl) {
        countEl.textContent = `${data.total} job${data.total === 1 ? "" : "s"}`;
      }
      renderRows(data.jobs || []);
    } catch (err) {
      errorEl.hidden = false;
      errorEl.textContent = err.message;
      body.innerHTML = `<tr><td colspan="6" class="muted">Failed to load history.</td></tr>`;
    }
  }

  body.addEventListener("click", async (e) => {
    const row = e.target.closest("tr[data-id]");
    if (!row) return;
    const jobId = row.getAttribute("data-id");

    if (e.target.classList.contains("btn-view")) {
      try {
        const result = await fetchJson(`/api/jobs/${jobId}/result`);
        dialogTitle.textContent = row.querySelector("td div")?.textContent || "Transcription";
        dialogText.value = result.transcription_text || "";
        dialogDownload.href = `/api/jobs/${jobId}/download?format=txt`;
        dialog.showModal();
      } catch (err) {
        errorEl.hidden = false;
        errorEl.textContent = err.message;
      }
    }

    if (e.target.classList.contains("btn-delete")) {
      if (!confirm("Delete this job and its files?")) return;
      try {
        await fetchJson(`/api/jobs/${jobId}`, { method: "DELETE" });
        await loadHistory();
      } catch (err) {
        errorEl.hidden = false;
        errorEl.textContent = err.message;
      }
    }
  });

  if (dialogCopy && dialogText) {
    dialogCopy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(dialogText.value || "");
      } catch (_) {
        dialogText.select();
        document.execCommand("copy");
      }
    });
  }

  if (refreshBtn) refreshBtn.addEventListener("click", loadHistory);
  loadHistory();
})();
