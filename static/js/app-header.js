(function () {
  function isFreeRestrictedUser() {
    try {
      var plan = String(window.__APP_PLAN || "free").toLowerCase();
      var role = String(window.__APP_ROLE || "user").toLowerCase();
      if (role === "admin" || role === "moderator") return false;
      return plan === "free";
    } catch (e) {
      return false;
    }
  }
  function goTariffIfRestricted(e) {
    if (!isFreeRestrictedUser()) return false;
    if (e && e.preventDefault) e.preventDefault();
    if (e && e.stopPropagation) e.stopPropagation();
    window.location.href = "/subscriptions";
    return true;
  }
  async function refreshAppHeaderBadges() {
    try {
      var r = await fetch("/community/activity/unread-count", { credentials: "same-origin" });
      var d = await r.json();
      var act =
        typeof d.activity_total === "number"
          ? d.activity_total
          : (d.likes || 0) + (d.comments || 0) + (d.profile_likes || 0);
      var ab = document.getElementById("cpActivityBadge");
      if (ab) {
        var n = Math.max(0, parseInt(act, 10) || 0);
        ab.textContent = n > 99 ? "99+" : String(n);
        ab.classList.toggle("on", n > 0);
      }
      var mb = document.getElementById("cpMsgBadge");
      if (mb) {
        var nm = Math.max(0, d.messages || 0);
        mb.textContent = nm > 99 ? "99+" : String(nm);
        mb.classList.toggle("on", nm > 0);
      }
    } catch (e) {}
  }
  window.refreshAppHeaderBadges = refreshAppHeaderBadges;
  window.closeAppActivityPanel = function () {};

  document.addEventListener("DOMContentLoaded", function () {
    var brand = document.querySelector(".app-head-brand");
    if (brand) {
      brand.addEventListener("click", function (e) {
        goTariffIfRestricted(e);
      });
    }
    var menu = document.getElementById("appMobileMenuBtn");
    if (menu) {
      menu.addEventListener("click", function () {
        if (typeof window.__appMenuClick === "function") window.__appMenuClick();
        else location.href = "/community";
      });
    }
    if (document.getElementById("cpActivityBadge")) {
      setInterval(refreshAppHeaderBadges, 45000);
      refreshAppHeaderBadges();
    }
    var msgTab = document.getElementById("cpMsgHeadBottom");
    if (msgTab) {
      msgTab.addEventListener("click", function (e) {
        goTariffIfRestricted(e);
      });
    }

    var dmToastTimer = null;
    function showDmToastToast(t) {
      var el = document.getElementById("appDmToast");
      if (!el || !t || !t.url) return;
      el.style.display = "block";
      el.innerHTML = "";
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "app-dm-toast-inner";
      btn.setAttribute("aria-label", "Открыть диалог");
      var title = document.createElement("span");
      title.textContent = "💬 " + (t.name || "Сообщение");
      var meta = document.createElement("span");
      meta.className = "app-dm-toast-meta";
      meta.textContent = (t.snippet || "").slice(0, 120);
      btn.appendChild(title);
      btn.appendChild(meta);
      btn.addEventListener("click", function () {
        if (dmToastTimer) clearTimeout(dmToastTimer);
        el.style.display = "none";
        window.location.href = t.url;
      });
      el.appendChild(btn);
      if (dmToastTimer) clearTimeout(dmToastTimer);
      dmToastTimer = setTimeout(function () {
        el.style.display = "none";
        el.innerHTML = "";
      }, 3000);
    }
    async function pollDmInboxToast() {
      try {
        if (!window._isLoggedIn) return;
        var el = document.getElementById("appDmToast");
        if (!el) return;
        var p = window.location.pathname || "";
        if (p.indexOf("/chats") === 0) return;
        var last = parseInt(sessionStorage.getItem("dmToastLastId") || "0", 10) || 0;
        var r = await fetch("/community/messages/inbox-toast?after_id=" + last, { credentials: "same-origin" });
        var d = await r.json();
        if (!d || !d.toast) return;
        var t = d.toast;
        if (!t.id || t.id <= last) return;
        sessionStorage.setItem("dmToastLastId", String(t.id));
        showDmToastToast(t);
        if (typeof refreshAppHeaderBadges === "function") refreshAppHeaderBadges();
      } catch (e) {}
    }
    if (document.getElementById("appDmToast")) {
      setInterval(pollDmInboxToast, 12000);
      setTimeout(pollDmInboxToast, 2500);
    }
  });
})();
