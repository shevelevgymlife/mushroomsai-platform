/**
 * Оформление по периметру («Вид»): window.__SCREEN_RIM → #app-screen-rim.
 * w — ширина свечения (0.05…1): от тонкой кромки до широкого ореола.
 */
(function () {
  function buildInsetShadow(r, g, b, strength, widthW) {
    var s = Math.max(0.05, Math.min(1, Number(strength) || 0.55));
    var wf = Math.max(0.05, Math.min(1, Number(widthW) != null ? Number(widthW) : 0.35));
    var edge, b1, b2;
    if (wf <= 0.35) {
      var t = wf / 0.35;
      edge = 0.35 + t * 0.65;
      b1 = Math.round(2 + t * 10);
      b2 = Math.round(4 + t * 24);
    } else {
      var u = (wf - 0.35) / 0.65;
      edge = 1 + u * 5.5;
      b1 = Math.round(12 + u * 52);
      b2 = Math.round(28 + u * 150);
    }
    var a1 = 0.5 * s;
    var a2 = 0.32 * s;
    var a3 = 0.14 * s;
    return (
      "inset 0 0 0 " +
      edge.toFixed(2) +
      "px rgba(" +
      r +
      "," +
      g +
      "," +
      b +
      "," +
      a1 +
      "), inset 0 0 " +
      b1 +
      "px rgba(" +
      r +
      "," +
      g +
      "," +
      b +
      "," +
      a2 +
      "), inset 0 0 " +
      b2 +
      "px rgba(" +
      r +
      "," +
      g +
      "," +
      b +
      "," +
      a3 +
      ")"
    );
  }

  function applyRim(cfg) {
    var el = document.getElementById("app-screen-rim");
    if (!el || !cfg) return;
    var r = cfg.r | 0,
      g = cfg.g | 0,
      b = cfg.b | 0;
    var s = cfg.s != null ? cfg.s : 0.55;
    var w = cfg.w != null ? cfg.w : 0.35;
    el.classList.toggle("is-on", !!cfg.on);
    el.classList.toggle("is-off", !cfg.on);
    el.style.boxShadow = cfg.on ? buildInsetShadow(r, g, b, s, w) : "none";

    document.documentElement.style.setProperty("--rim-r", String(r));
    document.documentElement.style.setProperty("--rim-g", String(g));
    document.documentElement.style.setProperty("--rim-b", String(b));
    var r2 = Math.round(r * 0.55 + 120),
      g2 = Math.round(g * 0.4 + 80),
      b2 = Math.round(b * 0.65 + 60);
    document.documentElement.style.setProperty("--rim-r2", String(Math.min(255, r2)));
    document.documentElement.style.setProperty("--rim-g2", String(Math.min(255, g2)));
    document.documentElement.style.setProperty("--rim-b2", String(Math.min(255, b2)));

    document.querySelectorAll(".js-drawer-rim-toggle").forEach(function (inp) {
      if (inp.type === "checkbox") inp.checked = !!cfg.on;
    });
    document.querySelectorAll(".rim-palette-shimmer").forEach(function (btn) {
      btn.style.setProperty("--rim-r", String(r));
      btn.style.setProperty("--rim-g", String(g));
      btn.style.setProperty("--rim-b", String(b));
      btn.style.setProperty("--rim-r2", String(Math.min(255, r2)));
      btn.style.setProperty("--rim-g2", String(Math.min(255, g2)));
      btn.style.setProperty("--rim-b2", String(Math.min(255, b2)));
    });
  }

  function mergeCfg(patch) {
    var base = window.__SCREEN_RIM || { on: false, r: 61, g: 212, b: 224, s: 0.55, w: 0.35 };
    var out = {
      on: patch.on != null ? !!patch.on : !!base.on,
      r: patch.r != null ? patch.r | 0 : base.r | 0,
      g: patch.g != null ? patch.g | 0 : base.g | 0,
      b: patch.b != null ? patch.b | 0 : base.b | 0,
      s: patch.s != null ? +patch.s : base.s != null ? +base.s : 0.55,
      w: patch.w != null ? +patch.w : base.w != null ? +base.w : 0.35,
    };
    out.w = Math.max(0.05, Math.min(1, out.w));
    return out;
  }

  /** Ключ LS на пользователя — иначе при смене аккаунта на том же устройстве подтягивались чужие настройки. */
  function rimStorageKey() {
    try {
      var uid = window.__APP_EFF_UID;
      if (uid != null && uid !== "") return "screen_rim_v1_u" + String(uid);
    } catch (e) {}
    return "screen_rim_v1";
  }

  function persistRimLs(cfg) {
    try {
      localStorage.setItem(rimStorageKey(), JSON.stringify(cfg));
    } catch (e) {}
  }

  async function saveRim(patch) {
    var cfg = mergeCfg(patch);
    window.__SCREEN_RIM = cfg;
    applyRim(cfg);
    persistRimLs(cfg);
    try {
      var resp = await fetch("/account/screen-rim", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify(cfg),
      });
      var data = await resp.json().catch(function () {
        return {};
      });
      if (data && data.screen_rim) {
        window.__SCREEN_RIM = data.screen_rim;
        applyRim(data.screen_rim);
        persistRimLs(data.screen_rim);
      }
    } catch (e) {}
  }

  window.applyScreenRimFromPrefs = applyRim;
  window.saveScreenRimPrefs = saveRim;

  document.addEventListener("DOMContentLoaded", function () {
    if (typeof window.__SCREEN_RIM === "undefined" || !window.__SCREEN_RIM) return;
    // Источник истины — БД (подставлено в шаблоне для текущего аккаунта). Старый общий LS
    // screen_rim_v1 ломал смену пользователя на одном устройстве.
    var serverCfg = mergeCfg(window.__SCREEN_RIM);
    window.__SCREEN_RIM = serverCfg;
    applyRim(serverCfg);
    persistRimLs(serverCfg);

    document.querySelectorAll(".js-drawer-rim-toggle").forEach(function (inp) {
      inp.addEventListener("change", function () {
        saveRim({ on: inp.checked });
      });
    });
  });
})();
