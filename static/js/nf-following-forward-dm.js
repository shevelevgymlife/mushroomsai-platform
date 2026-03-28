/**
 * Пересылка поста в ЛС: после @ — выпадашка только из подписок (following-share),
 * клик или Enter — сразу POST share-dm. Поле ввода НЕ должно иметь class nf-mention-field.
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
    var statusEl = config.statusEl;
    var sending = false;

    if (!inp || !dd || !postId) {
      return { loadFollowing: function () {} };
    }

    var users = [];
    var _active = false;
    var _state = null;
    var _items = [];
    var _hi = -1;

    function setStatus(msg, isErr) {
      if (!statusEl) return;
      if (!msg) {
        statusEl.style.display = "none";
        statusEl.textContent = "";
        return;
      }
      statusEl.style.display = "block";
      statusEl.textContent = msg;
      statusEl.style.color = isErr ? "#f87171" : "#4ade80";
    }

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

    function paintHi() {
      dd.querySelectorAll(".nf-fwd-dd-item").forEach(function (btn, j) {
        if (j === _hi) {
          btn.style.background = "rgba(247,179,255,.2)";
          btn.style.outline = "1px solid rgba(247,179,255,.45)";
        } else {
          btn.style.background = "rgba(255,255,255,.04)";
          btn.style.outline = "none";
        }
      });
    }

    function renderDd(list) {
      if (!list || !list.length) {
        dd.innerHTML =
          '<div style="padding:14px 12px;font-size:13px;color:#94a3b8;text-align:center">Нет подписок с таким @id — подпишитесь в ленте или введите другие цифры</div>';
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
            : '<div style="width:40px;height:40px;border-radius:50%;background:#1a1f2e;display:flex;align-items:center;justify-content:center;font-size:18px;flex-shrink:0;border:1px solid rgba(247,179,255,.3)">🍄</div>';
          return (
            '<button type="button" role="option" data-idx="' +
            idx +
            '" class="nf-fwd-dd-item" style="display:flex;width:100%;align-items:center;gap:12px;padding:10px 12px;margin:0 0 4px;border:none;border-radius:12px;background:rgba(255,255,255,.04);color:#e8eaef;cursor:pointer;text-align:left;font-size:14px;box-sizing:border-box">' +
            avH +
            '<span style="min-width:0;flex:1;overflow:hidden"><span style="font-weight:800;color:#ffc6ff;font-size:15px">@' +
            u.id +
            '</span><br><span style="font-size:13px;color:#cbd5e1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;display:block;max-width:100%">' +
            esc2(u.name || "Участник") +
            "</span></span></button>"
          );
        })
        .join("");
      dd.querySelectorAll(".nf-fwd-dd-item").forEach(function (btn) {
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
          sendTo(_items[ix].id);
        });
      });
      dd.style.display = "block";
      paintHi();
      positionDd(_state.pos);
    }

    function filterFollowing(digits) {
      var d = String(digits || "");
      var needle = d.toLowerCase();
      if (!users.length) return [];
      return users
        .filter(function (u) {
          var idStr = String(u.id);
          if (!needle) return true;
          return idStr.indexOf(needle) === 0;
        })
        .slice(0, 40);
    }

    function suggest(st) {
      if (!users.length) {
        dd.innerHTML =
          '<div style="padding:14px 12px;font-size:13px;color:#94a3b8;text-align:center">Загрузка подписок…</div>';
        dd.style.display = "block";
        positionDd(st.pos);
        return;
      }
      renderDd(filterFollowing(st.digits));
    }

    function checkField() {
      var st = getMentionState(inp);
      if (!st) {
        hideDd();
        return;
      }
      _active = true;
      _state = st;
      suggest(st);
    }

    async function sendTo(recipientId) {
      if (sending) return;
      sending = true;
      hideDd();
      setStatus("Отправка…", false);
      try {
        var r = await fetch("/community/post/" + postId + "/share-dm", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ recipient_id: recipientId }),
        });
        var d = await r.json().catch(function () {
          return {};
        });
        if (r.ok && d.ok) {
          inp.value = "";
          setStatus("Пост отправлен в личку со ссылкой", false);
          setTimeout(function () {
            setStatus("");
          }, 3200);
        } else {
          setStatus(d.error || "Не удалось отправить", true);
        }
      } catch (e) {
        setStatus("Сеть недоступна", true);
      }
      sending = false;
      try {
        inp.focus();
      } catch (e2) {}
    }

    inp.addEventListener("input", checkField);
    inp.addEventListener("keyup", function (ev) {
      if (ev.isComposing) return;
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
          sendTo(_items[_hi].id);
        }
      },
      true
    );
    inp.addEventListener("blur", function () {
      setTimeout(function () {
        if (document.activeElement && dd.contains(document.activeElement)) return;
        hideDd();
      }, 200);
    });
    dd.addEventListener("mousedown", function (e) {
      e.preventDefault();
    });

    function syncPos() {
      if (_active && _state && dd.style.display !== "none") positionDd(_state.pos);
    }
    window.addEventListener("scroll", syncPos, true);
    window.addEventListener("resize", syncPos);

    function loadFollowing() {
      return fetch("/community/me/following-share", { credentials: "same-origin" })
        .then(function (r) {
          return r.json();
        })
        .then(function (d) {
          users = (d && d.users) || [];
          if (_active && _state) suggest(_state);
          return users;
        })
        .catch(function () {
          users = [];
          return [];
        });
    }

    loadFollowing();

    return { loadFollowing: loadFollowing };
  }

  window.NFFollowingForwardDm = { attach: attach };
})();
