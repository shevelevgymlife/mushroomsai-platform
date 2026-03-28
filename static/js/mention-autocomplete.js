/**
 * Подсказки @упоминание: после @ — топ по популярности; цифры — фильтр по префиксу id.
 * Поля с class="nf-mention-field"
 */
(function () {
  var API = "/community/users/mention-suggest";
  var debounceMs = 120;
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
    _dd.setAttribute("aria-label", "Упоминания");
    _dd.style.cssText =
      "display:none;position:fixed;z-index:2147483000;min-width:240px;max-width:min(94vw,340px);max-height:min(48vh,320px);overflow:auto;border-radius:16px;border:1px solid rgba(61,212,224,.45);background:rgba(10,12,18,.98);box-shadow:0 20px 56px rgba(0,0,0,.6),0 0 0 1px rgba(255,255,255,.06);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);padding:8px;pointer-events:auto;";
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
    if (v == null) return null;
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
    var w = Math.max(240, Math.min(r.width, 340));
    var estH = Math.min(320, window.innerHeight * 0.45);
    dd.style.width = w + "px";
    dd.style.left = Math.max(8, Math.min(r.left, window.innerWidth - w - 8)) + "px";
    var below = r.bottom + 6;
    var above = r.top - estH - 6;
    if (below + estH > window.innerHeight - 12 && above > 12) {
      dd.style.top = Math.max(8, above) + "px";
      dd.style.maxHeight = Math.min(estH, r.top - 20) + "px";
    } else {
      dd.style.top = below + "px";
      dd.style.maxHeight = Math.min(320, window.innerHeight - below - 12) + "px";
    }
  }

  function showLoading() {
    var dd = ensureDd();
    _items = [];
    _hi = -1;
    dd.innerHTML =
      '<div style="padding:14px 12px;font-size:13px;color:#94a3b8;text-align:center;display:flex;align-items:center;justify-content:center;gap:10px"><span style="display:inline-block;width:18px;height:18px;border:2px solid rgba(61,212,224,.35);border-top-color:#3dd4e0;border-radius:50%;animation:nf-ma-spin .7s linear infinite"></span> Загрузка…</div>';
    if (!document.getElementById("nf-ma-spin-style")) {
      var st = document.createElement("style");
      st.id = "nf-ma-spin-style";
      st.textContent = "@keyframes nf-ma-spin{to{transform:rotate(360deg)}}";
      document.head.appendChild(st);
    }
    dd.style.display = "block";
  }

  function render(users) {
    var dd = ensureDd();
    if (!users || !users.length) {
      _items = [];
      _hi = -1;
      dd.innerHTML =
        '<div style="padding:14px 12px;font-size:13px;color:#94a3b8;text-align:center">Никого не найдено — введите ещё цифры id</div>';
      dd.style.display = "block";
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
          '" class="nf-mention-dd-item" style="display:flex;width:100%;align-items:center;gap:12px;padding:10px 12px;margin:0 0 4px;border:none;border-radius:12px;background:rgba(255,255,255,.04);color:#e8eaef;cursor:pointer;text-align:left;font-size:14px;box-sizing:border-box">' +
          avH +
          '<span style="min-width:0;flex:1;overflow:hidden"><span style="font-weight:800;color:#67e8f9;font-size:15px">@' +
          u.id +
          '</span><br><span style="font-size:13px;color:#cbd5e1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;display:block;max-width:100%">' +
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
        btn.style.background = "rgba(61,212,224,.18)";
        btn.style.outline = "1px solid rgba(61,212,224,.45)";
      } else {
        btn.style.background = "rgba(255,255,255,.04)";
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
    try {
      el.focus();
    } catch (e) {}
    try {
      el.dispatchEvent(new Event("input", { bubbles: true }));
    } catch (e) {}
  }

  function fetchSuggest(digits) {
    var q = digits || "";
    var url = API + "?digits=" + encodeURIComponent(q);
    var my = ++_reqSeq;
    fetch(url, { credentials: "same-origin" })
      .then(function (r) {
        return r.json().then(function (d) {
          return { ok: r.ok, status: r.status, d: d };
        });
      })
      .then(function (pack) {
        if (my !== _reqSeq) return;
        if (!pack.ok) {
          var msg =
            pack.status === 401
              ? "Войдите в аккаунт"
              : "Не удалось загрузить подсказки";
          var dd = ensureDd();
          dd.innerHTML =
            '<div style="padding:14px;font-size:13px;color:#f87171;text-align:center">' +
            esc(msg) +
            "</div>";
          dd.style.display = "block";
          return;
        }
        var d = pack.d;
        if (!d || !Array.isArray(d.users)) {
          var dd2 = ensureDd();
          dd2.innerHTML =
            '<div style="padding:14px;font-size:13px;color:#f87171;text-align:center">Неверный ответ сервера</div>';
          dd2.style.display = "block";
          return;
        }
        _items = d.users;
        render(d.users);
      })
      .catch(function () {
        if (my !== _reqSeq) return;
        var dd = ensureDd();
        dd.innerHTML =
          '<div style="padding:14px;font-size:13px;color:#f87171;text-align:center">Ошибка сети</div>';
        dd.style.display = "block";
      });
  }

  function scheduleSuggest(el, st) {
    _activeEl = el;
    _state = st;
    positionDd(el);
    showLoading();
    if (_debounce) clearTimeout(_debounce);
    _debounce = setTimeout(function () {
      _debounce = null;
      fetchSuggest(st.digits);
    }, debounceMs);
  }

  function checkField(el) {
    if (!el || !el.classList || !el.classList.contains("nf-mention-field"))
      return;
    var st = getMentionState(el);
    if (!st) {
      hide();
      return;
    }
    scheduleSuggest(el, st);
  }

  function onInput(ev) {
    checkField(ev.target);
  }

  function onKeyup(ev) {
    if (ev.isComposing) return;
    checkField(ev.target);
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
    }, 200);
  }

  function bindEl(el) {
    if (!el || _bound.has(el)) return;
    _bound.add(el);
    el.addEventListener("input", onInput);
    el.addEventListener("keyup", onKeyup);
    el.addEventListener("keydown", onKeydown, true);
    el.addEventListener("blur", onBlur);
  }

  function scan() {
    try {
      document.querySelectorAll(".nf-mention-field").forEach(bindEl);
    } catch (e) {}
  }

  window.NFInitMentionAutocomplete = scan;

  function boot() {
    scan();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

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
