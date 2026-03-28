/**
 * Подставляет локальное время пользователя для меток data-nf-local-utc (UTC ISO).
 */
(function () {
  function formatAll(root) {
    var scope = root || document;
    var opts = {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    };
    scope.querySelectorAll("[data-nf-local-utc]").forEach(function (el) {
      var s = el.getAttribute("data-nf-local-utc");
      if (!s) return;
      var x = new Date(s);
      if (isNaN(x.getTime())) return;
      el.textContent = x.toLocaleString(undefined, opts);
    });
  }

  window.formatNfNotificationTimes = formatAll;

  function run() {
    formatAll(document);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", run);
  } else {
    run();
  }
})();
