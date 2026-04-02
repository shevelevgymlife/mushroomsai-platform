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
        mount.innerHTML = data.html;
        mountWellnessCharts(data.charts || []);
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
