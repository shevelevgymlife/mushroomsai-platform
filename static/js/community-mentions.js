/**
 * Упоминания @<числовой id> — как на сервере (services/event_notify).
 * Экранирует текст, затем делает @123 ссылкой на /community/profile/123
 */
(function () {
  var RE = /(?<![\w/])@(\d{1,12})\b/g;
  window.linkifyCommunityMentionsPlain = function (raw) {
    var d = document.createElement("div");
    d.textContent = raw == null ? "" : String(raw);
    var h = d.innerHTML;
    return h.replace(RE, function (_, id) {
      return (
        '<a class="nf-mention" href="/community/profile/' +
        id +
        '" title="Профиль участника">@' +
        id +
        "</a>"
      );
    });
  };
})();
