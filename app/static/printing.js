// Poll a print job's status while it is still moving through the queue, then reload to show the result.
(function () {
  var el = document.getElementById("job-status");
  if (!el) { return; }
  var status = el.getAttribute("data-status");
  if (["QUEUED", "CLAIMED", "DOWNLOADING", "DOWNLOADED"].indexOf(status) === -1) { return; }
  var url = el.getAttribute("data-url");
  var timer = setInterval(function () {
    fetch(url, { credentials: "same-origin", headers: { "Accept": "application/json" } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d && d.status !== status) { clearInterval(timer); window.location.reload(); } })
      .catch(function () { /* transient network error: keep trying */ });
  }, 3000);
})();
