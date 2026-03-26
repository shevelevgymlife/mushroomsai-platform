(function () {
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
  window.openMainTelegramBot = openMainTelegramBot;
  window.__appMenuClick = function () {
    toggleAppGlobalDrawer();
  };

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeAppGlobalDrawer();
  });
})();
