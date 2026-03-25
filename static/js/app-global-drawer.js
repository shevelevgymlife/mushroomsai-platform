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

  window.closeAppGlobalDrawer = closeAppGlobalDrawer;
  window.openAppGlobalDrawer = openAppGlobalDrawer;
  window.toggleAppGlobalDrawer = toggleAppGlobalDrawer;
  window.__appMenuClick = function () {
    toggleAppGlobalDrawer();
  };

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeAppGlobalDrawer();
  });

  /* Меню открыто сразу при заходе на страницу (после первой отрисовки) */
  document.addEventListener("DOMContentLoaded", function () {
    if (!document.getElementById("appGlobalDrawer")) return;
    requestAnimationFrame(function () {
      requestAnimationFrame(openAppGlobalDrawer);
    });
  });
})();
