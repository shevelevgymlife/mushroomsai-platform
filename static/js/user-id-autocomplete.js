/**
 * Поля ввода user id: формат @id, выпадающий список при фокусе и при вводе.
 * Класс: nf-user-id-field (опционально nf-user-id-bulk для списков через запятую).
 * data-nf-user-id-api — переопределить URL (иначе /admin/... или /community/users/mention-suggest).
 * data-nf-user-id-hidden — id скрытого input: при выборе пишется числовой id, в видимое — @id.
 * Элементы с id giftUserSearch оставляем вне автоинициализации (свой UI на /subscriptions).
 */
(function () {
  var _dd = null;
  var _activeEl = null;
  var _bound = new WeakSet();
  var _debounce = null;
  var _seq = 0;
  var _items = [];
  var _hi = -1;

  function esc(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  function escAttr(s) {
    return String(s || "").replace(/&/g, "&amp;").replace(/"/g, "&quot;");
  }

  function defaultApi() {
    var p = (location.pathname || "").toLowerCase();
    if (p.indexOf("/admin") === 0) return "/admin/users/mention-suggest";
    return "/community/users/mention-suggest";
  }

  function apiFor(el) {
    var o = (el.getAttribute("data-nf-user-id-api") || "").trim();
    return o || defaultApi();
  }

  function isBulk(el) {
    return el.classList && el.classList.contains("nf-user-id-bulk");
  }

  function shouldSkip(el) {
    if (!el || el.id === "giftUserSearch") return true;
    if (el.getAttribute("data-nf-user-id-skip") === "1") return true;
    return false;
  }

  /** Запрос для API: без ведущих @ */
  function queryFromValue(el) {
    var v = (el.value || "").trim().replace(/\uff20/g, "@");
    if (!isBulk(el)) {
      v = v.replace(/^@+/, "").trim();
      return v.length > 80 ? v.slice(0, 80) : v;
    }
    var pos =
      typeof el.selectionStart === "number" ? el.selectionStart : v.length;
    var before = v.slice(0, pos);
    var lastComma = Math.max(before.lastIndexOf(","), before.lastIndexOf(";"));
    var tail = (lastComma < 0 ? before : before.slice(lastComma + 1)).trim();
    tail = tail.replace(/^@+/, "").trim();
    return tail.length > 80 ? tail.slice(0, 80) : tail;
  }

  function ensureDd() {
    if (_dd) return _dd;
    _dd = document.createElement("div");
    _dd.id = "nf-user-id-dd";
    _dd.setAttribute("role", "listbox");
    _dd.setAttribute("aria-label", "Пользователи");
    _dd.style.cssText =
      "display:none;position:fixed;z-index:2147483001;min-width:240px;max-width:min(94vw,360px);max-height:min(48vh,320px);overflow:auto;border-radius:16px;border:1px solid rgba(61,212,224,.45);background:rgba(10,12,18,.98);box-shadow:0 20px 56px rgba(0,0,0,.6);padding:8px;pointer-events:auto;";
    document.body.appendChild(_dd);
    _dd.addEventListener("mousedown", function (e) {
      e.preventDefault();
    });
    return _dd;
  }

  function hide() {
    if (_dd) _dd.style.display = "none";
    _activeEl = null;
    _items = [];
    _hi = -1;
  }

  function positionUnder(el) {
    var dd = ensureDd();
    var r = el.getBoundingClientRect();
    var w = Math.min(360, Math.max(260, r.width, 280));
    dd.style.width = w + "px";
    var left = Math.max(8, Math.min(r.left, window.innerWidth - w - 8));
    var top = r.bottom + 6;
    var maxH = Math.min(320, window.innerHeight - top - 12);
    maxH = Math.max(120, maxH);
    dd.style.maxHeight = maxH + "px";
    dd.style.left = left + "px";
    dd.style.top = top + "px";
  }

  function render(users) {
    var dd = ensureDd();
    if (!users || !users.length) {
      dd.innerHTML =
        '<div style="padding:14px 12px;font-size:13px;color:#94a3b8;text-align:center">Никого не найдено — @ и цифры id или имя</div>';
      dd.style.display = "block";
      positionUnder(_activeEl);
      return;
    }
    dd.innerHTML = users
      .map(function (u, idx) {
        var av = (u.avatar || "").trim();
        var avH = av
          ? '<img src="' +
            escAttr(av) +
            '" alt="" loading="lazy" style="width:40px;height:40px;border-radius:50%;object-fit:cover;border:2px solid rgba(61,212,224,.4);flex-shrink:0">'
          : '<div style="width:40px;height:40px;border-radius:50%;background:linear-gradient(145deg,#1a1f2e,#0f1218);display:flex;align-items:center;justify-content:center;font-size:18px;flex-shrink:0;border:1px solid rgba(61,212,224,.25)">🍄</div>';
        return (
          '<button type="button" role="option" data-idx="' +
          idx +
          '" class="nf-uid-dd-item" style="display:flex;width:100%;align-items:center;gap:12px;padding:10px 12px;margin:0 0 4px;border:none;border-radius:12px;background:rgba(255,255,255,.04);color:#e8eaef;cursor:pointer;text-align:left;font-size:14px;box-sizing:border-box">' +
          avH +
          '<span style="min-width:0;flex:1;overflow:hidden"><span style="font-weight:800;color:#67e8f9;font-size:15px">@' +
          u.id +
          '</span><br><span style="font-size:13px;color:#cbd5e1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;display:block;max-width:100%">' +
          esc(u.name || "Участник") +
          "</span></span></button>"
        );
      })
      .join("");
    dd.querySelectorAll(".nf-uid-dd-item").forEach(function (btn) {
      btn.addEventListener("mouseenter", function () {
        var ix = parseInt(btn.getAttribute("data-idx"), 10);
        if (!isNaN(ix)) {
          _hi = ix;
          paintHi();
        }
      });
      btn.addEventListener("click", function () {
        var ix = parseInt(btn.getAttribute("data-idx"), 10);
        if (!isNaN(ix) && _items[ix]) pick(_activeEl, _items[ix].id);
      });
    });
    dd.style.display = "block";
    _hi = 0;
    paintHi();
    positionUnder(_activeEl);
  }

  function paintHi() {
    if (!_dd) return;
    _dd.querySelectorAll(".nf-uid-dd-item").forEach(function (btn, j) {
      if (j === _hi) {
        btn.style.background = "rgba(61,212,224,.18)";
        btn.style.outline = "1px solid rgba(61,212,224,.45)";
      } else {
        btn.style.background = "rgba(255,255,255,.04)";
        btn.style.outline = "none";
      }
    });
  }

  function pick(el, userId) {
    var id = parseInt(userId, 10);
    if (!el || !id) return hide();
    var hidId = (el.getAttribute("data-nf-user-id-hidden") || "").trim();
    var hid = hidId ? document.getElementById(hidId) : null;
    if (hid) {
      hid.value = String(id);
      el.value = "@" + id;
      try {
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
      } catch (e) {}
      hide();
      try {
        el.focus();
      } catch (e2) {}
      return;
    }
    if (isBulk(el)) {
      var v = (el.value || "").trim();
      if (!v) el.value = "@" + id + ", ";
      else if (/[;,]\s*$/.test(v)) el.value = v + " @" + id + ", ";
      else el.value = v + ", @" + id + ", ";
      var np = el.value.length;
      if (typeof el.setSelectionRange === "function") el.setSelectionRange(np, np);
    } else {
      el.value = "@" + id;
      if (typeof el.setSelectionRange === "function") {
        var L = el.value.length;
        el.setSelectionRange(L, L);
      }
    }
    try {
      el.dispatchEvent(new Event("input", { bubbles: true }));
    } catch (e) {}
    hide();
    try {
      el.focus();
    } catch (e2) {}
  }

  function fetchSuggest(el, q) {
    var url = apiFor(el) + "?q=" + encodeURIComponent(q || "");
    var my = ++_seq;
    fetch(url, { credentials: "same-origin" })
      .then(function (r) {
        return r.json().then(function (d) {
          return { ok: r.ok, status: r.status, d: d };
        });
      })
      .then(function (pack) {
        if (my !== _seq || el !== _activeEl) return;
        if (!pack.ok) {
          var dd = ensureDd();
          dd.innerHTML =
            '<div style="padding:14px;font-size:13px;color:#f87171;text-align:center">' +
            (pack.status === 401 ? "Войдите в аккаунт" : "Не удалось загрузить список") +
            "</div>";
          dd.style.display = "block";
          positionUnder(el);
          return;
        }
        var d = pack.d;
        if (!d || !Array.isArray(d.users)) {
          ensureDd().innerHTML =
            '<div style="padding:14px;font-size:13px;color:#f87171">Неверный ответ</div>';
          ensureDd().style.display = "block";
          positionUnder(el);
          return;
        }
        _items = d.users;
        render(d.users);
      })
      .catch(function () {
        if (my !== _seq || el !== _activeEl) return;
        var dd = ensureDd();
        dd.innerHTML =
          '<div style="padding:14px;font-size:13px;color:#f87171;text-align:center">Ошибка сети</div>';
        dd.style.display = "block";
        positionUnder(el);
      });
  }

  function schedule(el) {
    if (shouldSkip(el)) return;
    _activeEl = el;
    var q = queryFromValue(el);
    ensureDd().innerHTML =
      '<div style="padding:12px;font-size:13px;color:#94a3b8;text-align:center">Загрузка…</div>';
    ensureDd().style.display = "block";
    positionUnder(el);
    if (_debounce) clearTimeout(_debounce);
    _debounce = setTimeout(function () {
      _debounce = null;
      fetchSuggest(el, q);
    }, 120);
  }

  function onKeydown(ev) {
    var el = ev.target;
    if (!el.classList || !el.classList.contains("nf-user-id-field") || shouldSkip(el)) return;
    if (!_dd || _dd.style.display === "none") return;
    if (ev.key === "Escape") {
      ev.preventDefault();
      hide();
      return;
    }
    if (ev.key === "ArrowDown") {
      ev.preventDefault();
      if (_items.length) {
        _hi = Math.min(_items.length - 1, _hi + 1);
        paintHi();
      }
      return;
    }
    if (ev.key === "ArrowUp") {
      ev.preventDefault();
      if (_items.length) {
        _hi = Math.max(0, _hi - 1);
        paintHi();
      }
      return;
    }
    if (ev.key === "Enter" && _items.length && _hi >= 0) {
      ev.preventDefault();
      pick(el, _items[_hi].id);
    }
  }

  function onBlurEl() {
    setTimeout(function () {
      var ae = document.activeElement;
      if (ae && _dd && _dd.contains(ae)) return;
      hide();
    }, 200);
  }

  function bindEl(el) {
    if (!el || _bound.has(el) || shouldSkip(el)) return;
    _bound.add(el);
    el.addEventListener("focus", function () {
      schedule(el);
    });
    el.addEventListener("input", function () {
      schedule(el);
    });
    el.addEventListener("keydown", onKeydown, true);
    el.addEventListener("blur", onBlurEl);
  }

  function normalizeSubmitValue(el) {
    if (isBulk(el)) {
      var t = (el.value || "").replace(/\uff20/g, "@");
      var parts = [];
      (t.match(/@?\d+/g) || []).forEach(function (x) {
        var n = String(x).replace(/^@+/, "");
        if (/^\d+$/.test(n)) parts.push(n);
      });
      el.value = parts.join(", ");
    } else {
      var s = (el.value || "").trim().replace(/\uff20/g, "@");
      while (s.startsWith("@")) s = s.slice(1).trim();
      if (/^\d+$/.test(s)) el.value = s;
    }
  }

  function scan() {
    try {
      document.querySelectorAll(".nf-user-id-field").forEach(bindEl);
    } catch (e) {}
  }

  document.addEventListener(
    "submit",
    function (ev) {
      var form = ev.target;
      if (!form || !form.querySelectorAll) return;
      try {
        form.querySelectorAll(".nf-user-id-field").forEach(normalizeSubmitValue);
      } catch (e) {}
    },
    true
  );

  window.NFInitUserIdAutocomplete = scan;

  function boot() {
    scan();
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  var mo = new MutationObserver(function () {
    scan();
  });
  if (document.documentElement) {
    mo.observe(document.documentElement, { childList: true, subtree: true });
  }
  window.addEventListener("resize", function () {
    if (_activeEl && _dd && _dd.style.display !== "none") positionUnder(_activeEl);
  });
})();
