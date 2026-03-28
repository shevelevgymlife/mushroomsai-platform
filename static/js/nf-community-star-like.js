/**
 * Лайк звездой: анимация + опционально переход на /community/post/{id}/stars
 * (на странице звёзд — поиск с @ среди лайкнувших: nf-post-likers-at-search.js)
 * Если уже лайкнуто — POST не вызываем (чтобы не снять лайк).
 */
(function () {
  function playStarFall(then) {
    var o = document.createElement("div");
    o.className = "nf-star-fall-overlay";
    o.setAttribute("aria-hidden", "true");
    o.innerHTML = '<div class="nf-star-fall-emoji" role="presentation">⭐</div>';
    document.body.appendChild(o);
    requestAnimationFrame(function () {
      o.classList.add("nf-star-fall-on");
    });
    setTimeout(function () {
      try {
        if (then) then();
      } finally {
        o.remove();
      }
    }, 820);
  }

  /**
   * @param {number} postId
   * @param {{ wasLiked?: boolean, backUrl?: string, skipNavigate?: boolean, onCounts?: (d:object)=>void }} opts
   */
  window.NF_communityStarLike = function (postId, opts) {
    opts = opts || {};
    var was = !!opts.wasLiked;
    var back = encodeURIComponent(opts.backUrl || location.href || "/community");
    var finish = function () {
      if (!opts.skipNavigate) {
        location.href = "/community/post/" + postId + "/stars?back=" + back;
      }
    };
    playStarFall(function () {
      if (was) {
        finish();
        return;
      }
      fetch("/community/like/" + postId, { method: "POST", credentials: "same-origin" })
        .then(function (r) {
          return r.json().catch(function () {
            return {};
          });
        })
        .then(function (d) {
          if (typeof opts.onCounts === "function") opts.onCounts(d);
          finish();
        })
        .catch(function () {
          finish();
        });
    });
  };

  window.NF_openPostReposts = function (postId, backUrl) {
    var b = encodeURIComponent(backUrl || location.href || "/community");
    location.href = "/community/post/" + postId + "/reposts?back=" + b;
  };
})();
