/**
 * Вход через Telegram Mini App: initData приходит в URL hash (#tgWebAppData=...).
 * Нельзя делать location.replace без сохранения hash — иначе подпись теряется.
 *
 * На части клиентов (iOS / веб) `Telegram.WebApp.initData` долго пустой, зато
 * подписанная строка уже есть в fragment `tgWebAppData` — её обязательно читаем.
 */
(function (window) {
  function extractParamFromQueryString(qs, key) {
    if (!qs) return "";
    var prefix = key + "=";
    var parts = String(qs).replace(/^[#?]/, "").split("&");
    for (var i = 0; i < parts.length; i++) {
      var p = parts[i];
      if (p.indexOf(prefix) === 0) {
        try {
          return decodeURIComponent(p.slice(prefix.length).replace(/\+/g, " "));
        } catch (e) {
          return p.slice(prefix.length);
        }
      }
    }
    return "";
  }

  function getInitDataFromUrl() {
    try {
      var h = extractParamFromQueryString(window.location.hash || "", "tgWebAppData");
      if (h && h.length) return h;
      return extractParamFromQueryString(window.location.search || "", "tgWebAppData");
    } catch (e) {
      return "";
    }
  }

  function getInitDataRaw() {
    try {
      var tg = window.Telegram && window.Telegram.WebApp;
      if (tg && tg.initData && String(tg.initData).length) {
        return String(tg.initData);
      }
      var fromUrl = getInitDataFromUrl();
      if (fromUrl && fromUrl.length) {
        return fromUrl;
      }
      var wv = window.Telegram && window.Telegram.WebView && window.Telegram.WebView.initParams;
      if (wv && wv.tgWebAppData && String(wv.tgWebAppData).length) {
        return String(wv.tgWebAppData);
      }
    } catch (e) {}
    return "";
  }

  function waitForInitData(tg, maxMs, intervalMs) {
    var deadline = Date.now() + (maxMs || 35000);
    var step = intervalMs || 80;
    return new Promise(function (resolve) {
      var resolved = false;
      function finish(val) {
        if (resolved) return;
        resolved = true;
        try {
          window.removeEventListener("hashchange", onHash);
        } catch (e) {}
        resolve(val || "");
      }
      function onHash() {
        var raw = getInitDataRaw();
        if (raw && raw.length) finish(raw);
      }
      try {
        window.addEventListener("hashchange", onHash);
      } catch (e) {}
      function tick() {
        var raw = getInitDataRaw();
        if (raw && raw.length) {
          finish(raw);
          return;
        }
        if (Date.now() >= deadline) {
          finish("");
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

        if (document.readyState === "loading") {
          await new Promise(function (r) {
            document.addEventListener("DOMContentLoaded", r, { once: true });
          });
        }
        await new Promise(function (r) {
          setTimeout(r, 150);
        });

        var initData = await waitForInitData(tg, 35000, 80);

        if (!initData) {
          var platform = null;
          var unsafeKeys = 0;
          var uid = null;
          var apiLen = 0;
          var hashHasData = false;
          try {
            platform = tg ? tg.platform : null;
            if (tg && tg.initDataUnsafe && typeof tg.initDataUnsafe === "object") {
              unsafeKeys = Object.keys(tg.initDataUnsafe).length;
            }
            uid =
              tg && tg.initDataUnsafe && tg.initDataUnsafe.user
                ? tg.initDataUnsafe.user.id
                : null;
            apiLen = tg && tg.initData ? String(tg.initData).length : 0;
            hashHasData =
              (window.location.hash || "").indexOf("tgWebAppData=") >= 0 ||
              (window.location.search || "").indexOf("tgWebAppData=") >= 0;
          } catch (e) {}
          var msg =
            "Telegram initData не найден. Откройте приложение через кнопку меню бота или по ссылке с бота (нужен параметр #tgWebAppData в адресе).<br/>" +
            "Диагностика: WebApp=" +
            (tg ? "yes" : "no") +
            ", platform=" +
            (platform || "—") +
            ", initData.len=" +
            apiLen +
            ", hash/query tgWebAppData=" +
            (hashHasData ? "yes" : "no") +
            ", initDataUnsafe.keys=" +
            unsafeKeys +
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
