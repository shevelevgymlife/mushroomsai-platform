(function () {
  function syncFromHash() {
    try {
      var path = (window.location.pathname || "").replace(/\/$/, "") || "/";
      var h = (window.location.hash || "").replace(/^#/, "");
      if (path !== "/dashboard") return;
      var bar = document.getElementById("appUniTabbar");
      if (!bar) return;
      var tabs = bar.querySelectorAll(".app-uni-tab");
      tabs.forEach(function (t) {
        t.classList.remove("app-uni-tab--on");
      });
      if (h === "search" || h === "feed" || h === "shop") {
        var sel = bar.querySelector('.app-uni-tab[data-app-tab="' + h + '"]');
        if (sel) sel.classList.add("app-uni-tab--on");
      } else if (!h || h === "me" || h === "home") {
        var feed = bar.querySelector('.app-uni-tab[data-app-tab="feed"]');
        if (feed) feed.classList.add("app-uni-tab--on");
      }
    } catch (e) {}
  }
  window.addEventListener("hashchange", syncFromHash);
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", syncFromHash);
  } else {
    syncFromHash();
  }
})();
