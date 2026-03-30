/**
 * Упоминания @<числовой id> — как на сервере (services/event_notify).
 * Экранирует текст, затем делает @123 ссылкой на /community/profile/123
 * + linkifyChatPlain: URL и относительные /call/{uuid} в сообщениях чатов.
 */
(function () {
  var RE = /(?<![\w/])@(\d{1,12})\b/g;
  var CALL_PATH_RE = /\/call\/[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}/i;
  /** http(s), www., относительный /call/uuid */
  var CHAT_URL_RE =
    /(https?:\/\/[^\s<]+|www\.[^\s<]+|\/call\/[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})/gi;

  function mentionLink(id) {
    return (
      '<a class="nf-mention" href="/community/profile/' +
      id +
      '" title="Профиль участника">@' +
      id +
      "</a>"
    );
  }

  function escHtml(s) {
    var d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  function escAttr(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;");
  }

  function linkifyChatLine(line) {
    var s = String(line);
    var re = new RegExp(CHAT_URL_RE.source, "gi");
    var out = "";
    var last = 0;
    var m;
    while ((m = re.exec(s)) !== null) {
      out += window.linkifyMentionsInEscapedFragment(escHtml(s.slice(last, m.index)));
      var url = m[1];
      var href = url;
      if (/^www\./i.test(url)) href = "https://" + url;
      if (url.indexOf("/") === 0) href = (window.location && window.location.origin ? window.location.origin : "") + url;
      out +=
        '<a class="ch-msg-link nf-chat-url" href="' +
        escAttr(href) +
        '" target="_blank" rel="noopener noreferrer">' +
        escHtml(url) +
        "</a>";
      last = m.index + m[0].length;
    }
    out += window.linkifyMentionsInEscapedFragment(escHtml(s.slice(last)));
    return out;
  }

  /** Сырой текст → экранирование + ссылки @id */
  window.linkifyCommunityMentionsPlain = function (raw) {
    var d = document.createElement("div");
    d.textContent = raw == null ? "" : String(raw);
    var h = d.innerHTML;
    return h.replace(RE, function (_, id) {
      return mentionLink(id);
    });
  };

  /**
   * Уже экранированный HTML-фрагмент (например после esc() или разбиения URL) —
   * только подстановка @123 → ссылка на профиль.
   */
  window.linkifyMentionsInEscapedFragment = function (escapedHtml) {
    return String(escapedHtml || "").replace(RE, function (_, id) {
      return mentionLink(id);
    });
  };

  /** Сообщения чата: @упоминания + кликабельные URL (в т.ч. /call/uuid) */
  window.linkifyChatPlain = function (raw) {
    var text = raw == null ? "" : String(raw);
    var lines = text.split("\n");
    return lines.map(linkifyChatLine).join("<br>");
  };

  /** Приглашение на видеозвонок из ЛС (текст от /api/rtc/start-call) */
  window.isCallInviteMessageText = function (raw) {
    var s = String(raw || "");
    if (!CALL_PATH_RE.test(s)) return false;
    return /📹|вам\s+звонит|видеозвонок|принять\s+звонок/i.test(s);
  };

  window.renderCallInviteMessageHtml = function (raw) {
    if (window.__NF_VIDEO_CALLS__ === false) return null;
    if (!window.isCallInviteMessageText(raw)) return null;
    var s = String(raw || "");
    var hm = s.match(/(https?:\/\/[^\s]+\/call\/[0-9a-f-]{36}|\/call\/[0-9a-f-]{36})/i);
    if (!hm) return null;
    var callPath = hm[1];
    var href = callPath.indexOf("/") === 0 ? (window.location && window.location.origin ? window.location.origin : "") + callPath : callPath;
    var body = s
      .replace(/\n*\s*Принять\s+звонок\s*:\s*\S+.*$/gim, "")
      .replace(/\n*\s*https?:\/\/[^\s]+\/call\/[0-9a-f-]{36}\s*$/gim, "")
      .trim();
    var bodyHtml = body ? window.linkifyChatPlain(body) : '<span class="nf-call-invite-fallback">Входящий видеозвонок</span>';
    return (
      '<div class="nf-call-invite-card">' +
      '<div class="nf-call-invite-text">' +
      bodyHtml +
      "</div>" +
      '<div class="nf-call-invite-actions">' +
      '<a class="nf-call-btn nf-call-btn--yes" href="' +
      escAttr(href) +
      '">Принять звонок</a>' +
      '<button type="button" class="nf-call-btn nf-call-btn--no" data-nf-call-dismiss="1">Отклонить</button>' +
      "</div>" +
      "</div>"
    );
  };

  document.addEventListener(
    "click",
    function (e) {
      var btn = e.target && e.target.closest && e.target.closest("[data-nf-call-dismiss]");
      if (!btn) return;
      var card = btn.closest(".nf-call-invite-card");
      if (card) {
        card.classList.add("nf-call-invite-card--declined");
        e.preventDefault();
      }
    },
    true
  );
})();
