/**
 * Вкладки периода дашборда wellness: fetch JSON-фрагмента + history.pushState.
 * Требует Chart.js (глобальный Chart) на странице до вызова wellnessDashSpaInit.
 */
(function () {
  function destroyWellnessChartsInMount(mount) {
    if (!window.Chart || !mount) return;
    mount.querySelectorAll("canvas").forEach(function (canvas) {
      var ch = Chart.getChart(canvas);
      if (ch) ch.destroy();
    });
  }

  function mountWellnessCharts(list) {
    if (!window.Chart || !list || !list.length) return;
    list.forEach(function (item) {
      var c = document.getElementById(item.canvas_id);
      if (!c) return;
      var old = Chart.getChart(c);
      if (old) old.destroy();
      new Chart(c, item.config);
    });
  }

  var _carouselIo = null;

  function destroyWellnessChartCarousel(mount) {
    if (_carouselIo) {
      try {
        _carouselIo.disconnect();
      } catch (e) {}
      _carouselIo = null;
    }
    if (!mount) return;
    mount.querySelectorAll("[data-carousel-dots]").forEach(function (dots) {
      dots.innerHTML = "";
    });
  }

  function initWellnessChartCarousel(mount) {
    destroyWellnessChartCarousel(mount);
    if (!mount) return;
    var wrap = mount.querySelector(".wd-chart-carousel-wrap");
    if (!wrap) return;
    var car = wrap.querySelector("[data-wellness-chart-carousel]");
    var dots = wrap.querySelector("[data-carousel-dots]");
    var slides = wrap.querySelectorAll(".wd-chart-slide");
    if (!car || !dots || slides.length === 0) return;

    slides.forEach(function (_, i) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "wd-chart-dot" + (i === 0 ? " wd-chart-dot--active" : "");
      b.setAttribute("aria-label", "График " + (i + 1));
      b.setAttribute("role", "tab");
      b.addEventListener("click", function () {
        slides[i].scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
      });
      dots.appendChild(b);
    });

    var dotEls = dots.querySelectorAll(".wd-chart-dot");
    function setActive(idx) {
      dotEls.forEach(function (d, j) {
        d.classList.toggle("wd-chart-dot--active", j === idx);
      });
    }

    if (typeof IntersectionObserver === "undefined") {
      car.addEventListener(
        "scroll",
        function () {
          var w = car.clientWidth || 1;
          var idx = Math.round(car.scrollLeft / (w * 0.85));
          idx = Math.max(0, Math.min(slides.length - 1, idx));
          setActive(idx);
        },
        { passive: true }
      );
      return;
    }

    _carouselIo = new IntersectionObserver(
      function (entries) {
        var best = -1;
        var bestRatio = 0;
        entries.forEach(function (en) {
          if (!en.isIntersecting) return;
          var idx = Array.prototype.indexOf.call(slides, en.target);
          if (idx < 0) return;
          if (en.intersectionRatio > bestRatio) {
            bestRatio = en.intersectionRatio;
            best = idx;
          }
        });
        if (best >= 0) setActive(best);
      },
      { root: car, threshold: [0.35, 0.55, 0.75] }
    );
    slides.forEach(function (s) {
      _carouselIo.observe(s);
    });
  }

  function tabUrlToPartialJson(href) {
    var u = new URL(href, window.location.origin);
    u.searchParams.set("partial", "json");
    return u.toString();
  }

  function updateTabActive(mount, href) {
    if (!mount) return;
    var tabs = mount.querySelectorAll(".wd-tabs a.wd-tab-spa");
    tabs.forEach(function (a) {
      a.classList.remove("wd-active");
      if (a.getAttribute("href") === href) a.classList.add("wd-active");
    });
  }

  function loadFragment(url, hrefForTabs) {
    var mount = document.getElementById("wd-dash-mount");
    if (!mount) return Promise.reject();
    return fetch(url, {
      credentials: "same-origin",
      headers: { Accept: "application/json", "X-Requested-With": "XMLHttpRequest" },
    })
      .then(function (r) {
        if (!r.ok) throw new Error("bad status");
        return r.json();
      })
      .then(function (data) {
        if (!data || typeof data.html !== "string") throw new Error("bad payload");
        destroyWellnessChartsInMount(mount);
        destroyWellnessChartCarousel(mount);
        mount.innerHTML = data.html;
        mountWellnessCharts(data.charts || []);
        initWellnessChartCarousel(mount);
        try {
          document.dispatchEvent(new CustomEvent("wellness:fragment:mounted", { detail: { mount: mount } }));
        } catch (e) {}
        if (hrefForTabs) updateTabActive(mount, hrefForTabs);
      });
  }

  function wellnessDashMountFromPopstate() {
    var mount = document.getElementById("wd-dash-mount");
    if (!mount || mount.getAttribute("data-wellness-spa") !== "1") return;
    var u = new URL(window.location.href);
    u.searchParams.set("partial", "json");
    loadFragment(u.toString(), window.location.pathname + window.location.search).catch(function () {
      window.location.reload();
    });
  }

  window.wellnessDashSpaInit = function (opts) {
    opts = opts || {};
    var mount = document.getElementById("wd-dash-mount");
    if (!mount || mount.getAttribute("data-wellness-spa") !== "1") return;

    if (opts.initialCharts && opts.initialCharts.length && window.Chart) {
      mountWellnessCharts(opts.initialCharts);
    }
    initWellnessChartCarousel(mount);

    mount.addEventListener("click", function (e) {
      var a = e.target.closest(".wd-tabs a.wd-tab-spa");
      if (!a || !mount.contains(a)) return;
      var href = a.getAttribute("href");
      if (!href) return;
      e.preventDefault();
      var partialUrl = tabUrlToPartialJson(href);
      var canonical = new URL(href, window.location.origin);
      var pathQs = canonical.pathname + canonical.search;
      history.pushState({ wellnessDashSpa: 1 }, "", pathQs);
      loadFragment(partialUrl, href).catch(function () {
        window.location.href = href;
      });
    });

    window.addEventListener("popstate", wellnessDashMountFromPopstate);
  };
})();
