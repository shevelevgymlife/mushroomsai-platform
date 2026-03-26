(function () {
  "use strict";
  (function () {
    function applyVvh() {
      var vv = window.visualViewport;
      var h = vv ? vv.height : window.innerHeight;
      document.documentElement.style.setProperty("--vvh", h + "px");
    }
    applyVvh();
    window.addEventListener("resize", applyVvh);
    if (window.visualViewport) window.visualViewport.addEventListener("resize", applyVvh);
  })();

  var WP = window.__WALLET_PAGE__ || {};
  var DSC_CHAIN_ID = "0x4B";
  var DSC_PARAMS = {
    chainId: DSC_CHAIN_ID,
    chainName: "Decimal Smart Chain",
    nativeCurrency: { name: "DEL", symbol: "DEL", decimals: 18 },
    rpcUrls: ["https://node.decimalchain.com/web3/"],
    blockExplorerUrls: ["https://explorer.decimalchain.com"],
  };
  var SHEVELEV_TOKEN = String(WP.shevelevToken || "").trim();
  var SHEVELEV_AUTO_MS = 45000;
  var _shevelevAutoTimer = null;
  var _shevelevVisBound = false;

  function applyTokenLampState(isOn) {
    var swSettings = document.getElementById("tokenLampToggleSettings");
    var on = !!isOn;
    if (swSettings && swSettings.checked !== on) swSettings.checked = on;
  }

  async function saveTokenLampEnabled(isOn) {
    applyTokenLampState(!!isOn);
    try {
      var r = await fetch("/profile/token-lamp", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ token_lamp_enabled: !!isOn }),
      });
      var d = await r.json().catch(function () {
        return {};
      });
      if (!r.ok || !d.ok) throw new Error(d.error || "Не удалось сохранить");
    } catch (e) {
      applyTokenLampState(!isOn);
      showNotification((e && e.message) || "Не удалось сохранить свет", "error");
    }
  }

  function showNotification(msg, type) {
    type = type || "info";
    var colors = {
      info: "rgba(61,212,224,.92)",
      error: "rgba(239,68,68,.92)",
      success: "rgba(74,222,128,.92)",
    };
    var n = document.createElement("div");
    n.style.cssText =
      "position:fixed;top:20px;right:16px;z-index:9999;background:" +
      (colors[type] || colors.info) +
      ";color:#000;padding:10px 18px;border-radius:10px;font-size:13px;font-weight:700;box-shadow:0 4px 20px rgba(0,0,0,.5);max-width:min(280px,92vw)";
    n.textContent = msg;
    document.body.appendChild(n);
    setTimeout(function () {
      n.remove();
    }, 3500);
  }

  function getDashWallet() {
    var h = document.getElementById("dashWalletAddr");
    if (h && h.value && h.value.trim()) return h.value.trim();
    var inp = document.getElementById("wltInp");
    return inp && inp.value.trim() ? inp.value.trim() : "";
  }

  function initShevelevOnLoad() {
    if (!window.ethereum) return;
    var w = getDashWallet();
    if (!w) return;
    loadDelNative(w);
    if (SHEVELEV_TOKEN) loadSHEVELEVBalance(w);
  }

  async function loadDelNative(addr) {
    if (!addr || !window.ethereum) return;
    var el = document.getElementById("delBal");
    if (!el) return;
    el.textContent = "…";
    try {
      try {
        await window.ethereum.request({ method: "wallet_switchEthereumChain", params: [{ chainId: DSC_CHAIN_ID }] });
      } catch (se) {
        if (se && se.code === 4902) {
          try {
            await window.ethereum.request({ method: "wallet_addEthereumChain", params: [DSC_PARAMS] });
          } catch (_) {}
        }
      }
      var hex = await window.ethereum.request({ method: "eth_getBalance", params: [addr, "latest"] });
      var wei = BigInt(!hex || hex === "0x" ? "0" : hex);
      var human = Number(wei) / 1e18;
      var num = Number.isFinite(human) ? human.toLocaleString("ru-RU", { maximumFractionDigits: 8 }) : "0";
      el.textContent = num + " DEL";
    } catch (e) {
      console.error("DEL:", e);
      el.textContent = "—";
    }
  }

  async function erc20Decimals(token) {
    try {
      var r = await window.ethereum.request({ method: "eth_call", params: [{ to: token, data: "0x313ce567" }, "latest"] });
      if (!r || r === "0x") return 18;
      var n = parseInt(r, 16);
      return Number.isFinite(n) && n >= 0 && n <= 36 ? n : 18;
    } catch (_) {
      return 18;
    }
  }

  async function connectMetaMask() {
    if (!window.ethereum) {
      var mob = /iPhone|Android|iPad/i.test(navigator.userAgent);
      if (mob) {
        window.location.href =
          "https://metamask.app.link/dapp/" + encodeURIComponent(window.location.origin + "/account/wallet");
      } else showNotification("Установите MetaMask в браузер", "error");
      return;
    }
    try {
      var accs = await window.ethereum.request({ method: "eth_requestAccounts" });
      var addr = accs[0];
      try {
        await window.ethereum.request({ method: "wallet_switchEthereumChain", params: [{ chainId: DSC_CHAIN_ID }] });
      } catch (se) {
        if (se.code === 4902) {
          try {
            await window.ethereum.request({ method: "wallet_addEthereumChain", params: [DSC_PARAMS] });
          } catch (ae) {
            showNotification("Не удалось добавить сеть Decimal", "error");
            return;
          }
        }
      }
      var wi = document.getElementById("wltInp");
      if (wi) wi.value = addr;
      await saveWallet(addr);
      showNotification("Кошелёк подключён ✓", "success");
    } catch (e) {
      if (e.code !== 4001) showNotification("Ошибка: " + (e.message || e), "error");
    }
  }

  async function loadSHEVELEVBalance(addr) {
    if (!SHEVELEV_TOKEN || !addr || !window.ethereum) return;
    function errEl() {
      return document.getElementById("shevelevBalErrMe") || document.getElementById("shevelevBalErr");
    }
    function setErr(msg, hide) {
      var e = errEl();
      if (!e) return;
      if (hide) {
        e.style.display = "none";
        e.textContent = "";
      } else {
        e.style.display = "block";
        e.textContent = msg;
      }
    }
    var el = document.getElementById("shevelevBal");
    setErr("", true);
    if (el) el.textContent = "…";
    try {
      var dec = await erc20Decimals(SHEVELEV_TOKEN);
      var pad = addr.replace(/^0x/i, "").toLowerCase().padStart(64, "0");
      var data = "0x70a08231" + pad;
      var res = await window.ethereum.request({ method: "eth_call", params: [{ to: SHEVELEV_TOKEN, data: data }, "latest"] });
      var wei = BigInt(!res || res === "0x" ? "0" : res);
      var human = Number(wei) / Math.pow(10, dec);
      var txt =
        (Number.isFinite(human) ? human.toLocaleString("ru-RU", { maximumFractionDigits: 8 }) : "0") + " SHEVELEV";
      if (el) el.textContent = txt;
    } catch (e) {
      console.error("SHEVELEV:", e);
      if (el) el.textContent = "—";
      setErr(
        "Не удалось прочитать баланс. В MetaMask выберите сеть Decimal Smart Chain (chain id 75 / 0x4B), где находится контракт SHEVELEV.",
        false
      );
    }
  }

  async function saveWallet(addr) {
    var inp = document.getElementById("wltInp");
    var a = addr || (inp && inp.value.trim());
    if (!a) return;
    var r = await fetch("/profile/wallet", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ wallet_address: a }),
    });
    if (!r.ok) return;
    var hid = document.getElementById("dashWalletAddr");
    if (hid) hid.value = a;
    var sh = a.slice(0, 12) + "..." + a.slice(-4);
    var st = document.getElementById("wltSt");
    if (st) st.innerHTML = '<span style="color:#4ade80">✓ ' + sh + "</span>";
    if (window.ethereum) {
      loadDelNative(a);
      if (SHEVELEV_TOKEN) loadSHEVELEVBalance(a);
    }
    startShevelevAutoSync();
    syncBalancesServerSilent({ silent: true });
  }

  async function saveTokenVisibility() {
    var del = document.getElementById("pubShowDel");
    var shev = document.getElementById("pubShowShev");
    var ok = document.getElementById("tokVisOk");
    try {
      var r = await fetch("/profile/token-visibility", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({
          show_del_to_public: !!(del && del.checked),
          show_shev_to_public: !!(shev && shev.checked),
        }),
      });
      var d = await r.json().catch(function () {
        return {};
      });
      if (!r.ok) {
        showNotification(d.error || "Не удалось сохранить", "error");
        return;
      }
      if (ok) {
        ok.style.display = "inline";
        setTimeout(function () {
          ok.style.display = "none";
        }, 2200);
      }
      showNotification("Видимость токенов в профиле сохранена", "success");
    } catch (e) {
      showNotification("Ошибка сети", "error");
    }
  }

  async function syncBalancesServerSilent(opts) {
    opts = opts || {};
    var w = getDashWallet();
    if (!w || !w.startsWith("0x")) return;
    try {
      var r = await fetch("/profile/wallet/sync-balances", { method: "POST", credentials: "same-origin" });
      var d = await r.json();
      if (!r.ok || !d.ok) {
        if (!opts.silent && d && d.error) showNotification(d.error, "error");
        return;
      }
      var dSet = document.getElementById("delBal");
      if (dSet && d.del_formatted != null) dSet.textContent = String(d.del_formatted) + " DEL";
      var sSet = document.getElementById("shevelevBal");
      if (sSet && d.shevelev_formatted != null)
        sSet.textContent = String(d.shevelev_formatted) + (SHEVELEV_TOKEN ? " SHEVELEV" : "");
      var erM = document.getElementById("shevelevBalErrMe") || document.getElementById("shevelevBalErr");
      if (erM) {
        if (d.shevelev_error) {
          erM.textContent = d.shevelev_error;
          erM.style.display = "block";
        } else {
          erM.style.display = "none";
          erM.textContent = "";
        }
      }
      if (!opts.silent) {
        if (d.shevelev_error) showNotification("DEL обновлён. " + d.shevelev_error, "error");
        else showNotification("Балансы обновлены", "success");
      }
    } catch (e) {
      if (!opts.silent) console.error(e);
    }
  }

  function stopShevelevAutoSync() {
    if (_shevelevAutoTimer) {
      clearInterval(_shevelevAutoTimer);
      _shevelevAutoTimer = null;
    }
  }

  function _shevelevOnVis() {
    if (document.hidden) return;
    syncBalancesServerSilent({ silent: true });
  }

  function startShevelevAutoSync() {
    stopShevelevAutoSync();
    var w = getDashWallet();
    if (!w || !w.startsWith("0x")) return;
    if (!_shevelevVisBound) {
      document.addEventListener("visibilitychange", _shevelevOnVis);
      _shevelevVisBound = true;
    }
    var tick = function () {
      if (!document.hidden) syncBalancesServerSilent({ silent: true });
    };
    _shevelevAutoTimer = setInterval(tick, SHEVELEV_AUTO_MS);
    setTimeout(tick, 5000);
  }

  async function syncBlockchainBalances() {
    var el = document.getElementById("shevelevBalMe");
    var delEl = document.getElementById("delBalMe");
    var er = document.getElementById("shevelevBalErrMe") || document.getElementById("shevelevBalErr");
    if (er) {
      er.style.display = "none";
      er.textContent = "";
    }
    if (delEl) delEl.textContent = "…";
    if (el) el.textContent = "…";
    try {
      var r = await fetch("/profile/wallet/sync-balances", { method: "POST", credentials: "same-origin" });
      var d = await r.json();
      if (!d.ok) {
        if (er) {
          er.textContent = d.error || "Ошибка";
          er.style.display = "block";
        }
        if (delEl) delEl.textContent = "—";
        if (el) el.textContent = "—";
        showNotification(d.error || "Ошибка синхронизации", "error");
        return;
      }
      if (delEl) delEl.textContent = d.del_formatted != null ? String(d.del_formatted) : "—";
      if (el) {
        if (d.shevelev_formatted != null) el.textContent = String(d.shevelev_formatted);
        else if (d.shevelev_error) el.textContent = "—";
      }
      var dSet = document.getElementById("delBal");
      if (dSet && d.del_formatted != null) dSet.textContent = String(d.del_formatted) + " DEL";
      var sSet = document.getElementById("shevelevBal");
      if (sSet && d.shevelev_formatted != null)
        sSet.textContent = String(d.shevelev_formatted) + (SHEVELEV_TOKEN ? " SHEVELEV" : "");
      if (er) {
        if (d.shevelev_error) {
          er.textContent = d.shevelev_error;
          er.style.display = "block";
        } else {
          er.style.display = "none";
          er.textContent = "";
        }
      }
      if (d.shevelev_error) {
        showNotification("DEL сохранён. SHEVELEV: " + d.shevelev_error, "error");
      } else if (d.shevelev_formatted != null) {
        showNotification("Балансы DEL и SHEVELEV обновлены в профиле", "success");
      } else {
        showNotification("Баланс DEL обновлён в профиле", "success");
      }
    } catch (e) {
      if (er) {
        er.textContent = e.message || "Сеть";
        er.style.display = "block";
      }
      if (delEl) delEl.textContent = "—";
      if (el) el.textContent = "—";
    }
  }

  function openSendShevModal() {
    var m = document.getElementById("sendShevModal");
    if (m) m.style.display = "flex";
    var st = document.getElementById("sendShevSt");
    if (st) {
      st.textContent = "";
      st.style.color = "#888";
    }
  }

  function _shevParseAmountToWei(s, decimals) {
    var t = String(s).replace(",", ".").trim();
    if (!/^\d+(\.\d+)?$/.test(t)) return null;
    var parts = t.split(".");
    var ip = parts[0];
    var fp = parts[1] || "";
    var frac = (fp + "0".repeat(decimals)).slice(0, decimals).padEnd(decimals, "0");
    try {
      return BigInt(ip || "0") * BigInt(10) ** BigInt(decimals) + BigInt(frac);
    } catch (_) {
      return null;
    }
  }

  function _shevEncodeTransfer(toAddr, wei) {
    var addr = toAddr.replace(/^0x/i, "").toLowerCase().padStart(64, "0");
    var v = BigInt(wei);
    if (v < 0n) return null;
    var hex = v.toString(16).padStart(64, "0");
    return "0xa9059cbb" + addr + hex;
  }

  async function submitSendShev() {
    var st = document.getElementById("sendShevSt");
    if (st) {
      st.textContent = "";
      st.style.color = "#888";
    }
    if (!window.ethereum) {
      if (st) {
        st.style.color = "#f87171";
        st.textContent = "Нужен MetaMask";
      }
      return;
    }
    if (!SHEVELEV_TOKEN) {
      if (st) {
        st.style.color = "#f87171";
        st.textContent = "Контракт SHEVELEV не настроен на сервере";
      }
      return;
    }
    var to = (document.getElementById("sendShevTo") && document.getElementById("sendShevTo").value || "").trim();
    var rawAmt = (document.getElementById("sendShevAmt") && document.getElementById("sendShevAmt").value || "").trim();
    if (!/^0x[a-fA-F0-9]{40}$/i.test(to)) {
      if (st) {
        st.style.color = "#f87171";
        st.textContent = "Укажите адрес получателя (0x + 40 hex)";
      }
      return;
    }
    var dec = await erc20Decimals(SHEVELEV_TOKEN);
    var wei = _shevParseAmountToWei(rawAmt, dec);
    if (wei == null || wei <= 0n) {
      if (st) {
        st.style.color = "#f87171";
        st.textContent = "Укажите положительную сумму";
      }
      return;
    }
    var data = _shevEncodeTransfer(to, wei);
    if (!data) {
      if (st) {
        st.style.color = "#f87171";
        st.textContent = "Слишком большая сумма";
      }
      return;
    }
    try {
      var accs = await window.ethereum.request({ method: "eth_requestAccounts" });
      var from = accs[0];
      try {
        await window.ethereum.request({ method: "wallet_switchEthereumChain", params: [{ chainId: DSC_CHAIN_ID }] });
      } catch (se) {
        if (se && se.code === 4902) {
          await window.ethereum.request({ method: "wallet_addEthereumChain", params: [DSC_PARAMS] });
        } else if (se && se.code !== 4001) throw se;
      }
      if (st) st.textContent = "Подтвердите транзакцию в MetaMask…";
      var txh = await window.ethereum.request({
        method: "eth_sendTransaction",
        params: [{ from: from, to: SHEVELEV_TOKEN, data: data }],
      });
      if (st) {
        st.style.color = "#4ade80";
        st.textContent = "Отправлено: " + String(txh).slice(0, 18) + "…";
      }
      try {
        await fetch("/profile/shevelev-transfer-notify", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({ to: to, tx_hash: String(txh), amount: rawAmt }),
        });
      } catch (_) {}
      syncBalancesServerSilent({ silent: true });
      setTimeout(function () {
        syncBalancesServerSilent({ silent: true });
      }, 4000);
    } catch (e) {
      if (e && e.code === 4001) {
        if (st) {
          st.style.color = "#888";
          st.textContent = "Отменено";
        }
        return;
      }
      if (st) {
        st.style.color = "#f87171";
        st.textContent = e.message || String(e);
      }
    }
  }

  window.connectMetaMask = connectMetaMask;
  window.saveWallet = saveWallet;
  window.saveTokenVisibility = saveTokenVisibility;
  window.syncBlockchainBalances = syncBlockchainBalances;
  window.openSendShevModal = openSendShevModal;
  window.submitSendShev = submitSendShev;
  window.saveTokenLampEnabled = saveTokenLampEnabled;

  function boot() {
    initShevelevOnLoad();
    startShevelevAutoSync();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
