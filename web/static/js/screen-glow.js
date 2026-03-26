/* Screen Glow — подсветка по периметру экрана. Данные в localStorage. */
(function () {
  var K_ON  = 'nglow_on';
  var K_CLR = 'nglow_clr';
  var K_INT = 'nglow_int';

  /* --- Создаём оверлей один раз --- */
  var overlay = document.createElement('div');
  overlay.id = 'screenGlowOverlay';
  overlay.style.cssText =
    'position:fixed;inset:0;pointer-events:none;z-index:9997;' +
    'transition:box-shadow .35s ease;border-radius:0';
  document.body.appendChild(overlay);

  function hexToRgba(hex, a) {
    if (!hex || hex.length < 7) return 'rgba(61,212,224,' + a + ')';
    var r = parseInt(hex.slice(1,3), 16);
    var g = parseInt(hex.slice(3,5), 16);
    var b = parseInt(hex.slice(5,7), 16);
    return 'rgba(' + r + ',' + g + ',' + b + ',' + a + ')';
  }

  function applyGlow() {
    var on  = localStorage.getItem(K_ON)  === 'true';
    var clr = localStorage.getItem(K_CLR) || '#3dd4e0';
    var int = Math.max(0, Math.min(100, parseInt(localStorage.getItem(K_INT) || '60')));

    if (on && int > 0) {
      var alpha  = (int / 100) * 0.85;
      var spread = Math.round(int / 14) + 2;   /* 2–9px */
      var blur   = Math.round(int / 6)  + 10;  /* 10–26px */
      overlay.style.boxShadow =
        'inset 0 0 ' + blur + 'px ' + spread + 'px ' + hexToRgba(clr, alpha);
    } else {
      overlay.style.boxShadow = 'none';
    }

    /* Обновляем все кнопки цвета в бургерах */
    var dots = document.querySelectorAll('.glow-color-dot');
    dots.forEach(function (d) {
      if (on && int > 0) {
        d.style.background = clr;
        d.style.boxShadow  = '0 0 8px 2px ' + hexToRgba(clr, .7) +
                             ',0 0 2px 1px ' + hexToRgba(clr, .4);
      } else {
        /* Неактивная — радужный градиент */
        d.style.background = 'linear-gradient(135deg,#3dd4e0,#b85fa3,#3dd4e0)';
        d.style.boxShadow  = 'none';
      }
    });

    /* Синхронизируем все тумблеры */
    var toggles = document.querySelectorAll('.glow-toggle-input');
    toggles.forEach(function (t) { t.checked = on; });
  }

  /* --- Публичное API --- */
  window.glowSetEnabled = function (val) {
    localStorage.setItem(K_ON, val ? 'true' : 'false');
    applyGlow();
  };
  window.glowSetColor = function (hex) {
    localStorage.setItem(K_CLR, hex);
    applyGlow();
  };
  window.glowSetIntensity = function (v) {
    localStorage.setItem(K_INT, String(Math.round(v)));
    applyGlow();
  };
  window.glowIsEnabled    = function () { return localStorage.getItem(K_ON) === 'true'; };
  window.glowGetColor     = function () { return localStorage.getItem(K_CLR) || '#3dd4e0'; };
  window.glowGetIntensity = function () { return parseInt(localStorage.getItem(K_INT) || '60'); };

  /* --- Применяем сразу и после DOMContentLoaded --- */
  applyGlow();
  document.addEventListener('DOMContentLoaded', applyGlow);
})();
