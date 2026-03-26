(function () {
  function syncFromHash() {
    try {
      var path = (window.location.pathname || "").replace(/\/$/, "") || "/";
      var uid = (window.__APP_EFF_UID == null || window.__APP_EFF_UID === "")
        ? "0"
        : String(parseInt(window.__APP_EFF_UID, 10) || 0);
      var bar = document.getElementById("appUniTabbar");
      if (!bar) return;
      var tabs = bar.querySelectorAll(".app-uni-tab");
      tabs.forEach(function (t) {
        t.classList.remove("app-uni-tab--on");
      });
      if (path === "/community" || path === "/community/") {
        var feed = bar.querySelector('.app-uni-tab[data-app-tab="feed"]');
        if (feed) feed.classList.add("app-uni-tab--on");
        return;
      }
      if (path.indexOf("/community/members") === 0) {
        var search = bar.querySelector('.app-uni-tab[data-app-tab="search"]');
        if (search) search.classList.add("app-uni-tab--on");
        return;
      }
      if (path.indexOf("/community/profile/") === 0) {
        var segs = path.split("/").filter(Boolean);
        var viewed = segs.length >= 3 ? segs[2] : "";
        if (viewed === uid) {
          var profile = bar.querySelector('.app-uni-tab[data-app-tab="profile"]');
          if (profile) profile.classList.add("app-uni-tab--on");
        }
        return;
      }
      if (path === "/community/post/new") {
        var post = bar.querySelector('.app-uni-tab[data-app-tab="post"]');
        if (post) post.classList.add("app-uni-tab--on");
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
