(() => {
  const errEl = document.getElementById("proc-error");
  const queueBody = document.getElementById("queue-body");
  const metricsBody = document.getElementById("metrics-body");
  const modelList = document.getElementById("model-list");
  const recommendBox = document.getElementById("recommend-box");
  let selectedJobId = null;
  let pollTimer = null;

  function showError(msg) {
    if (!msg) {
      errEl.hidden = true;
      errEl.textContent = "";
      return;
    }
    errEl.hidden = false;
    errEl.textContent = msg;
  }

  function fmtBytes(n) {
    if (n == null || Number.isNaN(n)) return "—";
    const v = Number(n);
    if (v < 1024) return `${v} B`;
    if (v < 1024 * 1024) return `${(v / 1024).toFixed(1)} KB`;
    if (v < 1024 ** 3) return `${(v / 1024 ** 2).toFixed(1)} MB`;
    return `${(v / 1024 ** 3).toFixed(2)} GB`;
  }

  function fmtSec(n) {
    if (n == null || n === "") return "—";
    return `${Number(n).toFixed(2)} s`;
  }

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      credentials: "include",
      headers: { Accept: "application/json", ...(opts.headers || {}) },
      ...opts,
    });
    if (res.status === 401) {
      window.location.href = "/login";
      throw new Error("Authentication required");
    }
    let body = null;
    try {
      body = await res.json();
    } catch (_) {
      body = null;
    }
    if (!res.ok) {
      const detail = body && body.detail;
      const msg =
        typeof detail === "string"
          ? detail
          : detail && detail.message
            ? detail.message
            : `Request failed (${res.status})`;
      throw new Error(msg);
    }
    return body;
  }

  function setDetail(job) {
    document.getElementById("detail-id").textContent = job?.id || "—";
    document.getElementById("detail-stage").textContent =
      job?.processing_stage || job?.status || "—";
    document.getElementById("detail-backend").textContent =
      job?.inference_backend || "—";
    document.getElementById("detail-model").textContent = job?.model_name || "—";
    document.getElementById("detail-queue").textContent = fmtSec(job?.queue_wait_seconds);
    document.getElementById("detail-inference").textContent = fmtSec(job?.inference_seconds);
    document.getElementById("detail-rtf").textContent =
      job?.realtime_factor == null ? "—" : Number(job.realtime_factor).toFixed(3);
    document.getElementById("detail-mem").textContent = fmtBytes(job?.peak_memory_bytes);
    document.getElementById("detail-error").textContent = job?.error_message || "—";
    const cancelable =
      job &&
      ["queued", "waiting_for_resources", "preparing", "processing", "finalizing"].includes(
        job.status
      );
    const retryable = job && ["failed", "cancelled"].includes(job.status);
    document.getElementById("btn-cancel").disabled = !cancelable;
    document.getElementById("btn-retry").disabled = !retryable;
  }

  async function selectJob(jobId) {
    selectedJobId = jobId;
    if (!jobId) {
      setDetail(null);
      return;
    }
    const job = await api(`/api/processing/jobs/${jobId}`);
    setDetail(job);
  }

  async function refreshResources() {
    const [resources, workers, models, queue] = await Promise.all([
      api("/api/system/resources"),
      api("/api/system/workers"),
      api("/api/models"),
      api("/api/processing/queue"),
    ]);
    const mem = resources.memory || {};
    const disk = resources.disk || {};
    const cpu = resources.cpu_percent;
    document.getElementById("stat-cpu").textContent =
      cpu == null ? "—" : `${Number(cpu).toFixed(0)}%`;
    document.getElementById("stat-mem").textContent = fmtBytes(
      mem.effective_available_bytes ?? mem.host_available_bytes
    );
    document.getElementById("stat-disk").textContent = fmtBytes(disk.data_free_bytes);
    document.getElementById("stat-gpu").textContent = resources.gpu?.available
      ? "Available"
      : "CPU only";
    document.getElementById("stat-workers").textContent = String(
      (workers.workers || []).length
    );
    document.getElementById("stat-queue").textContent = String((queue.jobs || []).length);
    document.getElementById("stat-active").textContent = String(queue.active_count || 0);
    document.getElementById("stat-models").textContent = String(
      (models.installed || []).length
    );
    document.getElementById("sys-note").textContent =
      mem.cgroup_limit_bytes != null
        ? "Container memory limit detected (cgroup)."
        : "Host memory metrics (no cgroup limit detected).";
    document.getElementById("queue-meta").textContent = `${queue.active_count || 0} active`;

    const jobs = queue.jobs || [];
    if (!jobs.length) {
      queueBody.innerHTML = `<tr><td colspan="8" class="muted">Queue is empty</td></tr>`;
    } else {
      queueBody.innerHTML = jobs
        .map((j) => {
          const cancelHint = j.cancel_requested ? " · cancel requested" : "";
          return `<tr data-id="${j.id}" class="${j.id === selectedJobId ? "row-selected" : ""}">
            <td><button type="button" class="linkish job-pick">${j.id.slice(0, 8)}</button></td>
            <td>${escapeHtml(j.original_filename || "")}</td>
            <td>${escapeHtml(j.model_name || "")}</td>
            <td>${escapeHtml(j.inference_backend || "")}</td>
            <td>${j.priority ?? 100}</td>
            <td><span class="badge ${j.status}">${j.status}${cancelHint}</span></td>
            <td>${j.retry_count || 0}</td>
            <td><button type="button" class="btn btn-ghost btn-sm job-pick">Details</button></td>
          </tr>`;
        })
        .join("");
      queueBody.querySelectorAll(".job-pick").forEach((btn) => {
        btn.addEventListener("click", () => {
          const tr = btn.closest("tr");
          selectJob(tr?.dataset?.id).catch((e) => showError(e.message));
        });
      });
    }

    const installed = new Set(models.installed || []);
    const profiles = models.profiles || [];
    modelList.innerHTML = profiles
      .map((p) => {
        const id = p.model_id || p.id || "";
        const mark = installed.has(id) ? "installed" : "not installed";
        return `<div class="model-card">
          <strong>${escapeHtml(id)}</strong>
          <span class="badge">${mark}</span>
          <p class="muted small">CPU ~${p.approx_ram_gb_cpu} GB · CUDA ~${p.approx_vram_gb_cuda} GB · ${escapeHtml(
            (p.backends || []).join(", ")
          )}</p>
          <p class="muted small">${escapeHtml(p.notes || "")}</p>
        </div>`;
      })
      .join("");
  }

  async function refreshMetrics() {
    const data = await api("/api/processing/metrics");
    const rows = data.metrics || [];
    if (!rows.length) {
      metricsBody.innerHTML = `<tr><td colspan="6" class="muted">No measured runs yet</td></tr>`;
      return;
    }
    metricsBody.innerHTML = rows
      .map(
        (r) => `<tr>
        <td>${escapeHtml(String(r.job_id || "").slice(0, 8))}</td>
        <td>${escapeHtml(r.model_name || "")}</td>
        <td>${escapeHtml(r.backend || "")}</td>
        <td>${r.audio_duration_sec == null ? "—" : Number(r.audio_duration_sec).toFixed(2)}</td>
        <td>${r.total_sec == null ? "—" : Number(r.total_sec).toFixed(2)}</td>
        <td>${r.realtime_factor == null ? "—" : Number(r.realtime_factor).toFixed(3)}</td>
      </tr>`
      )
      .join("");
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function refreshAll() {
    showError("");
    await refreshResources();
    await refreshMetrics();
    if (selectedJobId) await selectJob(selectedJobId);
  }

  document.getElementById("btn-refresh").addEventListener("click", () => {
    refreshAll().catch((e) => showError(e.message));
  });

  document.getElementById("btn-recommend").addEventListener("click", async () => {
    try {
      const data = await api("/api/models/recommendations");
      recommendBox.hidden = false;
      recommendBox.textContent = JSON.stringify(
        {
          preferred: data.preferred,
          recommendations: (data.recommendations || []).slice(0, 8),
          disclaimer: data.disclaimer,
        },
        null,
        2
      );
    } catch (e) {
      showError(e.message);
    }
  });

  document.getElementById("btn-download").addEventListener("click", async () => {
    try {
      const data = await api("/api/models/download", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ models: ["tiny", "base", "small"] }),
      });
      recommendBox.hidden = false;
      recommendBox.textContent = JSON.stringify(data, null, 2);
    } catch (e) {
      showError(e.message);
    }
  });

  document.getElementById("btn-cancel").addEventListener("click", async () => {
    if (!selectedJobId) return;
    try {
      await api(`/api/processing/jobs/${selectedJobId}/cancel`, { method: "POST" });
      await refreshAll();
    } catch (e) {
      showError(e.message);
    }
  });

  document.getElementById("btn-retry").addEventListener("click", async () => {
    if (!selectedJobId) return;
    try {
      await api(`/api/processing/jobs/${selectedJobId}/retry`, { method: "POST" });
      await refreshAll();
    } catch (e) {
      showError(e.message);
    }
  });

  refreshAll().catch((e) => showError(e.message));
  pollTimer = setInterval(() => {
    refreshAll().catch(() => {});
  }, 4000);
  window.addEventListener("beforeunload", () => clearInterval(pollTimer));
})();
