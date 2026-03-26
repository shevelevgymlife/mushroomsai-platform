(function () {
  function isFreeRestrictedUser() {
    try {
      var plan = String(window.__APP_PLAN || "free").toLowerCase();
      var role = String(window.__APP_ROLE || "user").toLowerCase();
      if (role === "admin" || role === "moderator") return false;
      return plan === "free";
    } catch (e) {
      return false;
    }
  }
  function goTariffIfRestricted(e) {
    if (!isFreeRestrictedUser()) return false;
    if (e && e.preventDefault) e.preventDefault();
    if (e && e.stopPropagation) e.stopPropagation();
    window.location.href = "/subscriptions";
    return true;
  }
  function escAttr(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/"/g, '&quot;');
  }
  function escHtml(t) {
    var d = document.createElement('div');
    d.textContent = t == null ? '' : String(t);
    return d.innerHTML;
  }
  function effUid() {
    var u = window.__APP_EFF_UID;
    if (u == null || u === '') return 0;
    return parseInt(u, 10) || 0;
  }
  function renderActivityRow(it) {
    var a = it.actor || {};
    var av = a.avatar
      ? '<img src="' +
        escAttr(a.avatar) +
        '" alt="" style="width:40px;height:40px;border-radius:50%;object-fit:cover;border:2px solid rgba(61,212,224,.35)">'
      : '<div style="width:40px;height:40px;border-radius:50%;background:#1a1a1a;display:flex;align-items:center;justify-content:center;font-size:18px">🍄</div>';
    var name = escHtml(String(a.name || 'Участник'));
    var t = String(it.created_at || '')
      .replace('T', ' ')
      .slice(0, 19);
    var uid = effUid();
    if (it.type === 'post_like') {
      var href =
        '/community/post/' +
        encodeURIComponent(String(it.post_id || '')) +
        '?back=' +
        encodeURIComponent(location.pathname);
      return (
        '<a class="app-act-row" href="' +
        href +
        '">' +
        av +
        '<div style="flex:1;min-width:0"><div class="app-act-title">⭐ Лайк поста</div><div class="app-act-meta">' +
        name +
        ' · ' +
        t +
        '</div></div></a>'
      );
    }
    if (it.type === 'comment') {
      var href2 =
        '/community/post/' +
        encodeURIComponent(String(it.post_id || '')) +
        '?back=' +
        encodeURIComponent(location.pathname);
      var sn = escHtml(String(it.snippet || ''));
      return (
        '<a class="app-act-row" href="' +
        href2 +
        '">' +
        av +
        '<div style="flex:1;min-width:0"><div class="app-act-title">💬 Комментарий</div><div class="app-act-meta">' +
        name +
        ': ' +
        sn +
        '</div><div class="app-act-meta" style="margin-top:4px;opacity:.85">' +
        t +
        '</div></div></a>'
      );
    }
    if (it.type === 'profile_like') {
      var profHref = '/community/profile/' + encodeURIComponent(String(effUid() || ''));
      return (
        '<a class="app-act-row" href="' +
        profHref +
        '">' +
        av +
        '<div style="flex:1;min-width:0"><div class="app-act-title">❤️ Лайк профиля</div><div class="app-act-meta">' +
        name +
        ' · ' +
        t +
        '</div></div></a>'
      );
    }
    if (it.type === 'message') {
      var href3 = '/messages/' + encodeURIComponent(String(a.id || ''));
      var tx = escHtml(String(it.text_preview || ''));
      return (
        '<a class="app-act-row" href="' +
        href3 +
        '">' +
        av +
        '<div style="flex:1;min-width:0"><div class="app-act-title">✉ Сообщение</div><div class="app-act-meta">' +
        name +
        ': ' +
        tx +
        '</div><div class="app-act-meta" style="margin-top:4px;opacity:.85">' +
        t +
        '</div></div></a>'
      );
    }
    return '';
  }
  async function refreshAppHeaderBadges() {
    try {
      var r = await fetch('/community/activity/unread-count', { credentials: 'same-origin' });
      var d = await r.json();
      var act =
        typeof d.activity_total === 'number'
          ? d.activity_total
          : (d.likes || 0) + (d.comments || 0) + (d.profile_likes || 0);
      var ab = document.getElementById('cpActivityBadge');
      if (ab) {
        var n = Math.max(0, parseInt(act, 10) || 0);
        ab.textContent = n > 99 ? '99+' : String(n);
        ab.classList.toggle('on', n > 0);
      }
      var mb = document.getElementById('cpMsgBadge');
      if (mb) {
        var nm = Math.max(0, d.messages || 0);
        mb.textContent = nm > 99 ? '99+' : String(nm);
        mb.classList.toggle('on', nm > 0);
      }
    } catch (e) {}
  }
  function openAppActivityPanel() {
    try {
      if (typeof window.closeAppGlobalDrawer === 'function') window.closeAppGlobalDrawer();
    } catch (e) {}
    var p = document.getElementById('appActivityPanel');
    var list = document.getElementById('appActivityList');
    if (!p || !list) return;
    p.style.display = 'block';
    try {
      document.body.style.overflow = 'hidden';
    } catch (e) {}
    list.innerHTML = '<div style="padding:20px;text-align:center;color:#888">Загрузка…</div>';
    fetch('/community/activity/feed', { credentials: 'same-origin' })
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        var items = (d && d.items) || [];
        if (!items.length) {
          list.innerHTML = '<div style="padding:28px;text-align:center;color:#666">Пока нет новых событий</div>';
          return;
        }
        list.innerHTML = items.map(renderActivityRow).join('');
      })
      .catch(function () {
        list.innerHTML = '<div style="padding:20px;color:#f87171;text-align:center">Ошибка загрузки</div>';
      });
  }
  function closeAppActivityPanel() {
    var p = document.getElementById('appActivityPanel');
    if (p) p.style.display = 'none';
    try {
      document.body.style.overflow = '';
    } catch (e) {}
    fetch('/community/activity/mark-read', { method: 'POST', credentials: 'same-origin' }).then(function () {
      return refreshAppHeaderBadges();
    });
  }
  window.closeAppActivityPanel = closeAppActivityPanel;
  window.refreshAppHeaderBadges = refreshAppHeaderBadges;

  document.addEventListener('DOMContentLoaded', function () {
    var brand = document.querySelector('.app-head-brand');
    if (brand) {
      brand.addEventListener('click', function (e) {
        goTariffIfRestricted(e);
      });
    }
    var menu = document.getElementById('appMobileMenuBtn');
    if (menu) {
      menu.addEventListener('click', function () {
        if (typeof window.__appMenuClick === 'function') window.__appMenuClick();
        else location.href = '/community';
      });
    }
    var bell = document.getElementById('cpActivityBell');
    if (bell) {
      bell.addEventListener('click', function (e) {
        if (goTariffIfRestricted(e)) return;
        e.preventDefault();
        e.stopPropagation();
        openAppActivityPanel();
      });
    }
    var profile = document.getElementById('cpProfileHead');
    if (profile) {
      profile.addEventListener('click', function (e) {
        goTariffIfRestricted(e);
      });
    }
    var panel = document.getElementById('appActivityPanel');
    if (panel) {
      panel.addEventListener('click', function (e) {
        if (e.target === panel) closeAppActivityPanel();
      });
    }
    if (document.getElementById('cpActivityBadge')) {
      setInterval(refreshAppHeaderBadges, 45000);
      refreshAppHeaderBadges();
    }
  });
})();
