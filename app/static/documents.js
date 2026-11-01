// Poll conversion status while a document is waiting or converting, then reload to show the result.
(function () {
  var el = document.getElementById("doc-status");
  if (!el) { return; }
  var status = el.getAttribute("data-status");
  if (status !== "QUEUED" && status !== "PROCESSING" && status !== "UPLOADED") { return; }
  var url = el.getAttribute("data-url");
  var timer = setInterval(function () {
    fetch(url, { credentials: "same-origin", headers: { "Accept": "application/json" } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (d && d.status !== status) { clearInterval(timer); window.location.reload(); }
      })
      .catch(function () { /* transient network error: keep trying */ });
  }, 2500);
})();
