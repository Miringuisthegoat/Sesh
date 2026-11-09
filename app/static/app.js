try { document.documentElement.dataset.theme = localStorage.getItem("sesh-theme") || "light"; }
catch (error) { document.documentElement.dataset.theme = "light"; }
try { document.documentElement.dataset.sidebarPinned = localStorage.getItem("sesh-sidebar-expanded") === "true" ? "true" : "false"; }
catch (error) { document.documentElement.dataset.sidebarPinned = "false"; }

document.addEventListener("submit", function (e) {
  var msg = e.target.getAttribute("data-confirm");
  if (msg && !window.confirm(msg)) { e.preventDefault(); }
});

document.addEventListener("DOMContentLoaded", function () {
  var themeToggle = document.getElementById("themeToggle");
  if (themeToggle) {
    function updateThemeToggle() {
      var isDark = document.documentElement.dataset.theme === "dark";
      themeToggle.setAttribute("aria-checked", String(isDark));
      themeToggle.setAttribute("aria-label", isDark ? "Switch to light theme" : "Switch to dark theme");
      themeToggle.setAttribute("title", isDark ? "Switch to light theme" : "Switch to dark theme");
      var label = themeToggle.querySelector(".theme-toggle-label");
      if (label) { label.textContent = isDark ? "Dark" : "Light"; }
    }

    updateThemeToggle();
    themeToggle.addEventListener("click", function () {
      var nextTheme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = nextTheme;
      try { localStorage.setItem("sesh-theme", nextTheme); }
      catch {}
      updateThemeToggle();
    });
  }

  var sidebar = document.getElementById("seshSidebar");
  var sidebarToggle = document.getElementById("sidebarExpandToggle");
  if (!sidebar || !sidebarToggle) { return; }

  var pinnedOpen = false;
  try { pinnedOpen = localStorage.getItem("sesh-sidebar-expanded") === "true"; }
  catch {}
  document.documentElement.dataset.sidebarPinned = String(pinnedOpen);

  function updateSidebarToggle() {
    var label = pinnedOpen ? "Use compact navigation" : "Keep navigation expanded";
    sidebarToggle.setAttribute("aria-pressed", String(pinnedOpen));
    sidebarToggle.setAttribute("aria-label", label);
    sidebarToggle.setAttribute("title", label);
  }

  updateSidebarToggle();
  sidebarToggle.addEventListener("click", function () {
    pinnedOpen = !pinnedOpen;
    document.documentElement.dataset.sidebarPinned = String(pinnedOpen);
    try { localStorage.setItem("sesh-sidebar-expanded", String(pinnedOpen)); }
    catch {}
    updateSidebarToggle();
  });
});
