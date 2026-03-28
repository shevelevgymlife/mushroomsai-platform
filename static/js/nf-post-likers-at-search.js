/**
 * Поиск среди лайкнувших пост: в поле после @ — выпадающий список только тех, кто поставил звезду,
 * с фильтром по префиксу id. Панель позиционируется у каретки (как mention-autocomplete).
 *
 * Подключение на странице со списком карточек лайкнувших:
 *   var x = NFPostLikersAtSearch.attach({
 *     postId: 123,
 *     input: document.getElementById('...'),
 *     dropdown: document.getElementById('...'), // один div на страницу
 *     cardsSelector: '#grid .nf-stars-card'      // элементы с data-name, data-id
 *   });
 *   после загрузки списка: x.setLikers(users);
 */
(function () {
  function esc2(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  function escA(s) {
    return String(s || "").replace(/&/g, "&amp;").replace(/"/g, "&quot;");
  }

  function getMentionState(el) {
    var v = el.value;
    if (v == null) return null;
    var pos = typeof el.selectionStart === "number" ? el.selectionStart : v.length;
    if (pos < 1) return null;
    var i = pos - 1;
    while (i >= 0 && /\d/.test(v.charAt(i))) i--;
    if (i < 0 || v.charAt(i) !== "@") return null;
    var chunk = v.slice(i + 1, pos);
    if (!/^\d*$/.test(chunk)) return null;
    return { at: i, digits: chunk, pos: pos };
  }

  function caretCoordsInput(el, pos) {
    var val = el.value || "";
    pos = Math.max(0, Math.min(pos, val.length));
    var rect = el.getBoundingClientRect();
    var cs = window.getComputedStyle(el);
    var bl = parseFloat(cs.borderLeftWidth) || 0;
    var bt = parseFloat(cs.borderTopWidth) || 0;
    var pl = parseFloat(cs.paddingLeft) || 0;
    var pt = parseFloat(cs.paddingTop) || 0;
    var canvas = document.createElement("canvas");
    var ctx = canvas.getContext("2d");
    ctx.font = cs.font || "";
    var w = ctx.measureText(val.substring(0, pos)).width;
    var sl = el.scrollLeft || 0;
    var x = rect.left + bl + pl + w - sl;
    var fs = parseFloat(cs.fontSize) || 14;
    var lh = parseFloat(cs.lineHeight);
    if (!lh || isNaN(lh)) lh = fs * 1.28;
    var y = rect.top + bt + pt;
    return { left: x, top: y, height: lh };
  }

  function attach(config) {
    var postId = config.postId;
    var inp = config.input;
    var dd = config.dropdown;
    var cardsSelector = config.cardsSelector || "#starCanvas .nf-stars-card";
    if (!inp || !dd || !postId) {
      return {
        setLikers: function () {},
        fetchLikers: function () {
          return Promise.resolve([]);
        },
      };
    }

    var users = [];
    var _active = false;
    var _state = null;
    var _items = [];
    var _hi = -1;

    function positionDd(caretPos) {
      var cr = caretCoordsInput(inp, caretPos);
      var w = Math.min(340, Math.max(240, window.innerWidth - 20));
      dd.style.width = w + "px";
      var gap = 6;
      var left = Math.max(6, Math.min(cr.left, window.innerWidth - w - 6));
      var preferTop = cr.top + cr.height + gap;
      var spaceBelow = window.innerHeight - preferTop - 10;
      var spaceAbove = cr.top - 10;
      var maxH = Math.max(100, Math.min(320, Math.max(spaceBelow, spaceAbove, 0) - 4));
      dd.style.maxHeight = maxH + "px";
      if (spaceBelow >= 140 || spaceBelow >= spaceAbove) dd.style.top = preferTop + "px";
      else dd.style.top = Math.max(8, cr.top - maxH - gap) + "px";
      dd.style.left = left + "px";
    }

    function hideDd() {
      dd.style.display = "none";
      _active = false;
      _state = null;
      _items = [];
      _hi = -1;
    }

    function filterCards(raw) {
      var t = (raw || "").trim().toLowerCase().replace(/\s+$/g, "");
      var m = t.match(/@(\d*)$/);
      var atPrefix = m ? m[1] : null;
      var q = m ? t.slice(0, m.index).replace(/\s+$/g, "").replace(/\s+/g, " ").trim() : t.replace(/\s+/g, " ").trim();
      document.querySelectorAll(cardsSelector).forEach(function (el) {
        var nm = el.getAttribute("data-name") || "";
        var id = el.getAttribute("data-id") || "";
        var ok = true;
        if (atPrefix !== null) {
          if (atPrefix === "") ok = true;
          else ok = id.indexOf(atPrefix) === 0;
        }
        if (q) ok = ok && (nm.indexOf(q) >= 0 || id.indexOf(q) >= 0);
        el.style.display = ok ? "block" : "none";
      });
    }

    function paintHi() {
      dd.querySelectorAll(".nf-likers-at-item").forEach(function (btn, j) {
        if (j === _hi) {
          btn.style.background = "rgba(61,212,224,.18)";
          btn.style.outline = "1px solid rgba(61,212,224,.45)";
        } else {
          btn.style.background = "rgba(255,255,255,.04)";
          btn.style.outline = "none";
        }
      });
    }

    function renderDd(list) {
      if (!list || !list.length) {
        dd.innerHTML =
          '<div style="padding:14px 12px;font-size:13px;color:#94a3b8;text-align:center">Нет совпадений среди поставивших звезду</div>';
        dd.style.display = "block";
        positionDd(_state.pos);
        return;
      }
      _items = list;
      _hi = 0;
      dd.innerHTML = list
        .map(function (u, idx) {
          var av = (u.avatar || "").trim();
          var avH = av
            ? '<img src="' +
              escA(av) +
              '" alt="" loading="lazy" style="width:40px;height:40px;border-radius:50%;object-fit:cover;border:2px solid rgba(247,179,255,.4);flex-shrink:0">'
            : '<div style="width:40px;height:40px;border-radius:50%;background:#1a1f2e;display:flex;align-items:center;justify-content:center;font-size:18px;flex-shrink:0;border:1px solid rgba(247,179,255,.3)">⭐</div>';
          return (
            '<button type="button" role="option" data-idx="' +
            idx +
            '" class="nf-likers-at-item" style="display:flex;width:100%;align-items:center;gap:12px;padding:10px 12px;margin:0 0 4px;border:none;border-radius:12px;background:rgba(255,255,255,.04);color:#e8eaef;cursor:pointer;text-align:left;font-size:14px;box-sizing:border-box">' +
            avH +
            '<span style="min-width:0;flex:1;overflow:hidden"><span style="font-weight:800;color:#ffc6ff;font-size:15px">@' +
            u.id +
            '</span><br><span style="font-size:13px;color:#cbd5e1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;display:block;max-width:100%">' +
            esc2(u.name || "Участник") +
            "</span></span></button>"
          );
        })
        .join("");
      dd.querySelectorAll(".nf-likers-at-item").forEach(function (btn) {
        btn.addEventListener("mouseenter", function () {
          var ix = parseInt(btn.getAttribute("data-idx"), 10);
          if (!isNaN(ix)) {
            _hi = ix;
            paintHi();
          }
        });
        btn.addEventListener("mousedown", function (e) {
          e.preventDefault();
        });
        btn.addEventListener("click", function () {
          var ix = parseInt(btn.getAttribute("data-idx"), 10);
          if (isNaN(ix) || !_items[ix]) return;
          var uid = _items[ix].id;
          var st = _state;
          if (!st) return;
          var before = inp.value.slice(0, st.at);
          var after = inp.value.slice(st.pos);
          inp.value = before + "@" + uid + " " + after;
          var np = before.length + String(uid).length + 2;
          if (typeof inp.setSelectionRange === "function") inp.setSelectionRange(np, np);
          hideDd();
          filterCards(inp.value);
          try {
            inp.focus();
          } catch (e) {}
        });
      });
      dd.style.display = "block";
      paintHi();
      positionDd(_state.pos);
    }

    function suggestFromLikers(digits) {
      var d = String(digits || "");
      if (!users.length) {
        dd.innerHTML =
          '<div style="padding:14px 12px;font-size:13px;color:#94a3b8;text-align:center">Пока нет звёзд или список ещё грузится</div>';
        dd.style.display = "block";
        positionDd(_state.pos);
        return;
      }
      var out = users
        .filter(function (u) {
          var idStr = String(u.id);
          return !d || idStr.indexOf(d) === 0;
        })
        .slice(0, 40);
      renderDd(out);
    }

    function checkField() {
      var st = getMentionState(inp);
      if (!st) {
        hideDd();
        return;
      }
      _active = true;
      _state = st;
      suggestFromLikers(st.digits);
    }

    inp.addEventListener("input", function () {
      filterCards(inp.value);
      checkField();
    });
    inp.addEventListener("keyup", function (ev) {
      if (ev.isComposing) return;
      filterCards(inp.value);
      checkField();
    });
    inp.addEventListener("select", function () {
      if (_active) checkField();
    });
    inp.addEventListener("click", function () {
      if (_active) checkField();
    });
    inp.addEventListener(
      "keydown",
      function (ev) {
        if (dd.style.display === "none") return;
        if (ev.key === "Escape") {
          ev.preventDefault();
          hideDd();
          return;
        }
        if (ev.key === "ArrowDown") {
          ev.preventDefault();
          if (_items.length) _hi = Math.min(_items.length - 1, _hi + 1);
          paintHi();
          return;
        }
        if (ev.key === "ArrowUp") {
          ev.preventDefault();
          if (_items.length) _hi = Math.max(0, _hi - 1);
          paintHi();
          return;
        }
        if (ev.key === "Enter" && _items.length && _hi >= 0) {
          ev.preventDefault();
          var u = _items[_hi];
          if (!u) return;
          var st = _state;
          if (!st) return;
          var before = inp.value.slice(0, st.at);
          var after = inp.value.slice(st.pos);
          inp.value = before + "@" + u.id + " " + after;
          var np = before.length + String(u.id).length + 2;
          if (typeof inp.setSelectionRange === "function") inp.setSelectionRange(np, np);
          hideDd();
          filterCards(inp.value);
        }
      },
      true
    );
    inp.addEventListener("blur", function () {
      setTimeout(function () {
        if (document.activeElement && dd.contains(document.activeElement)) return;
        hideDd();
      }, 180);
    });
    dd.addEventListener("mousedown", function (e) {
      e.preventDefault();
    });

    function syncPos() {
      if (_active && _state && dd.style.display !== "none") positionDd(_state.pos);
    }
    window.addEventListener("scroll", syncPos, true);
    window.addEventListener("resize", syncPos);

    function setLikers(list) {
      users = list && list.slice ? list.slice() : [];
    }

    function fetchLikers() {
      return fetch("/community/post/" + postId + "/likers-json", { credentials: "same-origin" })
        .then(function (r) {
          return r.json();
        })
        .then(function (d) {
          var arr = (d && d.users) || [];
          setLikers(arr);
          return arr;
        })
        .catch(function () {
          return [];
        });
    }

    return { setLikers: setLikers, fetchLikers: fetchLikers, filterCards: filterCards };
  }

  window.NFPostLikersAtSearch = { attach: attach };
})();
