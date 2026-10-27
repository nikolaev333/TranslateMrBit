(() => {
  const toggle = document.getElementById("menu-toggle");
  const sidebar = document.getElementById("app-sidebar");
  const backdrop = document.getElementById("sidebar-backdrop");
  if (!toggle || !sidebar) return;

  function setOpen(open) {
    document.body.classList.toggle("nav-open", open);
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    if (backdrop) backdrop.hidden = !open;
  }

  toggle.addEventListener("click", () => {
    setOpen(!document.body.classList.contains("nav-open"));
  });
  if (backdrop) {
    backdrop.addEventListener("click", () => setOpen(false));
  }
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape") setOpen(false);
  });
})();
