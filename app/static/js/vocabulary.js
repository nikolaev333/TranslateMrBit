(() => {
  const body = document.getElementById("vocab-body");
  const countEl = document.getElementById("vocab-count");
  const errorEl = document.getElementById("vocab-error");
  const searchEl = document.getElementById("vocab-search");
  const langFilter = document.getElementById("vocab-language-filter");
  const catFilter = document.getElementById("vocab-category-filter");
  const enabledOnly = document.getElementById("vocab-enabled-only");
  const addBtn = document.getElementById("vocab-add-btn");
  const dialog = document.getElementById("vocab-dialog");
  const dialogTitle = document.getElementById("vocab-dialog-title");
  const editId = document.getElementById("vocab-edit-id");
  const correctEl = document.getElementById("vocab-correct");
  const altEl = document.getElementById("vocab-alternatives");
  const languageEl = document.getElementById("vocab-language");
  const categoryEl = document.getElementById("vocab-category");
  const descriptionEl = document.getElementById("vocab-description");
  const enabledEl = document.getElementById("vocab-enabled");
  const saveBtn = document.getElementById("vocab-save-btn");
  const categoryList = document.getElementById("vocab-category-list");

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

  function showError(msg) {
    errorEl.hidden = !msg;
    errorEl.textContent = msg || "";
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function parseAlternatives(raw) {
    return String(raw || "")
      .split(/[,;\n]+/)
      .map((x) => x.trim())
      .filter(Boolean);
  }

  function fillFilters(categories, entries) {
    const langs = new Set();
    entries.forEach((e) => {
      if (e.language) langs.add(e.language);
    });
    const currentLang = langFilter.value;
    const currentCat = catFilter.value;
    langFilter.innerHTML = `<option value="">All</option>` +
      [...langs].sort().map((l) => `<option value="${escapeHtml(l)}">${escapeHtml(l)}</option>`).join("");
    catFilter.innerHTML = `<option value="">All</option>` +
      categories.map((c) => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join("");
    langFilter.value = currentLang;
    catFilter.value = currentCat;
    categoryList.innerHTML = categories
      .map((c) => `<option value="${escapeHtml(c)}"></option>`)
      .join("");
  }

  function renderRows(entries) {
    if (!entries.length) {
      body.innerHTML = `<tr><td colspan="6" class="muted">No vocabulary entries yet.</td></tr>`;
      return;
    }
    body.innerHTML = entries
      .map((e) => {
        const alts = (e.alternatives || []).join(", ");
        return `<tr data-id="${escapeHtml(e.id)}">
          <td><strong>${escapeHtml(e.correct_term)}</strong>
            ${e.description ? `<div class="muted small">${escapeHtml(e.description)}</div>` : ""}
          </td>
          <td class="mono-cell">${escapeHtml(alts)}</td>
          <td>${escapeHtml(e.language)}</td>
          <td>${escapeHtml(e.category)}</td>
          <td>
            <button type="button" class="btn btn-ghost btn-toggle" data-action="toggle">
              ${e.enabled ? "On" : "Off"}
            </button>
          </td>
          <td class="actions-cell">
            <button type="button" class="btn btn-ghost" data-action="edit">Edit</button>
            <button type="button" class="btn btn-ghost danger-text" data-action="delete">Delete</button>
          </td>
        </tr>`;
      })
      .join("");
  }

  async function loadVocabulary() {
    showError("");
    const params = new URLSearchParams();
    if (searchEl.value.trim()) params.set("search", searchEl.value.trim());
    if (langFilter.value) params.set("language", langFilter.value);
    if (catFilter.value) params.set("category", catFilter.value);
    if (enabledOnly.checked) params.set("enabled", "true");
    const data = await fetchJson(`/api/vocabulary?${params.toString()}`);
    countEl.textContent = `${data.total} entr${data.total === 1 ? "y" : "ies"}`;
    fillFilters(data.categories || [], data.entries || []);
    renderRows(data.entries || []);
  }

  function openCreate() {
    editId.value = "";
    dialogTitle.textContent = "Add terminology";
    correctEl.value = "";
    altEl.value = "";
    languageEl.value = "Bulgarian";
    categoryEl.value = "iGaming";
    descriptionEl.value = "";
    enabledEl.checked = true;
    dialog.showModal();
  }

  function openEdit(entry) {
    editId.value = entry.id;
    dialogTitle.textContent = "Edit terminology";
    correctEl.value = entry.correct_term;
    altEl.value = (entry.alternatives || []).join(", ");
    languageEl.value = entry.language || "any";
    categoryEl.value = entry.category || "General";
    descriptionEl.value = entry.description || "";
    enabledEl.checked = !!entry.enabled;
    dialog.showModal();
  }

  async function saveEntry() {
    const payload = {
      correct_term: correctEl.value.trim(),
      alternatives: parseAlternatives(altEl.value),
      language: languageEl.value.trim() || "any",
      category: categoryEl.value.trim() || "General",
      description: descriptionEl.value.trim(),
      enabled: enabledEl.checked,
    };
    if (!payload.correct_term) {
      showError("Correct term is required");
      return;
    }
    const id = editId.value;
    if (id) {
      await fetchJson(`/api/vocabulary/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    } else {
      await fetchJson("/api/vocabulary", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    }
    dialog.close();
    await loadVocabulary();
  }

  body.addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-action]");
    if (!btn) return;
    const row = btn.closest("tr[data-id]");
    if (!row) return;
    const id = row.getAttribute("data-id");
    const action = btn.getAttribute("data-action");
    try {
      if (action === "delete") {
        if (!confirm("Delete this vocabulary entry?")) return;
        await fetchJson(`/api/vocabulary/${id}`, { method: "DELETE" });
        await loadVocabulary();
        return;
      }
      const entry = await fetchJson(`/api/vocabulary/${id}`);
      if (action === "edit") {
        openEdit(entry);
        return;
      }
      if (action === "toggle") {
        await fetchJson(`/api/vocabulary/${id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled: !entry.enabled }),
        });
        await loadVocabulary();
      }
    } catch (err) {
      showError(err.message || "Action failed");
    }
  });

  addBtn.addEventListener("click", openCreate);
  saveBtn.addEventListener("click", () => {
    saveEntry().catch((err) => showError(err.message || "Save failed"));
  });

  let debounce = null;
  function scheduleReload() {
    clearTimeout(debounce);
    debounce = setTimeout(() => {
      loadVocabulary().catch((err) => showError(err.message || "Load failed"));
    }, 200);
  }

  searchEl.addEventListener("input", scheduleReload);
  langFilter.addEventListener("change", scheduleReload);
  catFilter.addEventListener("change", scheduleReload);
  enabledOnly.addEventListener("change", scheduleReload);

  loadVocabulary().catch((err) => showError(err.message || "Load failed"));
})();
