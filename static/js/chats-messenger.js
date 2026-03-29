(function () {
  const ME = window.__CHATS_ME || {};
  const uid = ME.id;
  const qs = new URLSearchParams(location.search);
  const openUserParam = qs.get("open_user");
  const openChatParam = qs.get("open_chat");
  const focusMsgParam = qs.get("focus_msg");

  const PERM_KEYS = [
    "send_messages",
    "send_media",
    "invite_members",
    "pin_messages",
    "edit_group_info",
    "delete_others_messages",
    "add_admins",
    "ban_members",
    "send_stickers",
    "send_voice",
    "send_links",
    "mention_everyone",
    "slow_mode_bypass",
    "manage_topics",
  ];
  const PERM_LABELS = {
    send_messages: "Сообщения",
    send_media: "Фото и медиа",
    invite_members: "Приглашения",
    pin_messages: "Закреп",
    edit_group_info: "Инфо о группе",
    delete_others_messages: "Удаление чужих",
    add_admins: "Назначение админов",
    ban_members: "Блокировки",
    send_stickers: "Стикеры",
    send_voice: "Голосовые",
    send_links: "Ссылки",
    mention_everyone: "Упоминания всех",
    slow_mode_bypass: "Обход slow mode",
    manage_topics: "Темы",
  };

  const el = {
    app: document.getElementById("chatsApp"),
    list: document.getElementById("chatsList"),
    search: document.getElementById("chatsSidebarSearch"),
    main: document.getElementById("chatsMain"),
    placeholder: document.getElementById("chatsPlaceholder"),
    thread: document.getElementById("chatsThread"),
    scroll: document.getElementById("chatsScroll"),
    headHit: document.getElementById("chatsThreadHeadHit"),
    headTitle: document.getElementById("chatsHeadTitle"),
    headSub: document.getElementById("chatsHeadSub"),
    headAv: document.getElementById("chatsHeadAv"),
    menuBtn: document.getElementById("chatsMenuBtn"),
    drop: document.getElementById("chatsDropMenu"),
    mobileBack: document.getElementById("chatsMobileBack"),
    ta: document.getElementById("chatsTextarea"),
    send: document.getElementById("chatsSend"),
    attach: document.getElementById("chatsAttach"),
    file: document.getElementById("chatsFile"),
    replyBar: document.getElementById("chatsReplyBar"),
    replyCancel: document.getElementById("chatsReplyCancel"),
    modal: document.getElementById("chatsModal"),
    modalClose: document.getElementById("chatsModalClose"),
    btnNew: document.getElementById("chatsBtnNew"),
    tabPersonal: document.getElementById("chatsTabPersonal"),
    tabGroup: document.getElementById("chatsTabGroup"),
    panePersonal: document.getElementById("chatsPanePersonal"),
    paneGroup: document.getElementById("chatsPaneGroup"),
    userSearch: document.getElementById("chatsUserSearch"),
    userResults: document.getElementById("chatsUserResults"),
    gName: document.getElementById("chatsGroupName"),
    gFile: document.getElementById("chatsGroupAvatar"),
    gSearch: document.getElementById("chatsGroupSearch"),
    gMembers: document.getElementById("chatsGroupMembers"),
    gCreate: document.getElementById("chatsGroupCreate"),
    compose: document.querySelector(".chats-compose"),
    addMemberBack: document.getElementById("chAddMemberBack"),
    addMemberClose: document.getElementById("chAddMemberClose"),
    addMemberSearch: document.getElementById("chAddMemberSearch"),
    addMemberResults: document.getElementById("chAddMemberResults"),
    groupShell: document.getElementById("chGroupShell"),
    shellClose: document.getElementById("chShellClose"),
    shellHubTitle: document.getElementById("chShellHubTitle"),
    tabParticipants: document.getElementById("chTabParticipants"),
    tabMedia: document.getElementById("chTabMedia"),
    tabBodyParticipants: document.getElementById("chTabBodyParticipants"),
    tabBodyMedia: document.getElementById("chTabBodyMedia"),
    partSearch: document.getElementById("chPartSearch"),
    partList: document.getElementById("chPartList"),
    mediaGrid: document.getElementById("chMediaGrid"),
    openDeepSettings: document.getElementById("chOpenDeepSettings"),
    quickVideo: document.getElementById("chQuickVideo"),
    quickMute: document.getElementById("chQuickMute"),
    quickMuteIcWrap: document.getElementById("chQuickMuteIcWrap"),
    quickMuteLbl: document.getElementById("chQuickMuteLbl"),
    quickSearch: document.getElementById("chQuickSearch"),
    quickMore: document.getElementById("chQuickMore"),
    chatSearchInput: document.getElementById("chChatSearchInput"),
    chatSearchResults: document.getElementById("chChatSearchResults"),
    setName: document.getElementById("chSetName"),
    setDesc: document.getElementById("chSetDesc"),
    togglePublic: document.getElementById("chTogglePublic"),
    setPublicVal: document.getElementById("chSetPublicVal"),
    toggleReactions: document.getElementById("chToggleReactions"),
    setReactVal: document.getElementById("chSetReactVal"),
    cycleAppearance: document.getElementById("chCycleAppearance"),
    setAppearVal: document.getElementById("chSetAppearVal"),
    toggleTopics: document.getElementById("chToggleTopics"),
    setTopicsVal: document.getElementById("chSetTopicsVal"),
    setMembersCount: document.getElementById("chSetMembersCount"),
    setPermsScore: document.getElementById("chSetPermsScore"),
    setAdminCount: document.getElementById("chSetAdminCount"),
    setBanCount: document.getElementById("chSetBanCount"),
    settingsSave: document.getElementById("chSettingsSave"),
    deleteGroup: document.getElementById("chDeleteGroup"),
    permsList: document.getElementById("chPermsList"),
    permsSave: document.getElementById("chPermsSave"),
    adminsList: document.getElementById("chAdminsList"),
    bansList: document.getElementById("chBansList"),
    auditList: document.getElementById("chAuditList"),
  };

  const chSpr = document.querySelector(".ch-svg-sprite");
  if (chSpr && chSpr.parentNode) {
    document.body.appendChild(chSpr);
  }
  if (el.groupShell && el.groupShell.parentNode) {
    document.body.appendChild(el.groupShell);
  }
  if (el.addMemberBack && el.addMemberBack.parentNode) {
    document.body.appendChild(el.addMemberBack);
  }

  function isChatsMobile() {
    return window.matchMedia("(max-width: 900px)").matches;
  }

  function syncChatsViewport() {
    const root = document.documentElement;
    if (!el.app) return;
    if (!isChatsMobile()) {
      root.classList.remove("chats-vv-root");
      root.style.removeProperty("--ch-surface-h");
      document.body.classList.remove("chats-thread-open");
      return;
    }
    root.classList.add("chats-vv-root");
    const vv = window.visualViewport;
    const topBar = document.querySelector(".app-mobile-topbar");
    let top = 0;
    if (topBar && topBar.getBoundingClientRect) {
      top = Math.max(0, topBar.getBoundingClientRect().bottom);
    } else {
      const hh = parseFloat(getComputedStyle(root).getPropertyValue("--app-header-h")) || 56;
      top = hh;
    }
    const layoutBottom = vv ? vv.offsetTop + vv.height : window.innerHeight;
    const threadOpen = el.app.classList.contains("mobile-chat-open");
    if (threadOpen) document.body.classList.add("chats-thread-open");
    else document.body.classList.remove("chats-thread-open");
    let subtractTab = 0;
    if (!threadOpen) {
      const tab = document.querySelector(".app-uni-tabbar");
      if (tab) {
        const tr = tab.getBoundingClientRect();
        if (tr.height > 0) subtractTab = tr.height;
      }
    }
    const rawH = layoutBottom - top - subtractTab;
    const h = Math.max(160, Math.floor(rawH));
    root.style.setProperty("--ch-surface-h", h + "px");
  }

  function scrollMessagesToEnd() {
    const sc = el.scroll;
    if (!sc) return;
    const run = function () {
      sc.scrollTop = sc.scrollHeight;
    };
    run();
    requestAnimationFrame(run);
    requestAnimationFrame(function () {
      requestAnimationFrame(run);
    });
    setTimeout(run, 50);
    setTimeout(run, 200);
    setTimeout(run, 450);
  }

  function bindChatsViewport() {
    const fn = function () {
      syncChatsViewport();
    };
    const vv = window.visualViewport;
    if (vv) {
      vv.addEventListener("resize", fn);
      vv.addEventListener("scroll", fn);
    }
    window.addEventListener("resize", fn);
    window.addEventListener("orientationchange", fn);
    setTimeout(fn, 0);
    setTimeout(fn, 400);
  }

  let chats = [];
  let activeChatId = null;
  let messages = [];
  let replyToId = null;
  let pendingMediaUrl = null;
  let ws = null;
  let hbTimer = null;
  let loadingMore = false;
  let hasMoreOlder = false;
  let currentMeta = null;
  let selectedGroupIds = new Set();
  let membersCache = [];
  let addMemberSearchT = null;
  let partSearchT = null;
  let chatSearchT = null;
  let pendingFocusMessageId = null;

  function esc(s) {
    if (!s) return "";
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  function bubbleMsgHtml(text) {
    const raw = String(text == null ? "" : text);
    const lines = raw.split("\n");
    const fmt = (line) =>
      typeof window.linkifyCommunityMentionsPlain === "function"
        ? window.linkifyCommunityMentionsPlain(line)
        : esc(line);
    return lines.map(fmt).join("<br>");
  }

  function fmtTime(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    const now = new Date();
    if (d.toDateString() === now.toDateString()) {
      return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    }
    return d.toLocaleDateString([], { day: "numeric", month: "short" });
  }

  function fmtDay(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    return d.toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" });
  }

  async function api(path, opt) {
    const r = await fetch(path, Object.assign({ credentials: "same-origin" }, opt || {}));
    return r;
  }

  async function loadChats() {
    const r = await api("/api/chats");
    const d = await r.json();
    chats = d.chats || [];
    renderList();
  }

  function appendChatRow(c) {
    const row = document.createElement("div");
    row.className = "chats-row" + (c.id === activeChatId ? " active" : "");
    row.dataset.id = String(c.id);
    const av = c.avatar_url || "/static/favicon.svg";
    const badge = c.unread > 0 ? " on" : "";
    const isGroup = (c.type || "") === "group";
    const kindLabel = isGroup ? "Группа" : "ЛС";
    row.innerHTML =
      '<img class="chats-row-av" src="' +
      esc(av) +
      '" alt="" onerror="this.src=\'/static/favicon.svg\'">' +
      '<div class="chats-row-body">' +
      '<div class="chats-row-top"><span class="chats-row-name">' +
      esc(c.name) +
      '</span><span class="chats-row-time">' +
      esc(fmtTime(c.last_at)) +
      "</span></div>" +
      '<div class="chats-row-kind">' +
      kindLabel +
      "</div>" +
      '<div class="chats-row-preview">' +
      esc(c.last_message || "—") +
      "</div></div>" +
      '<span class="chats-row-badge' +
      badge +
      '">' +
      (c.unread > 99 ? "99+" : c.unread) +
      "</span>";
    row.onclick = () => openChat(c.id);
    el.list.appendChild(row);
  }

  function renderList() {
    const q = (el.search && el.search.value) || "";
    const ql = q.trim().toLowerCase();
    el.list.innerHTML = "";
    const filtered = chats.filter((c) => !ql || (c.name || "").toLowerCase().includes(ql));
    const groupChats = [];
    const personalChats = [];
    filtered.forEach((c) => {
      if ((c.type || "") === "group") groupChats.push(c);
      else personalChats.push(c);
    });
    function appendSection(title, arr) {
      if (!arr.length) return;
      const h = document.createElement("div");
      h.className = "chats-list-section-title";
      h.setAttribute("role", "presentation");
      h.textContent = title;
      el.list.appendChild(h);
      arr.forEach(appendChatRow);
    }
    appendSection("Группы", groupChats);
    appendSection("Личные чаты (ЛС)", personalChats);
  }

  function setMobileOpen(on) {
    if (!el.app) return;
    el.app.classList.toggle("mobile-chat-open", on);
    syncChatsViewport();
  }

  function syncGroupMemberChrome() {
    var nodes = document.querySelectorAll("[data-ch-requires-manage]");
    var addBtn = document.getElementById("chatsMenuAdd");
    if (!currentMeta || currentMeta.type !== "group") {
      nodes.forEach(function (n) {
        n.style.removeProperty("display");
      });
      if (addBtn) addBtn.style.removeProperty("display");
      return;
    }
    var can = !!currentMeta.can_manage_members;
    nodes.forEach(function (n) {
      n.style.display = can ? "" : "none";
    });
    if (addBtn) addBtn.style.display = can ? "" : "none";
  }

  function applyGroupTheme(meta) {
    if (!el.app) return;
    if (meta && meta.type === "group" && meta.group_settings) {
      const a = meta.group_settings.appearance || "cyan";
      if (a === "cyan") delete el.app.dataset.groupTheme;
      else el.app.dataset.groupTheme = a;
    } else {
      delete el.app.dataset.groupTheme;
    }
  }

  function focusMessageInScroll(mid) {
    const sc = el.scroll;
    if (!sc || !mid) return;
    const n = sc.querySelector('.chats-msg-wrap[data-mid="' + String(mid) + '"]');
    if (!n) return;
    n.classList.add("ch-msg-focus");
    try {
      n.scrollIntoView({ block: "center", behavior: "smooth" });
    } catch (e) {
      n.scrollIntoView(true);
    }
    setTimeout(function () {
      n.classList.remove("ch-msg-focus");
    }, 2400);
  }

  async function openChat(cid, opts) {
    activeChatId = cid;
    pendingFocusMessageId = opts && opts.focusMessageId ? parseInt(opts.focusMessageId, 10) : null;
    if (pendingFocusMessageId && !Number.isFinite(pendingFocusMessageId)) pendingFocusMessageId = null;
    setMobileOpen(true);
    el.placeholder.style.display = "none";
    el.thread.style.display = "flex";
    renderList();
    await loadMeta(cid);
    if (pendingFocusMessageId) {
      const am = pendingFocusMessageId;
      pendingFocusMessageId = null;
      await loadMessages(cid, null, { aroundMessageId: am });
      setTimeout(function () {
        focusMessageInScroll(am);
      }, 80);
    } else {
      await loadMessages(cid, null);
    }
    connectWs(cid);
    resizeTa();
    syncChatsViewport();
    if (!opts || !opts.focusMessageId) scrollMessagesToEnd();
  }

  async function loadMeta(cid) {
    const r = await api("/api/chats/" + cid + "/meta");
    currentMeta = await r.json();
    if (!r.ok) return;
    el.headTitle.textContent = currentMeta.name || "Чат";
    el.headAv.src = currentMeta.avatar_url || "/static/favicon.svg";
    applyGroupTheme(currentMeta);
    if (el.headHit) {
      if (currentMeta.type === "group") {
        el.headHit.disabled = false;
        el.headHit.style.display = "flex";
      } else {
        el.headHit.disabled = true;
        el.headHit.style.display = "flex";
      }
    }
    if (currentMeta.type === "group") {
      const n = currentMeta.member_count || 0;
      const on = (currentMeta.online_user_ids || []).length;
      el.headSub.textContent = n + " участников" + (on ? " · " + on + " онлайн" : "");
      el.headSub.classList.remove("online");
      el.menuBtn.style.display = "block";
    } else {
      const p = currentMeta.partner;
      const oid = p && p.id;
      const online = oid && (currentMeta.online_user_ids || []).indexOf(oid) >= 0;
      el.headSub.textContent = online ? "онлайн" : "не в сети";
      el.headSub.classList.toggle("online", !!online);
      el.menuBtn.style.display = "none";
    }
    syncGroupMemberChrome();
  }

  async function loadMessages(cid, beforeId, extra) {
    loadingMore = !!beforeId;
    let url = "/api/chats/" + cid + "/messages?limit=50";
    if (extra && extra.aroundMessageId && !beforeId) {
      url = "/api/chats/" + cid + "/messages?limit=80&around_message_id=" + encodeURIComponent(extra.aroundMessageId);
    } else if (beforeId) url += "&before_id=" + beforeId;
    const r = await api(url);
    const d = await r.json();
    if (!r.ok) return;
    const incoming = d.messages || [];
    hasMoreOlder = d.has_more;
    if (beforeId) {
      const prevH = el.scroll.scrollHeight;
      messages = incoming.concat(messages);
      renderMessages();
      el.scroll.scrollTop = el.scroll.scrollHeight - prevH;
    } else {
      messages = incoming;
      renderMessages();
      scrollMessagesToEnd();
    }
    loadingMore = false;
    await loadChats();
    if (!beforeId) {
      try {
        if (typeof updateUnreadCount === "function") updateUnreadCount();
        if (typeof refreshAppHeaderBadges === "function") refreshAppHeaderBadges();
      } catch (e) {}
    }
  }

  function renderMessages() {
    el.scroll.innerHTML = "";
    let lastDay = "";
    const reactionsOff =
      currentMeta &&
      currentMeta.type === "group" &&
      currentMeta.group_settings &&
      currentMeta.group_settings.reactions_mode === "none";
    messages.forEach((m, i) => {
      const day = m.created_at ? fmtDay(m.created_at) : "";
      if (day && day !== lastDay) {
        lastDay = day;
        const div = document.createElement("div");
        div.className = "chats-day";
        div.textContent = day;
        el.scroll.appendChild(div);
      }
      const mine = m.user_id === uid;
      const wrap = document.createElement("div");
      wrap.className = "chats-msg-wrap" + (mine ? " mine" : "");
      wrap.dataset.mid = String(m.id);

      const actions = document.createElement("div");
      actions.className = "chats-msg-actions";
      actions.innerHTML =
        (reactionsOff
          ? ""
          : '<button type="button" data-a="react" title="Реакция">😊</button>') +
        '<button type="button" data-a="reply" title="Ответить">↩️</button>' +
        (mine ? '<button type="button" data-a="del" title="Удалить">🗑️</button>' : "");
      actions.querySelectorAll("button").forEach((b) => {
        b.onclick = (ev) => {
          ev.stopPropagation();
          const a = b.getAttribute("data-a");
          if (a === "reply") {
            replyToId = m.id;
            showReplyBar(m);
          }
          if (a === "del") deleteMsg(m.id);
          if (a === "react") quickReact(m.id, "👍");
        };
      });

      const inner = document.createElement("div");
      inner.className = "chats-msg-inner";
      if (!mine) {
        const img = document.createElement("img");
        img.className = "chats-msg-av";
        img.src = m.sender_avatar || "/static/favicon.svg";
        img.alt = "";
        inner.appendChild(img);
      }
      const col = document.createElement("div");
      const bubble = document.createElement("div");
      bubble.className = "chats-bubble";
      if (!mine) {
        const nm = document.createElement("div");
        nm.style.cssText = "font-size:12px;color:#3dd4e0;margin-bottom:6px;font-weight:600";
        nm.textContent = m.sender_name || "Участник";
        bubble.appendChild(nm);
      }
      if (m.reply_preview) {
        const rp = document.createElement("div");
        rp.className = "chats-reply-bar";
        rp.innerHTML =
          "<strong>" +
          esc(m.reply_preview.sender_name || "…") +
          "</strong>" +
          bubbleMsgHtml(m.reply_preview.text || "");
        bubble.appendChild(rp);
      }
      if (m.text) {
        const t = document.createElement("div");
        t.innerHTML = bubbleMsgHtml(m.text);
        bubble.appendChild(t);
      }
      if (m.media_url) {
        const im = document.createElement("img");
        im.className = "chats-msg-media";
        im.src = m.media_url;
        im.alt = "";
        bubble.appendChild(im);
      }
      const meta = document.createElement("div");
      meta.className = "chats-msg-meta";
      meta.textContent = fmtTime(m.created_at);
      col.appendChild(bubble);
      col.appendChild(meta);

      const reactRow = document.createElement("div");
      reactRow.className = "chats-reactions";
      const counts = m.reactions || {};
      Object.keys(counts).forEach((em) => {
        if (reactionsOff) {
          const span = document.createElement("span");
          span.className = "chats-react-pill";
          span.textContent = em + " " + counts[em];
          reactRow.appendChild(span);
        } else {
          const pill = document.createElement("button");
          pill.type = "button";
          pill.className = "chats-react-pill" + ((m.my_reactions || []).includes(em) ? " active" : "");
          pill.textContent = em + " " + counts[em];
          pill.onclick = () => toggleReact(m.id, em);
          reactRow.appendChild(pill);
        }
      });
      col.appendChild(reactRow);

      inner.appendChild(col);
      wrap.appendChild(actions);
      wrap.appendChild(inner);
      el.scroll.appendChild(wrap);
    });
  }

  function showReplyBar(m) {
    el.replyBar.classList.add("on");
    el.replyBar.innerHTML =
      'Ответ на: <strong>' +
      esc(m.sender_name || "") +
      "</strong> — " +
      esc((m.text || "").slice(0, 80)) +
      " <button type=\"button\" id=\"chatsReplyCancel2\" style=\"float:right;background:none;border:none;color:#3dd4e0;cursor:pointer\">✕</button>";
    document.getElementById("chatsReplyCancel2").onclick = clearReply;
  }

  function clearReply() {
    replyToId = null;
    el.replyBar.classList.remove("on");
    el.replyBar.innerHTML = "";
  }

  async function toggleReact(mid, emoji) {
    const r = await api("/api/chats/" + activeChatId + "/messages/" + mid + "/react", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ emoji }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) return;
    const m = messages.find((x) => x.id === mid);
    if (m && d.counts !== undefined) {
      m.reactions = d.counts;
      m.my_reactions = d.my_reactions || [];
      renderMessages();
    }
  }

  async function quickReact(mid, emoji) {
    await toggleReact(mid, emoji);
  }

  async function deleteMsg(mid) {
    if (!confirm("Удалить сообщение?")) return;
    await api("/api/chats/" + activeChatId + "/messages/" + mid, { method: "DELETE" });
    messages = messages.filter((x) => x.id !== mid);
    renderMessages();
  }

  function chShowView(name) {
    if (!el.groupShell) return;
    el.groupShell.querySelectorAll(".ch-view").forEach(function (v) {
      v.hidden = v.getAttribute("data-ch-view") !== name;
    });
  }

  function closeGroupShell() {
    if (!el.groupShell) return;
    el.groupShell.hidden = true;
    document.body.style.overflow = "";
    document.body.classList.remove("ch-group-shell-open");
  }

  function openGroupShell(tab) {
    if (!el.groupShell || !activeChatId || !currentMeta || currentMeta.type !== "group") return;
    el.groupShell.hidden = false;
    document.body.style.overflow = "hidden";
    document.body.classList.add("ch-group-shell-open");
    chShowView("hub");
    switchGroupTab(tab || "participants");
    refreshParticipantsList();
    loadGroupMedia();
    syncMuteBtn();
    if (el.shellHubTitle) el.shellHubTitle.textContent = currentMeta.name || "Группа";
  }

  function switchGroupTab(tab) {
    const p = tab === "participants";
    if (el.tabParticipants) el.tabParticipants.classList.toggle("on", p);
    if (el.tabMedia) el.tabMedia.classList.toggle("on", !p);
    if (el.tabBodyParticipants) el.tabBodyParticipants.hidden = !p;
    if (el.tabBodyMedia) el.tabBodyMedia.hidden = p;
    if (p) refreshParticipantsList();
    else loadGroupMedia();
  }

  function syncMuteBtn() {
    if (!el.quickMuteIcWrap || !el.quickMuteLbl || !currentMeta) return;
    const m = !!currentMeta.mute_notifications;
    el.quickMuteIcWrap.innerHTML =
      '<svg class="ch-ic-svg" aria-hidden="true"><use href="' +
      (m ? "#ch-ic-bell-off" : "#ch-ic-bell") +
      '"/></svg>';
    el.quickMuteLbl.textContent = m ? "без звука" : "звук";
  }

  async function patchGroup(body) {
    const r = await api("/api/chats/" + activeChatId + "/group", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const d = await r.json().catch(function () {
      return {};
    });
    if (!r.ok) return false;
    if (d.group_settings) currentMeta.group_settings = d.group_settings;
    if (d.name) currentMeta.name = d.name;
    if (d.description !== undefined) currentMeta.description = d.description;
    if (d.avatar_url !== undefined) currentMeta.avatar_url = d.avatar_url;
    await loadMeta(activeChatId);
    renderMessages();
    return true;
  }

  function appearanceLabel(code) {
    if (code === "gold") return "Золото";
    if (code === "violet") return "Фиолет";
    return "Бирюза";
  }

  function fillSettingsRows() {
    if (!currentMeta || currentMeta.type !== "group") return;
    const st = currentMeta.group_settings || {};
    const perms = st.permissions || {};
    if (el.setName) el.setName.value = currentMeta.name || "";
    if (el.setDesc) el.setDesc.value = currentMeta.description || "";
    if (el.setPublicVal) el.setPublicVal.textContent = st.is_public ? "Публичная" : "Приватная";
    if (el.setReactVal) el.setReactVal.textContent = st.reactions_mode === "none" ? "Выкл." : "Все реакции";
    if (el.setAppearVal) el.setAppearVal.textContent = appearanceLabel(st.appearance || "cyan");
    if (el.setTopicsVal) el.setTopicsVal.textContent = st.topics_enabled ? "Включены" : "Отключены";
    if (el.setMembersCount) el.setMembersCount.textContent = String(currentMeta.member_count || 0);
    if (el.setAdminCount) el.setAdminCount.textContent = String(currentMeta.admin_count || 0);
    if (el.setBanCount) el.setBanCount.textContent = String(currentMeta.ban_count || 0);
    if (el.setPermsScore) {
      el.setPermsScore.textContent = currentMeta.permissions_score || "14/14";
    }
  }

  function openSettingsDeep() {
    chShowView("settings");
    fillSettingsRows();
  }

  async function refreshParticipantsList() {
    if (!activeChatId || !el.partList) return;
    const r = await api("/api/chats/" + activeChatId + "/members");
    const d = await r.json();
    if (!r.ok) return;
    membersCache = d.members || [];
    renderParticipantsFiltered();
  }

  function renderParticipantsFiltered() {
    if (!el.partList) return;
    const qraw = (el.partSearch && el.partSearch.value) || "";
    let q = qraw.trim();
    while (q.charAt(0) === "@") q = q.slice(1).trim();
    const ql = q.toLowerCase();
    const can = currentMeta && currentMeta.can_manage_members;
    const myRole = (currentMeta && currentMeta.my_role) || "member";
    el.partList.innerHTML = "";
    membersCache.forEach(function (m) {
      const nm = (m.name || "User " + m.user_id).toLowerCase();
      const idstr = String(m.user_id);
      if (ql && nm.indexOf(ql) < 0 && idstr.indexOf(ql) < 0) return;
      const row = document.createElement("div");
      row.className = "ch-part-row";
      const av = m.avatar || "/static/favicon.svg";
      const showDel =
        can &&
        m.user_id !== uid &&
        m.role !== "owner" &&
        (myRole === "owner" || (myRole === "admin" && m.role !== "admin"));
      const showBan = showDel;
      row.innerHTML =
        '<img src="' +
        esc(av) +
        '" alt="" onerror="this.src=\'/static/favicon.svg\'">' +
        '<div class="ch-part-meta"><strong>' +
        esc(m.name || "User " + m.user_id) +
        "</strong><span>id " +
        m.user_id +
        " · " +
        esc(m.role) +
        "</span></div>";
      const actions = document.createElement("div");
      actions.style.cssText = "display:flex;flex-direction:column;gap:6px;align-items:flex-end;";
      if (showDel) {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "ch-part-del";
        b.textContent = "Удалить";
        b.onclick = async function () {
          if (!confirm("Удалить из группы?")) return;
          const rr = await api("/api/chats/" + activeChatId + "/members/" + m.user_id, { method: "DELETE" });
          if (rr.ok) {
            refreshParticipantsList();
            loadMeta(activeChatId);
          } else alert("Не удалось");
        };
        actions.appendChild(b);
      }
      if (showBan) {
        const bb = document.createElement("button");
        bb.type = "button";
        bb.className = "ch-part-del";
        bb.style.borderColor = "#444";
        bb.style.color = "#ccc";
        bb.textContent = "В бан";
        bb.onclick = async function () {
          if (!confirm("Заблокировать и удалить из группы?")) return;
          const rr = await api("/api/chats/" + activeChatId + "/bans", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ user_id: m.user_id }),
          });
          if (rr.ok) {
            refreshParticipantsList();
            loadMeta(activeChatId);
          } else alert("Не удалось");
        };
        actions.appendChild(bb);
      }
      if (actions.children.length) row.appendChild(actions);
      el.partList.appendChild(row);
    });
    if (!el.partList.children.length) {
      el.partList.innerHTML = '<p style="color:#888;font-size:13px;padding:12px">Никого не найдено.</p>';
    }
  }

  async function loadGroupMedia() {
    if (!el.mediaGrid || !activeChatId) return;
    const r = await api("/api/chats/" + activeChatId + "/media");
    const d = await r.json();
    if (!r.ok) return;
    const items = d.items || [];
    el.mediaGrid.innerHTML = "";
    const byDay = {};
    items.forEach(function (it) {
      const day = it.created_at ? fmtDay(it.created_at) : "—";
      if (!byDay[day]) byDay[day] = [];
      byDay[day].push(it);
    });
    Object.keys(byDay).forEach(function (day) {
      const h = document.createElement("div");
      h.className = "ch-media-date";
      h.textContent = day;
      el.mediaGrid.appendChild(h);
      const tiles = document.createElement("div");
      tiles.className = "ch-media-tiles";
      byDay[day].forEach(function (it) {
        const img = document.createElement("img");
        img.src = it.media_url;
        img.alt = "";
        img.onclick = function () {
          closeGroupShell();
          openChat(activeChatId, { focusMessageId: it.message_id });
        };
        tiles.appendChild(img);
      });
      el.mediaGrid.appendChild(tiles);
    });
    if (!items.length) {
      el.mediaGrid.innerHTML = '<p style="color:#888;font-size:13px;padding:16px">Пока нет фото в чате.</p>';
    }
  }

  function openAddMemberModal() {
    if (!el.addMemberBack) return;
    el.addMemberBack.hidden = false;
    if (el.addMemberSearch) {
      el.addMemberSearch.value = "";
      el.addMemberSearch.focus();
    }
    if (el.addMemberResults) el.addMemberResults.innerHTML = "";
  }

  function closeAddMemberModal() {
    if (el.addMemberBack) el.addMemberBack.hidden = true;
  }

  async function runAddMemberSearch() {
    if (!el.addMemberSearch || !el.addMemberResults || !activeChatId) return;
    let q = (el.addMemberSearch.value || "").trim();
    if (q.length < 1) {
      el.addMemberResults.innerHTML = "";
      return;
    }
    const r = await api("/api/chats/search-users?q=" + encodeURIComponent(q));
    const d = await r.json();
    el.addMemberResults.innerHTML = "";
    (d.users || []).forEach(function (u) {
      const row = document.createElement("div");
      row.className = "ch-add-pick";
      row.innerHTML =
        '<img class="chats-row-av" style="width:40px;height:40px" src="' +
        esc(u.avatar || "/static/favicon.svg") +
        '" alt="">' +
        "<div><strong>" +
        esc(u.name || "User " + u.id) +
        "</strong><br><span style=\"font-size:12px;color:#888\">id " +
        u.id +
        "</span></div>";
      row.onclick = async function () {
        const rr = await api("/api/chats/" + activeChatId + "/members", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ user_id: u.id }),
        });
        const x = await rr.json().catch(function () {
          return {};
        });
        if (rr.ok) {
          closeAddMemberModal();
          loadMeta(activeChatId);
          refreshParticipantsList();
        } else alert(x.error || "Ошибка");
      };
      el.addMemberResults.appendChild(row);
    });
    if (!el.addMemberResults.children.length) {
      el.addMemberResults.innerHTML = '<p style="color:#888;font-size:13px;padding:8px">Никого не найдено.</p>';
    }
  }

  async function renderPermsEditor() {
    if (!el.permsList || !currentMeta) return;
    const st = currentMeta.group_settings || {};
    const perms = Object.assign({}, st.permissions || {});
    el.permsList.innerHTML = "";
    PERM_KEYS.forEach(function (k) {
      const row = document.createElement("div");
      row.className = "ch-perm-row";
      const lab = PERM_LABELS[k] || k;
      const span = document.createElement("span");
      span.textContent = lab;
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = !!perms[k];
      cb.dataset.key = k;
      row.appendChild(span);
      row.appendChild(cb);
      el.permsList.appendChild(row);
    });
  }

  async function loadBansView() {
    if (!el.bansList || !activeChatId) return;
    const r = await api("/api/chats/" + activeChatId + "/bans");
    const d = await r.json();
    if (!r.ok) return;
    el.bansList.innerHTML = "";
    (d.bans || []).forEach(function (b) {
      const row = document.createElement("div");
      row.className = "ch-part-row";
      row.innerHTML =
        '<img src="' +
        esc(b.avatar || "/static/favicon.svg") +
        '" alt="">' +
        '<div class="ch-part-meta"><strong>' +
        esc(b.name || "User " + b.user_id) +
        "</strong><span>id " +
        b.user_id +
        "</span></div>";
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "ch-part-del";
      btn.textContent = "Снять";
      btn.onclick = async function () {
        await api("/api/chats/" + activeChatId + "/bans/" + b.user_id, { method: "DELETE" });
        loadBansView();
        loadMeta(activeChatId);
      };
      row.appendChild(btn);
      el.bansList.appendChild(row);
    });
    if (!el.bansList.children.length) {
      el.bansList.innerHTML = '<p style="color:#888;font-size:13px">Пусто.</p>';
    }
  }

  async function loadAuditView() {
    if (!el.auditList || !activeChatId) return;
    const r = await api("/api/chats/" + activeChatId + "/audit");
    const d = await r.json();
    if (!r.ok) return;
    el.auditList.innerHTML = "";
    (d.events || []).forEach(function (ev) {
      const div = document.createElement("div");
      div.className = "ch-audit-item";
      div.textContent =
        (ev.created_at || "") +
        " · " +
        (ev.actor_name || ev.actor_id || "—") +
        " · " +
        (ev.action || "") +
        (ev.detail ? " " + ev.detail : "");
      el.auditList.appendChild(div);
    });
  }

  async function loadAdminsView() {
    if (!el.adminsList || !activeChatId) return;
    const r = await api("/api/chats/" + activeChatId + "/members");
    const d = await r.json();
    if (!r.ok) return;
    const mems = d.members || [];
    el.adminsList.innerHTML = "";
    const isOwner = currentMeta && currentMeta.my_role === "owner";
    const hTop = document.createElement("div");
    hTop.className = "ch-media-date";
    hTop.textContent = "Администраторы";
    el.adminsList.appendChild(hTop);
    mems.forEach(function (m) {
      if (m.role !== "admin" && m.role !== "owner") return;
      const row = document.createElement("div");
      row.className = "ch-part-row";
      row.innerHTML =
        '<img src="' +
        esc(m.avatar || "/static/favicon.svg") +
        '" alt="">' +
        '<div class="ch-part-meta"><strong>' +
        esc(m.name || "") +
        "</strong><span>" +
        esc(m.role) +
        "</span></div>";
      if (isOwner && m.role === "admin") {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "ch-part-del";
        b.textContent = "Снять";
        b.onclick = async function () {
          await api("/api/chats/" + activeChatId + "/members/" + m.user_id + "/role", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ role: "member" }),
          });
          loadAdminsView();
          loadMeta(activeChatId);
        };
        row.appendChild(b);
      }
      el.adminsList.appendChild(row);
    });
    if (isOwner) {
      const h2 = document.createElement("div");
      h2.className = "ch-media-date";
      h2.textContent = "Участники → админ";
      h2.style.marginTop = "12px";
      el.adminsList.appendChild(h2);
      mems.forEach(function (m) {
        if (m.role !== "member") return;
        const row = document.createElement("div");
        row.className = "ch-part-row";
        row.innerHTML =
          '<img src="' +
          esc(m.avatar || "/static/favicon.svg") +
          '" alt="">' +
          '<div class="ch-part-meta"><strong>' +
          esc(m.name || "") +
          '</strong><span>id ' +
          m.user_id +
          "</span></div>";
        const b = document.createElement("button");
        b.type = "button";
        b.className = "ch-part-del";
        b.textContent = "Админ";
        b.onclick = async function () {
          await api("/api/chats/" + activeChatId + "/members/" + m.user_id + "/role", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ role: "admin" }),
          });
          loadAdminsView();
          loadMeta(activeChatId);
        };
        row.appendChild(b);
        el.adminsList.appendChild(row);
      });
    }
  }

  function connectWs(cid) {
    if (ws) {
      try {
        ws.close();
      } catch (e) {}
    }
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(proto + "//" + location.host + "/ws/chats/" + cid);
    ws.onmessage = (ev) => {
      try {
        const d = JSON.parse(ev.data);
        if (d.type === "message" && d.payload) {
          if (messages.some((x) => x.id === d.payload.id)) return;
          messages.push(d.payload);
          renderMessages();
          scrollMessagesToEnd();
          loadChats();
        }
        if (d.type === "message_deleted" && d.payload) {
          messages = messages.filter((x) => x.id !== d.payload.id);
          renderMessages();
        }
        if (d.type === "reaction" && d.payload) {
          const mid = d.payload.message_id;
          const m = messages.find((x) => x.id === mid);
          if (m && d.payload.counts) {
            m.reactions = Object.assign({}, d.payload.counts);
            renderMessages();
          }
        }
        if (d.type === "members_changed") {
          if (activeChatId) loadMeta(activeChatId);
          if (el.groupShell && !el.groupShell.hidden && el.partList) refreshParticipantsList();
        }
      } catch (e) {}
    };
    if (hbTimer) clearInterval(hbTimer);
    hbTimer = setInterval(() => {
      try {
        if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "hb" }));
      } catch (e) {}
    }, 25000);
  }

  async function sendMessage() {
    if (!activeChatId) return;
    const text = (el.ta.value || "").trim();
    if (!text && !pendingMediaUrl) return;
    const body = { text, media_url: pendingMediaUrl, reply_to_id: replyToId };
    const r = await api("/api/chats/" + activeChatId + "/messages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const d = await r.json();
    if (r.ok && d.message) {
      if (!messages.some((x) => x.id === d.message.id)) messages.push(d.message);
      renderMessages();
      scrollMessagesToEnd();
    }
    el.ta.value = "";
    pendingMediaUrl = null;
    clearReply();
    resizeTa();
    loadChats();
  }

  function resizeTa() {
    if (!el.ta) return;
    el.ta.style.height = "auto";
    const max = 120;
    el.ta.style.height = Math.min(el.ta.scrollHeight, max) + "px";
    if (isChatsMobile()) syncChatsViewport();
  }

  if (el.ta) {
    el.ta.addEventListener("input", resizeTa);
    el.ta.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    });
    el.ta.addEventListener("focus", function () {
      document.body.classList.add("chats-input-focus");
      if (window.__nfSyncViewportKb) window.__nfSyncViewportKb();
      syncChatsViewport();
      setTimeout(function () {
        if (window.__nfSyncViewportKb) window.__nfSyncViewportKb();
        syncChatsViewport();
        scrollMessagesToEnd();
        try {
          if (el.compose && el.compose.scrollIntoView) {
            el.compose.scrollIntoView({ block: "end", behavior: "smooth" });
          } else {
            el.ta.scrollIntoView({ block: "center", behavior: "smooth" });
          }
        } catch (e) {}
      }, 120);
      setTimeout(function () {
        if (window.__nfSyncViewportKb) window.__nfSyncViewportKb();
        syncChatsViewport();
        scrollMessagesToEnd();
      }, 350);
    });
    el.ta.addEventListener("blur", function () {
      document.body.classList.remove("chats-input-focus");
      setTimeout(syncChatsViewport, 180);
    });
  }
  if (el.send) el.send.onclick = sendMessage;

  if (el.attach && el.file) {
    el.attach.onclick = () => el.file.click();
    el.file.onchange = async () => {
      const f = el.file.files && el.file.files[0];
      if (!f) return;
      const fd = new FormData();
      fd.append("file", f);
      const r = await api("/api/chats/upload", { method: "POST", body: fd });
      const d = await r.json();
      if (d.url) pendingMediaUrl = d.url;
      el.file.value = "";
      if (pendingMediaUrl) sendMessage();
    };
  }

  el.scroll &&
    el.scroll.addEventListener("scroll", () => {
      if (el.scroll.scrollTop < 80 && !loadingMore && hasMoreOlder && messages.length) {
        const oldest = messages[0].id;
        loadMessages(activeChatId, oldest);
      }
    });

  if (el.search) el.search.oninput = () => renderList();

  if (el.mobileBack) {
    el.mobileBack.onclick = () => {
      activeChatId = null;
      el.thread.style.display = "none";
      el.placeholder.style.display = "flex";
      setMobileOpen(false);
      renderList();
      if (ws) {
        try {
          ws.close();
        } catch (e) {}
      }
      syncChatsViewport();
    };
  }

  if (el.menuBtn && el.drop) {
    el.menuBtn.onclick = (e) => {
      e.stopPropagation();
      el.drop.classList.toggle("open");
    };
    document.addEventListener("click", () => el.drop.classList.remove("open"));
  }

  document.getElementById("chatsMenuSettings") &&
    (document.getElementById("chatsMenuSettings").onclick = function () {
      el.drop.classList.remove("open");
      openGroupShell("participants");
    });

  document.getElementById("chatsMenuAdd") &&
    (document.getElementById("chatsMenuAdd").onclick = function () {
      el.drop.classList.remove("open");
      openAddMemberModal();
    });

  document.getElementById("chatsMenuLeave") &&
    (document.getElementById("chatsMenuLeave").onclick = async () => {
      el.drop.classList.remove("open");
      if (!confirm("Выйти из группы?")) return;
      await api("/api/chats/" + activeChatId + "/members/me", { method: "DELETE" });
      location.reload();
    });

  function openModal() {
    el.modal.classList.add("open");
    el.tabPersonal.classList.add("on");
    el.tabGroup.classList.remove("on");
    el.panePersonal.style.display = "block";
    el.paneGroup.style.display = "none";
  }
  function closeModal() {
    el.modal.classList.remove("open");
  }

  if (el.btnNew) el.btnNew.onclick = openModal;
  if (el.modalClose) el.modalClose.onclick = closeModal;
  el.modal &&
    el.modal.addEventListener("click", (e) => {
      if (e.target === el.modal) closeModal();
    });

  if (el.tabPersonal && el.tabGroup) {
    el.tabPersonal.onclick = () => {
      el.tabPersonal.classList.add("on");
      el.tabGroup.classList.remove("on");
      el.panePersonal.style.display = "block";
      el.paneGroup.style.display = "none";
    };
    el.tabGroup.onclick = () => {
      el.tabGroup.classList.add("on");
      el.tabPersonal.classList.remove("on");
      el.paneGroup.style.display = "block";
      el.panePersonal.style.display = "none";
      el.gMembers.innerHTML = '<p style="font-size:13px;color:#888;margin:0">Введите имя в поле поиска выше.</p>';
    };
  }

  let gSearchT = null;
  if (el.gSearch) {
    el.gSearch.oninput = () => {
      clearTimeout(gSearchT);
      gSearchT = setTimeout(async () => {
        const q = (el.gSearch.value || "").trim();
        el.gMembers.innerHTML = "";
        if (q.length < 1) {
          el.gMembers.innerHTML = '<p style="font-size:13px;color:#888">Минимум 1 символ для поиска.</p>';
          return;
        }
        const r = await api("/api/chats/search-users?q=" + encodeURIComponent(q));
        const d = await r.json();
        selectedGroupIds = new Set();
        (d.users || []).forEach((u) => {
          if (u.id === uid) return;
          const lab = document.createElement("label");
          lab.style.cssText =
            "display:flex;align-items:center;gap:10px;padding:8px;border-radius:8px;cursor:pointer;border:1px solid #222;margin-bottom:6px";
          const cb = document.createElement("input");
          cb.type = "checkbox";
          cb.value = String(u.id);
          cb.onchange = () => {
            if (cb.checked) selectedGroupIds.add(u.id);
            else selectedGroupIds.delete(u.id);
          };
          lab.appendChild(cb);
          lab.appendChild(document.createTextNode((u.name || "") + " (" + u.id + ")"));
          el.gMembers.appendChild(lab);
        });
        if (!el.gMembers.children.length) el.gMembers.innerHTML = '<p style="color:#888;font-size:13px">Никого не найдено.</p>';
      }, 280);
    };
  }

  let searchT = null;
  if (el.userSearch) {
    el.userSearch.oninput = () => {
      clearTimeout(searchT);
      searchT = setTimeout(doUserSearch, 300);
    };
  }

  async function doUserSearch() {
    const q = (el.userSearch.value || "").trim();
    el.userResults.innerHTML = "";
    if (q.length < 1) return;
    const r = await api("/api/chats/search-users?q=" + encodeURIComponent(q));
    const d = await r.json();
    (d.users || []).forEach((u) => {
      const row = document.createElement("div");
      row.className = "chats-user-pick";
      row.innerHTML =
        '<img class="chats-row-av" style="width:40px;height:40px" src="' +
        esc(u.avatar || "/static/favicon.svg") +
        '" alt="">' +
        "<div><strong>" +
        esc(u.name || "User " + u.id) +
        "</strong><br><span style=\"font-size:12px;color:#888\">id " +
        u.id +
        "</span></div>";
      row.onclick = async () => {
        const cr = await api("/api/chats/personal/" + u.id, { method: "POST" });
        const cj = await cr.json();
        closeModal();
        if (cj.chat_id) await openChat(cj.chat_id);
      };
      el.userResults.appendChild(row);
    });
  }

  if (el.gCreate) {
    el.gCreate.onclick = async () => {
      const name = (el.gName.value || "").trim();
      if (!name) {
        alert("Укажите название группы");
        return;
      }
      let avatar_url = null;
      const gf = el.gFile.files && el.gFile.files[0];
      if (gf) {
        const fd = new FormData();
        fd.append("file", gf);
        const ur = await api("/api/chats/upload", { method: "POST", body: fd });
        const uj = await ur.json();
        avatar_url = uj.url || null;
      }
      const member_ids = Array.from(selectedGroupIds);
      const r = await api("/api/chats/group", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, avatar_url, member_ids }),
      });
      const d = await r.json();
      closeModal();
      if (d.chat_id) await openChat(d.chat_id);
      else alert(d.error || "Не удалось создать");
    };
  }

  if (el.headHit) {
    el.headHit.addEventListener("click", function () {
      if (!currentMeta || currentMeta.type !== "group") return;
      openGroupShell("participants");
    });
  }

  if (el.addMemberBack) {
    el.addMemberBack.addEventListener("click", function (e) {
      if (e.target === el.addMemberBack) closeAddMemberModal();
    });
  }
  if (el.addMemberClose) el.addMemberClose.addEventListener("click", closeAddMemberModal);
  if (el.addMemberSearch) {
    el.addMemberSearch.addEventListener("input", function () {
      clearTimeout(addMemberSearchT);
      addMemberSearchT = setTimeout(runAddMemberSearch, 280);
    });
  }

  if (el.shellClose) el.shellClose.addEventListener("click", closeGroupShell);
  if (el.tabParticipants)
    el.tabParticipants.addEventListener("click", function () {
      switchGroupTab("participants");
    });
  if (el.tabMedia)
    el.tabMedia.addEventListener("click", function () {
      switchGroupTab("media");
    });
  if (el.partSearch) {
    el.partSearch.addEventListener("input", function () {
      clearTimeout(partSearchT);
      partSearchT = setTimeout(renderParticipantsFiltered, 200);
    });
  }

  if (el.openDeepSettings) {
    el.openDeepSettings.addEventListener("click", function () {
      openSettingsDeep();
    });
  }

  if (el.quickVideo) {
    el.quickVideo.addEventListener("click", function () {
      if (!activeChatId) return;
      window.open("https://meet.jit.si/NeuroFungiChat-" + activeChatId, "_blank", "noopener,noreferrer");
    });
  }
  if (el.quickMute) {
    el.quickMute.addEventListener("click", async function () {
      if (!activeChatId || !currentMeta) return;
      const next = !currentMeta.mute_notifications;
      const r = await api("/api/chats/" + activeChatId + "/members/me/mute", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ muted: next }),
      });
      if (r.ok) {
        await loadMeta(activeChatId);
        syncMuteBtn();
      }
    });
  }
  if (el.quickSearch) {
    el.quickSearch.addEventListener("click", function () {
      chShowView("chatSearch");
      if (el.chatSearchInput) el.chatSearchInput.focus();
    });
  }
  if (el.quickMore) {
    el.quickMore.addEventListener("click", function () {
      openSettingsDeep();
    });
  }

  if (el.chatSearchInput) {
    el.chatSearchInput.addEventListener("input", function () {
      clearTimeout(chatSearchT);
      chatSearchT = setTimeout(async function () {
        if (!el.chatSearchResults || !activeChatId) return;
        const q = (el.chatSearchInput.value || "").trim();
        el.chatSearchResults.innerHTML = "";
        if (q.length < 1) return;
        const r = await api("/api/chats/" + activeChatId + "/messages/search?q=" + encodeURIComponent(q));
        const d = await r.json();
        (d.results || []).forEach(function (hit) {
          const div = document.createElement("div");
          div.className = "ch-search-hit";
          div.innerHTML =
            "<strong>#" +
            hit.id +
            "</strong> · " +
            esc(hit.sender_name || "") +
            "<br><span style=\"font-size:12px;color:#888\">" +
            esc(hit.text || "") +
            "</span>";
          div.onclick = function () {
            closeGroupShell();
            openChat(activeChatId, { focusMessageId: hit.id });
          };
          el.chatSearchResults.appendChild(div);
        });
      }, 320);
    });
  }

  if (el.togglePublic) {
    el.togglePublic.addEventListener("click", async function () {
      const st = (currentMeta && currentMeta.group_settings) || {};
      await patchGroup({ is_public: !st.is_public });
      fillSettingsRows();
    });
  }
  if (el.toggleReactions) {
    el.toggleReactions.addEventListener("click", async function () {
      const st = (currentMeta && currentMeta.group_settings) || {};
      const next = st.reactions_mode === "none" ? "all" : "none";
      await patchGroup({ reactions_mode: next });
      fillSettingsRows();
    });
  }
  if (el.cycleAppearance) {
    el.cycleAppearance.addEventListener("click", async function () {
      const st = (currentMeta && currentMeta.group_settings) || {};
      const order = ["cyan", "gold", "violet"];
      const cur = st.appearance || "cyan";
      const i = order.indexOf(cur);
      const next = order[(i + 1) % order.length];
      await patchGroup({ appearance: next });
      fillSettingsRows();
    });
  }
  if (el.toggleTopics) {
    el.toggleTopics.addEventListener("click", async function () {
      const st = (currentMeta && currentMeta.group_settings) || {};
      await patchGroup({ topics_enabled: !st.topics_enabled });
      fillSettingsRows();
    });
  }

  if (el.settingsSave) {
    el.settingsSave.addEventListener("click", async function () {
      const name = el.setName ? el.setName.value.trim() : "";
      const description = el.setDesc ? el.setDesc.value.trim() : "";
      if (!name) {
        alert("Укажите название");
        return;
      }
      await patchGroup({ name: name, description: description });
      fillSettingsRows();
      chShowView("hub");
    });
  }

  if (el.permsSave) {
    el.permsSave.addEventListener("click", async function () {
      if (!el.permsList) return;
      const permissions = {};
      el.permsList.querySelectorAll("input[type=checkbox]").forEach(function (cb) {
        permissions[cb.dataset.key] = cb.checked;
      });
      await patchGroup({ permissions: permissions });
      chShowView("settings");
      fillSettingsRows();
    });
  }

  if (el.deleteGroup) {
    el.deleteGroup.addEventListener("click", async function () {
      if (!activeChatId) return;
      if (!confirm("Удалить группу безвозвратно?")) return;
      const r = await api("/api/chats/" + activeChatId, { method: "DELETE" });
      if (r.ok) {
        closeGroupShell();
        location.reload();
      } else alert("Не удалось удалить");
    });
  }

  if (el.groupShell) {
    el.groupShell.addEventListener("click", function (ev) {
      const back = ev.target.closest("[data-ch-back]");
      if (back) {
        const v = back.getAttribute("data-ch-back");
        if (v) chShowView(v);
        return;
      }
      const nav = ev.target.closest(".ch-set-row[data-nav]");
      if (nav && nav.dataset.nav && nav.dataset.nav !== "noop") {
        const n = nav.dataset.nav;
        if (n === "participants") {
          chShowView("hub");
          switchGroupTab("participants");
        } else if (n === "perms") {
          renderPermsEditor();
          chShowView("perms");
        } else if (n === "admins") {
          loadAdminsView();
          chShowView("admins");
        } else if (n === "bans") {
          loadBansView();
          chShowView("bans");
        } else if (n === "audit") {
          loadAuditView();
          chShowView("audit");
        }
      }
    });
  }

  bindChatsViewport();

  loadChats().then(() => {
    syncChatsViewport();
    if (openChatParam) {
      const cid = parseInt(openChatParam, 10);
      if (cid) {
        const fm = focusMsgParam ? parseInt(focusMsgParam, 10) : NaN;
        openChat(cid, Number.isFinite(fm) ? { focusMessageId: fm } : {});
      }
    } else if (openUserParam) {
      const oid = parseInt(openUserParam, 10);
      if (oid)
        api("/api/chats/personal/" + oid, { method: "POST" }).then((r) =>
          r.json().then((cj) => {
            if (cj.chat_id) openChat(cj.chat_id);
          })
        );
    }
  });
})();
