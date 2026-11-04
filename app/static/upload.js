// Drag-and-drop for the upload form. The real <input type="file"> stays the source of truth, so the form works without JS too.
(function () {
  var zone = document.getElementById("dropzone");
  if (!zone) { return; }
  var input = document.getElementById("file");
  var prompt = document.getElementById("dropzone-prompt");
  var fileBox = document.getElementById("dropzone-file");
  var nameEl = document.getElementById("dropzone-name");
  var sizeEl = document.getElementById("dropzone-size");
  var clearBtn = document.getElementById("dropzone-clear");
  var errorEl = document.getElementById("dropzone-error");
  var submitBtn = document.getElementById("upload-submit");
  var allowed = zone.getAttribute("data-extensions").split(",");
  var maxBytes = parseInt(zone.getAttribute("data-max-mb"), 10) * 1024 * 1024;

  function showError(msg) {
    errorEl.textContent = msg || "";
    errorEl.hidden = !msg;
  }

  function fmtSize(n) {
    return n >= 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB";
  }

  function reset() {
    input.value = "";
    fileBox.hidden = true;
    prompt.hidden = false;
  }

  function show(file) {
    nameEl.textContent = file.name;
    sizeEl.textContent = fmtSize(file.size);
    prompt.hidden = true;
    fileBox.hidden = false;
  }

  // Client-side check is a convenience only; the server validates again.
  function problem(file) {
    var ext = (file.name.split(".").pop() || "").toLowerCase();
    if (allowed.indexOf(ext) === -1) { return "This file type is not supported. Use " + allowed.map(function (e) { return "." + e; }).join(", ") + "."; }
    if (file.size === 0) { return "The file is empty."; }
    if (file.size > maxBytes) { return "The file is larger than the " + (maxBytes / 1048576) + " MB limit."; }
    return null;
  }

  function accept(files) {
    showError("");
    if (!files || !files.length) { return; }
    if (files.length > 1) { showError("Upload one file at a time. Using the first one."); }
    var file = files[0];
    var msg = problem(file);
    if (msg) { reset(); showError(msg); return; }
    if (input.files !== files) {
      var dt = new DataTransfer();
      dt.items.add(file);
      input.files = dt.files;
    }
    show(file);
  }

  input.addEventListener("change", function () { accept(input.files); });
  clearBtn.addEventListener("click", function () { showError(""); reset(); });

  var depth = 0;  // dragenter/dragleave fire for child elements too
  zone.addEventListener("dragenter", function (e) { e.preventDefault(); depth++; zone.classList.add("is-dragover"); });
  zone.addEventListener("dragover", function (e) { e.preventDefault(); if (e.dataTransfer) { e.dataTransfer.dropEffect = "copy"; } });
  zone.addEventListener("dragleave", function () { depth = Math.max(0, depth - 1); if (!depth) { zone.classList.remove("is-dragover"); } });
  zone.addEventListener("drop", function (e) {
    e.preventDefault();
    depth = 0;
    zone.classList.remove("is-dragover");
    if (e.dataTransfer && e.dataTransfer.files.length) { accept(e.dataTransfer.files); }
  });

  // A miss-drop outside the zone must not make the browser open the file and leave the page.
  ["dragover", "drop"].forEach(function (type) {
    window.addEventListener(type, function (e) { if (!zone.contains(e.target)) { e.preventDefault(); } });
  });

  input.form.addEventListener("submit", function () {
    if (submitBtn && input.files && input.files.length) {
      submitBtn.disabled = true;
      submitBtn.textContent = "Uploading…";
    }
  });
})();
