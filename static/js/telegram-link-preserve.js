/**
 * В Telegram Mini App подпись initData живёт в #tgWebAppData=...
 * Любой переход по ссылке (/login, /onboarding, «подписка» и т.д.) без hash её теряет.
 * Для гостей: при клике на внутренние ссылки добавляем сохранённый fragment.
 */
(function () {
  if (typeof window === "undefined" || window._isLoggedIn) return;

  function tgFragment() {
    var h = window.location.hash || "";
    if (h.indexOf("tgWebAppData=") >= 0) return h;
    try {
      var s = sessionStorage.getItem("__tg_fragment") || "";
      if (s.indexOf("tgWebAppData=") >= 0) return s;
    } catch (e) {}
    return "";
  }

  /** Для onclick / JS-навигации: тот же URL + fragment Telegram */
  window.__tgHrefWithInitData = function (pathOrUrl) {
    var frag = tgFragment();
    if (!frag) return pathOrUrl;
    try {
      var url = new URL(pathOrUrl, window.location.origin);
      if (url.origin !== window.location.origin) return pathOrUrl;
      if ((url.href || "").indexOf("tgWebAppData=") >= 0) return pathOrUrl;
      return url.pathname + url.search + frag;
    } catch (e) {
      return pathOrUrl;
    }
  };

  function shouldAttachTgFragment(pathname) {
    var p = pathname || "";
    if (p === "/login" || p === "/login/") return true;
    if (p.indexOf("/auth/") === 0) return true;
    if (p.indexOf("/onboarding") === 0) return true;
    if (p.indexOf("/account") === 0) return true;
    return false;
  }

  document.addEventListener(
    "click",
    function (e) {
      var frag = tgFragment();
      if (!frag) return;

      var el = e.target && e.target.closest ? e.target.closest("a[href]") : null;
      if (!el || el.getAttribute("data-no-tg-preserve") === "1") return;

      var href = el.getAttribute("href");
      if (!href || href.charAt(0) === "#") return;
      if (/^javascript:/i.test(href)) return;
      if (/^mailto:/i.test(href) || /^tel:/i.test(href)) return;
      if (el.hasAttribute("download")) return;

      if (/^https?:\/\//i.test(href)) {
        try {
          if (href.indexOf(window.location.origin) !== 0) return;
        } catch (err) {
          return;
        }
      }

      try {
        var url = new URL(href, window.location.origin);
        if (url.origin !== window.location.origin) return;
        if ((url.href || "").indexOf("tgWebAppData=") >= 0) return;
        if (!shouldAttachTgFragment(url.pathname)) return;

        e.preventDefault();
        e.stopPropagation();
        window.location.href = url.pathname + url.search + frag;
      } catch (err2) {}
    },
    true
  );
})();
