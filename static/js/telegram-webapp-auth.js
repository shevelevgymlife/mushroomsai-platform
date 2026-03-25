/**
 * Вход через Telegram Mini App: initData приходит в URL hash (#tgWebAppData=...).
 * Нельзя делать location.replace без сохранения hash — иначе подпись теряется.
 */
(function (window) {
  function getInitDataRaw() {
    try {
      var tg = window.Telegram && window.Telegram.WebApp;
      if (tg && tg.initData && String(tg.initData).length) {
        return String(tg.initData);
      }
      var wv = window.Telegram && window.Telegram.WebView && window.Telegram.WebView.initParams;
      if (wv && wv.tgWebAppData && String(wv.tgWebAppData).length) {
        return String(wv.tgWebAppData);
      }
    } catch (e) {}
    return "";
  }

  function waitForInitData(tg, maxMs, intervalMs) {
    var deadline = Date.now() + (maxMs || 20000);
    var step = intervalMs || 80;
    return new Promise(function (resolve) {
      function tick() {
        var raw = getInitDataRaw();
        if (raw && raw.length) {
          resolve(raw);
          return;
        }
        if (Date.now() >= deadline) {
          resolve("");
          return;
        }
        setTimeout(tick, step);
      }
      if (tg && typeof tg.ready === "function") {
        try {
          var r = tg.ready();
          if (r && typeof r.then === "function") {
            r.then(function () {
              tick();
            }).catch(function () {
              tick();
            });
            return;
          }
        } catch (e) {}
      }
      tick();
    });
  }

  /**
   * @param {{ nextPath: string, onError: function(string), onSuccess: function(string) }} opts
   */
  window.runTelegramWebAppAuth = function (opts) {
    opts = opts || {};
    if (window.__tgWebAppAuthStarted) {
      return Promise.resolve();
    }
    window.__tgWebAppAuthStarted = true;
    var next = opts.nextPath || "/dashboard";
    var onError = opts.onError || function () {};
    var onSuccess = opts.onSuccess || function () {};

    return (async function () {
      try {
        var tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
        if (tg && typeof tg.ready === "function") {
          try {
            tg.ready();
          } catch (e) {}
        }
        if (tg && typeof tg.expand === "function") {
          try {
            tg.expand();
          } catch (e) {}
        }

        var initData = await waitForInitData(tg, 22000, 80);

        if (!initData) {
          var platform = null;
          var hasUnsafe = false;
          var uid = null;
          try {
            platform = tg ? tg.platform : null;
            hasUnsafe = !!(tg && tg.initDataUnsafe);
            uid =
              tg && tg.initDataUnsafe && tg.initDataUnsafe.user
                ? tg.initDataUnsafe.user.id
                : null;
          } catch (e) {}
          var msg =
            "Telegram initData не найден. Откройте приложение через кнопку меню бота или по ссылке с бота (нужен параметр #tgWebAppData в адресе).<br/>" +
            "Диагностика: WebApp=" +
            (tg ? "yes" : "no") +
            ", platform=" +
            (platform || "—") +
            ", initDataUnsafe=" +
            (hasUnsafe ? "yes" : "no") +
            ", user.id=" +
            (uid != null ? uid : "—");
          window.__tgWebAppAuthStarted = false;
          onError(msg);
          return;
        }

        var resp = await fetch("/auth/telegram/webapp/callback", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({ initData: initData, next: next }),
        });

        var data = await resp.json().catch(function () {
          return {};
        });
        if (!resp.ok || !data || !data.ok) {
          window.__tgWebAppAuthStarted = false;
          onError((data && data.error) || "Ошибка " + resp.status);
          return;
        }
        var redirectTo = data.redirect || "/dashboard";
        onSuccess(redirectTo);
        window.location.href = redirectTo;
      } catch (e) {
        window.__tgWebAppAuthStarted = false;
        onError(String(e && e.message ? e.message : e));
      }
    })();
  };
})(window);
