try { document.documentElement.dataset.theme = localStorage.getItem("sesh-theme") || "light"; }
catch (error) { document.documentElement.dataset.theme = "light"; }

document.addEventListener("submit", function (e) {
  var msg = e.target.getAttribute("data-confirm");
  if (msg && !window.confirm(msg)) { e.preventDefault(); }
});

document.addEventListener("DOMContentLoaded", function () {
  var themeToggle = document.getElementById("themeToggle");
  if (!themeToggle) { return; }

  function updateThemeToggle() {
    var isDark = document.documentElement.dataset.theme === "dark";
    themeToggle.setAttribute("aria-checked", String(isDark));
    themeToggle.setAttribute("aria-label", isDark ? "Switch to light theme" : "Switch to dark theme");
    themeToggle.setAttribute("title", isDark ? "Switch to light theme" : "Switch to dark theme");
    themeToggle.querySelector(".theme-toggle-label").textContent = isDark ? "Dark" : "Light";
  }

  updateThemeToggle();
  themeToggle.addEventListener("click", function () {
    var nextTheme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = nextTheme;
    try { localStorage.setItem("sesh-theme", nextTheme); }
    catch {}
    updateThemeToggle();
  });
});
