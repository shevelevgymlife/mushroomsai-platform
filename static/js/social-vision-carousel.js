(function () {
  "use strict";

  function prefersReducedMotion() {
    try {
      return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    } catch (e) {
      return false;
    }
  }

  function nearestIndex(track, slides) {
    var rect = track.getBoundingClientRect();
    var cx = rect.left + rect.width / 2;
    var best = 0;
    var bestD = Infinity;
    for (var i = 0; i < slides.length; i++) {
      var r = slides[i].getBoundingClientRect();
      var d = Math.abs(r.left + r.width / 2 - cx);
      if (d < bestD) {
        bestD = d;
        best = i;
      }
    }
    return best;
  }

  function update(root, track, slides, dots, parallax) {
    if (!slides.length) return;
    var idx = nearestIndex(track, slides);
    for (var i = 0; i < slides.length; i++) {
      slides[i].classList.toggle("is-active", i === idx);
    }
    for (var j = 0; j < dots.length; j++) {
      dots[j].classList.toggle("is-active", j === idx);
    }
    if (parallax) {
      parallax.style.setProperty("--nf-parallax-x", track.scrollLeft + "px");
    }
  }

  function buildDots(host, slides, track) {
    host.innerHTML = "";
    var dots = [];
    for (var i = 0; i < slides.length; i++) {
      (function (index) {
        var b = document.createElement("button");
        b.type = "button";
        b.className = "nf-vp-carousel__dot";
        b.setAttribute("aria-label", "Слайд " + (index + 1));
        b.addEventListener("click", function () {
          slides[index].scrollIntoView({ inline: "center", block: "nearest", behavior: "smooth" });
        });
        host.appendChild(b);
        dots.push(b);
      })(i);
    }
    return dots;
  }

  function nudge(track) {
    if (prefersReducedMotion()) return;
    setTimeout(function () {
      track.scrollBy({ left: 14, behavior: "smooth" });
      setTimeout(function () {
        track.scrollBy({ left: -14, behavior: "smooth" });
      }, 620);
    }, 350);
  }

  function bind(root) {
    var track = root.querySelector(".nf-vp-carousel__track");
    if (!track) return null;
    var parallax = root.querySelector(".nf-vp-carousel__parallax");
    var dotsHost = root.querySelector(".nf-vp-carousel__dots");
    var slides = track.querySelectorAll(".nf-vp-carousel__slide");

    var dots = [];
    if (dotsHost && slides.length) {
      dots = buildDots(dotsHost, slides, track);
    }

    var raf = 0;
    function onScroll() {
      if (raf) cancelAnimationFrame(raf);
      raf = requestAnimationFrame(function () {
        raf = 0;
        update(root, track, slides, dots, parallax);
      });
    }

    track.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);

    var prev = root.querySelector(".nf-vp-carousel__arrow--prev");
    var next = root.querySelector(".nf-vp-carousel__arrow--next");
    if (prev) {
      prev.addEventListener("click", function () {
        track.scrollBy({ left: -Math.max(120, track.clientWidth * 0.35), behavior: "smooth" });
      });
    }
    if (next) {
      next.addEventListener("click", function () {
        track.scrollBy({ left: Math.max(120, track.clientWidth * 0.35), behavior: "smooth" });
      });
    }

    root.addEventListener(
      "touchstart",
      function () {
        root.classList.add("nf-vp-touch");
      },
      { passive: true }
    );

    if (!root.dataset.nfNudged && window.matchMedia("(max-width: 900px)").matches) {
      root.dataset.nfNudged = "1";
      nudge(track);
    }

    update(root, track, slides, dots, parallax);

    return function cleanup() {
      track.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }

  var registry = new WeakMap();

  function init(root) {
    if (!root || root.nodeType !== 1) return;
    var prev = registry.get(root);
    if (typeof prev === "function") prev();
    var fn = bind(root);
    registry.set(root, fn || function () {});
  }

  function scan() {
    document.querySelectorAll("[data-nf-vp-carousel]").forEach(init);
  }

  document.addEventListener("DOMContentLoaded", scan);
  if (document.readyState === "complete" || document.readyState === "interactive") {
    setTimeout(scan, 0);
  }

  window.nfVisionCarouselRefresh = function (root) {
    if (root) init(root);
    else scan();
  };
})();
