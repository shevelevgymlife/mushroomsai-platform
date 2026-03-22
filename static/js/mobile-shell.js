/**
 * Мобильный «shell» как в приложении: жест «назад» с края, согласование с history.
 * Подключается на страницах с body.dash-ig-app (дашборд) и опционально .app-mobile-chrome.
 */
(function () {
  'use strict';

  var EDGE_PX = 28;
  var SWIPE_MIN = 56;
  var MAX_ANGLE = 0.85;

  function isMobileLayout() {
    try {
      return window.matchMedia('(max-width: 768px)').matches;
    } catch (e) {
      return window.innerWidth <= 768;
    }
  }

  function onEdgeSwipeBack() {
    if (typeof window.__dashShellSwipeBack === 'function') {
      if (window.__dashShellSwipeBack() === false) return;
    }
    if (window.history.length > 1) {
      window.history.back();
    }
  }

  function attachEdgeSwipe() {
    var startX = 0;
    var startY = 0;
    var tracking = false;

    document.addEventListener(
      'touchstart',
      function (e) {
        if (!isMobileLayout()) return;
        if (e.touches.length !== 1) return;
        var t = e.touches[0];
        startX = t.clientX;
        startY = t.clientY;
        tracking = startX <= EDGE_PX;
      },
      { passive: true }
    );

    document.addEventListener(
      'touchmove',
      function (e) {
        if (!tracking || !isMobileLayout()) return;
        var t = e.touches[0];
        var dx = t.clientX - startX;
        var dy = t.clientY - startY;
        if (dx > SWIPE_MIN && Math.abs(dy) < dx * MAX_ANGLE) {
          tracking = false;
          onEdgeSwipeBack();
        }
      },
      { passive: true }
    );

    document.addEventListener(
      'touchend',
      function () {
        tracking = false;
      },
      { passive: true }
    );
  }

  function init() {
    attachEdgeSwipe();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
