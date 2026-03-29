/**
 * Упоминания @<числовой id> — как на сервере (services/event_notify).
 * Экранирует текст, затем делает @123 ссылкой на /community/profile/123
 */
(function () {
  var RE = /(?<![\w/])@(\d{1,12})\b/g;
  function mentionLink(id) {
    return (
      '<a class="nf-mention" href="/community/profile/' +
      id +
      '" title="Профиль участника">@' +
      id +
      "</a>"
    );
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
})();
