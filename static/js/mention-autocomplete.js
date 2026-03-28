/**
 * Подсказки @упоминание: после @ — топ по популярности; далее фильтр по префиксу числового id.
 * Поля: class="nf-mention-field" (+ опционально data-mention-bound="1" чтобы не дублировать).
 */
(function () {
  var API = "/community/users/mention-suggest";
  var debounceMs = 160;
  var _dd = null;
  var _activeEl = null;
  var _state = null;
  var _items = [];
  var _hi = -1;
  var _debounce = null;
  var _bound = new WeakSet();
  var _reqSeq = 0;

  function ensureDd() {
    if (_dd) return _dd;
    _dd = document.createElement("div");
    _dd.id = "nf-mention-dd";
    _dd.setAttribute("role", "listbox");
    _dd.style.cssText =
      "display:none;position:fixed;z-index:101500;min-width:220px;max-width:min(92vw,320px);max-height:min(42vh,280px);overflow:auto;border-radius:14px;border:1px solid rgba(61,212,224,.35);background:rgba(12,14,20,.97);box-shadow:0 16px 48px rgba(0,0,0,.55);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);padding:6px;";
    document.body.appendChild(_dd);
    _dd.addEventListener("mousedown", function (e) {
      e.preventDefault();
    });
    return _dd;
  }

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

  /** @returns {{ at: number, digits: string, pos: number } | null} */
  function getMentionState(el) {
    var v = el.value;
    var pos =
      typeof el.selectionStart === "number" ? el.selectionStart : v.length;
    if (pos < 1) return null;
    var i = pos - 1;
    while (i >= 0 && /\d/.test(v.charAt(i))) i--;
    if (i < 0 || v.charAt(i) !== "@") return null;
    var chunk = v.slice(i + 1, pos);
    if (!/^\d*$/.test(chunk)) return null;
    return { at: i, digits: chunk, pos: pos };
  }

  function hide() {
    if (_dd) _dd.style.display = "none";
    _activeEl = null;
    _state = null;
    _items = [];
    _hi = -1;
  }

  function positionDd(el) {
    var dd = ensureDd();
    dd.style.position = "fixed";
    var r = el.getBoundingClientRect();
    dd.style.top = r.bottom + 4 + "px";
    dd.style.left = r.left + "px";
    dd.style.width = Math.max(220, Math.min(r.width, 320)) + "px";
  }

  function render(users) {
    var dd = ensureDd();
    if (!users || !users.length) {
      _items = [];
      _hi = -1;
      dd.innerHTML =
        '<div style="padding:12px 10px;font-size:12px;color:#888;text-align:center">Никого не найдено</div>';
      dd.style.display = "block";
      return;
    }
    dd.innerHTML = users
      .map(function (u, idx) {
        var av = (u.avatar || "").trim();
        var avH = av
          ? '<img src="' +
            escAttr(av) +
            '" alt="" style="width:32px;height:32px;border-radius:50%;object-fit:cover;border:1px solid rgba(61,212,224,.35)">'
          : '<div style="width:32px;height:32px;border-radius:50%;background:#1a1a1a;display:flex;align-items:center;justify-content:center;font-size:12px">🍄</div>';
        return (
          '<button type="button" role="option" data-idx="' +
          idx +
          '" class="nf-mention-dd-item" style="display:flex;width:100%;align-items:center;gap:10px;padding:8px 10px;border:none;border-radius:10px;background:transparent;color:#e8eaef;cursor:pointer;text-align:left;font-size:13px">' +
          avH +
          '<span style="min-width:0;flex:1"><span style="font-weight:700;color:#7dd3fc">@' +
          u.id +
          '</span> <span style="color:#cbd5e1">' +
          esc(u.name || "Участник") +
          "</span></span></button>"
        );
      })
      .join("");
    dd.querySelectorAll(".nf-mention-dd-item").forEach(function (btn) {
      btn.addEventListener("mouseenter", function () {
        var ix = parseInt(btn.getAttribute("data-idx"), 10);
        if (!isNaN(ix)) setHighlight(ix);
      });
      btn.addEventListener("click", function () {
        var ix = parseInt(btn.getAttribute("data-idx"), 10);
        if (!isNaN(ix) && _items[ix]) pick(_items[ix].id);
      });
    });
    dd.style.display = "block";
    _hi = 0;
    paintHi();
  }

  function setHighlight(ix) {
    _hi = ix;
    paintHi();
  }

  function paintHi() {
    if (!_dd) return;
    _dd.querySelectorAll(".nf-mention-dd-item").forEach(function (btn, j) {
      if (j === _hi) {
        btn.style.background = "rgba(61,212,224,.14)";
        btn.style.outline = "1px solid rgba(61,212,224,.35)";
      } else {
        btn.style.background = "transparent";
        btn.style.outline = "none";
      }
    });
  }

  function pick(userId) {
    if (!_activeEl || !_state) return hide();
    var el = _activeEl;
    var st = _state;
    var id = parseInt(userId, 10);
    if (!id) return hide();
    var before = el.value.slice(0, st.at);
    var after = el.value.slice(st.pos);
    var insert = "@" + id + " ";
    el.value = before + insert + after;
    var np = before.length + insert.length;
    if (typeof el.setSelectionRange === "function") {
      el.setSelectionRange(np, np);
    }
    hide();
    el.focus();
  }

  function fetchSuggest(digits) {
    var q = digits || "";
    var url = API + "?digits=" + encodeURIComponent(q);
    var my = ++_reqSeq;
    fetch(url, { credentials: "same-origin" })
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (my !== _reqSeq) return;
        if (!d || !Array.isArray(d.users)) return;
        _items = d.users;
        render(d.users);
      })
      .catch(function () {
        if (_dd) {
          _dd.innerHTML =
            '<div style="padding:10px;font-size:12px;color:#f87171">Ошибка загрузки</div>';
          _dd.style.display = "block";
        }
      });
  }

  function onInput(ev) {
    var el = ev.target;
    if (!el.classList || !el.classList.contains("nf-mention-field")) return;
    var st = getMentionState(el);
    if (!st) {
      hide();
      return;
    }
    _activeEl = el;
    _state = st;
    positionDd(el);
    if (_debounce) clearTimeout(_debounce);
    _debounce = setTimeout(function () {
      _debounce = null;
      fetchSuggest(st.digits);
    }, debounceMs);
  }

  function onKeydown(ev) {
    var el = ev.target;
    if (!el.classList || !el.classList.contains("nf-mention-field")) return;
    if (!_dd || _dd.style.display === "none") return;
    if (ev.key === "Escape") {
      ev.preventDefault();
      ev.stopPropagation();
      hide();
      return;
    }
    if (ev.key === "ArrowDown") {
      ev.preventDefault();
      ev.stopPropagation();
      if (_items.length) setHighlight(Math.min(_items.length - 1, _hi + 1));
      return;
    }
    if (ev.key === "ArrowUp") {
      ev.preventDefault();
      ev.stopPropagation();
      if (_items.length) setHighlight(Math.max(0, _hi - 1));
      return;
    }
    if (ev.key === "Enter") {
      if (_items.length && _hi >= 0) {
        ev.preventDefault();
        ev.stopPropagation();
        pick(_items[_hi].id);
      }
      return;
    }
  }

  function onBlur() {
    setTimeout(function () {
      var ae = document.activeElement;
      if (ae && _dd && _dd.contains(ae)) return;
      hide();
    }, 180);
  }

  function bindEl(el) {
    if (!el || _bound.has(el)) return;
    _bound.add(el);
    el.addEventListener("input", onInput);
    /* capture: перехватить Enter/стрелки раньше inline onkeydown (личка, группы) */
    el.addEventListener("keydown", onKeydown, true);
    el.addEventListener("blur", onBlur);
  }

  function scan() {
    document.querySelectorAll(".nf-mention-field").forEach(bindEl);
  }

  window.NFInitMentionAutocomplete = function () {
    scan();
  };

  document.addEventListener("DOMContentLoaded", function () {
    scan();
  });

  function onScrollOrResize() {
    if (_activeEl && _dd && _dd.style.display !== "none") positionDd(_activeEl);
  }
  window.addEventListener("scroll", onScrollOrResize, true);
  window.addEventListener("resize", onScrollOrResize);

  var mo = new MutationObserver(function () {
    scan();
  });
  if (document.documentElement) {
    mo.observe(document.documentElement, { childList: true, subtree: true });
  }
})();

