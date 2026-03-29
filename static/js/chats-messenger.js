(function () {
  const ME = window.__CHATS_ME || {};
  const uid = ME.id;
  const qs = new URLSearchParams(location.search);
  const openUserParam = qs.get("open_user");

  const el = {
    app: document.getElementById("chatsApp"),
    list: document.getElementById("chatsList"),
    search: document.getElementById("chatsSidebarSearch"),
    main: document.getElementById("chatsMain"),
    placeholder: document.getElementById("chatsPlaceholder"),
    thread: document.getElementById("chatsThread"),
    scroll: document.getElementById("chatsScroll"),
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
  };

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

  function renderList() {
    const q = (el.search && el.search.value) || "";
    const ql = q.trim().toLowerCase();
    el.list.innerHTML = "";
    chats
      .filter((c) => !ql || (c.name || "").toLowerCase().includes(ql))
      .forEach((c) => {
        const row = document.createElement("div");
        row.className = "chats-row" + (c.id === activeChatId ? " active" : "");
        row.dataset.id = String(c.id);
        const av = c.avatar_url || "/static/favicon.svg";
        const badge = c.unread > 0 ? " on" : "";
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
      });
  }

  function setMobileOpen(on) {
    if (!el.app) return;
    el.app.classList.toggle("mobile-chat-open", on);
    syncChatsViewport();
  }

  async function openChat(cid) {
    activeChatId = cid;
    setMobileOpen(true);
    el.placeholder.style.display = "none";
    el.thread.style.display = "flex";
    renderList();
    await loadMeta(cid);
    await loadMessages(cid, null);
    connectWs(cid);
    resizeTa();
    syncChatsViewport();
    scrollMessagesToEnd();
  }

  async function loadMeta(cid) {
    const r = await api("/api/chats/" + cid + "/meta");
    currentMeta = await r.json();
    if (!r.ok) return;
    el.headTitle.textContent = currentMeta.name || "Чат";
    el.headAv.src = currentMeta.avatar_url || "/static/favicon.svg";
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
  }

  async function loadMessages(cid, beforeId) {
    loadingMore = !!beforeId;
    let url = "/api/chats/" + cid + "/messages?limit=50";
    if (beforeId) url += "&before_id=" + beforeId;
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
  }

  function renderMessages() {
    el.scroll.innerHTML = "";
    let lastDay = "";
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
        '<button type="button" data-a="react" title="Реакция">😊</button>' +
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
        const pill = document.createElement("button");
        pill.type = "button";
        pill.className = "chats-react-pill" + ((m.my_reactions || []).includes(em) ? " active" : "");
        pill.textContent = em + " " + counts[em];
        pill.onclick = () => toggleReact(m.id, em);
        reactRow.appendChild(pill);
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

  document.getElementById("chatsMenuMembers") &&
    (document.getElementById("chatsMenuMembers").onclick = async () => {
      el.drop.classList.remove("open");
      const r = await api("/api/chats/" + activeChatId + "/members");
      const d = await r.json();
      const lines = (d.members || []).map((m) => m.name + " (" + m.user_id + ") — " + m.role);
      alert(lines.join("\n") || "Нет данных");
    });

  document.getElementById("chatsMenuAdd") &&
    (document.getElementById("chatsMenuAdd").onclick = () => {
      el.drop.classList.remove("open");
      const idStr = prompt("ID пользователя для приглашения:");
      const id = parseInt(idStr, 10);
      if (!id) return;
      api("/api/chats/" + activeChatId + "/members", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: id }),
      }).then((r) => {
        if (r.ok) alert("Участник добавлен");
        else r.json().then((x) => alert(x.error || "Ошибка"));
      });
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

  bindChatsViewport();

  loadChats().then(() => {
    syncChatsViewport();
    if (openUserParam) {
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
