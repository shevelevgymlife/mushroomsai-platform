(function () {
  function escHtml(t) {
    var d = document.createElement("div");
    d.textContent = t == null ? "" : String(t);
    return d.innerHTML;
  }

  async function refreshFreeAiDrawerStatus() {
    try {
      var box = document.getElementById("freeChatQuotaCard");
      if (!box) return;
      var r = await fetch("/community/ai/quota", { credentials: "same-origin" });
      if (!r.ok) return;
      var d = await r.json();
      if (!d || !d.ok) return;

      if (d.plan !== "free") {
        box.style.display = "none";
        return;
      }
      box.style.display = "block";

      var rem = Math.max(0, parseInt(d.remaining || 0, 10));
      var used = Math.max(0, parseInt(d.used || 0, 10));
      var lim = Math.max(0, parseInt(d.free_limit || 5, 10));

      var cnt = document.getElementById("freeChatQuotaCount");
      if (cnt) cnt.textContent = String(rem) + "/" + String(lim);
      var txt = document.getElementById("freeChatQuotaHint");
      if (txt) txt.textContent = "Осталось сообщений: " + String(rem);

      var warn = document.getElementById("freeChatQuotaWarn");
      if (warn) {
        if (rem <= 0) {
          warn.style.display = "block";
          warn.textContent =
            d.menu_hint ||
            "Лимит сообщений закончился. Откройте меню и купите подписку для безлимитного общения.";
        } else {
          warn.style.display = "none";
        }
      }

      var list = document.getElementById("freeChatRecentList");
      if (list) {
        var items = Array.isArray(d.last_user_messages) ? d.last_user_messages : [];
        if (!items.length) {
          list.innerHTML = '<div style="font-size:11px;color:#6a7f84;padding:4px 0">Пока нет сообщений</div>';
        } else {
          list.innerHTML = items
            .map(function (m, idx) {
              var t = escHtml(m && m.text ? m.text : "—");
              var dt = escHtml(m && m.created_at ? m.created_at : "");
              return (
                '<div style="font-size:11px;line-height:1.45;color:#d4e0e4;padding:6px 8px;border:1px solid rgba(61,212,224,.16);border-radius:8px;background:rgba(8,10,16,.45)">' +
                '<div style="opacity:.78;margin-bottom:2px">#' +
                String(used - items.length + idx + 1 > 0 ? used - items.length + idx + 1 : idx + 1) +
                (dt ? " · " + dt : "") +
                "</div>" +
                "<div>" +
                t +
                "</div></div>"
              );
            })
            .join("");
        }
      }
    } catch (e) {}
  }

  function closeAppGlobalDrawer() {
    var dr = document.getElementById("appGlobalDrawer");
    var bd = document.getElementById("appGlobalDrawerBackdrop");
    if (dr) dr.classList.remove("open");
    if (bd) bd.classList.remove("on");
    try {
      document.body.style.overflow = "";
    } catch (e) {}
  }

  function openAppGlobalDrawer() {
    try {
      if (typeof window.closeAppActivityPanel === "function") window.closeAppActivityPanel();
    } catch (e) {}
    var dr = document.getElementById("appGlobalDrawer");
    var bd = document.getElementById("appGlobalDrawerBackdrop");
    if (!dr || !bd) return;
    dr.classList.add("open");
    bd.classList.add("on");
    try { refreshFreeAiDrawerStatus(); } catch (e) {}
    try {
      document.body.style.overflow = "hidden";
    } catch (e) {}
  }

  function toggleAppGlobalDrawer() {
    try {
      if (typeof window.closeAppActivityPanel === "function") window.closeAppActivityPanel();
    } catch (e) {}
    var dr = document.getElementById("appGlobalDrawer");
    var bd = document.getElementById("appGlobalDrawerBackdrop");
    if (!dr || !bd) return;
    var willOpen = !dr.classList.contains("open");
    dr.classList.toggle("open", willOpen);
    bd.classList.toggle("on", willOpen);
    if (willOpen) {
      try { refreshFreeAiDrawerStatus(); } catch (e) {}
    }
    try {
      document.body.style.overflow = willOpen ? "hidden" : "";
    } catch (e) {}
  }

  /** Главный бот: в Telegram Mini App — сразу в чат с ботом; в браузере — переход на t.me */
  function openMainTelegramBot() {
    closeAppGlobalDrawer();
    var u = "https://t.me/mushrooms_ai_bot";
    var tw = window.Telegram && window.Telegram.WebApp;
    if (tw && typeof tw.openTelegramLink === "function") {
      try {
        tw.openTelegramLink(u);
        return;
      } catch (e) {}
    }
    window.location.href = u;
  }

  window.closeAppGlobalDrawer = closeAppGlobalDrawer;
  window.openAppGlobalDrawer = openAppGlobalDrawer;
  window.toggleAppGlobalDrawer = toggleAppGlobalDrawer;
  window.refreshFreeAiDrawerStatus = refreshFreeAiDrawerStatus;
  window.openMainTelegramBot = openMainTelegramBot;
  window.__appMenuClick = function () {
    toggleAppGlobalDrawer();
  };

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeAppGlobalDrawer();
  });
})();
