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
  var _sendShevLastAddr = "";
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
        "Не удалось прочитать баланс. В расширении кошелька выберите сеть Decimal Smart Chain (chain id 75 / 0x4B), где находится контракт SHEVELEV.",
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

  function _copyTextToClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise(function (resolve, reject) {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy");
        resolve();
      } catch (e) {
        reject(e);
      } finally {
        document.body.removeChild(ta);
      }
    });
  }

  function _sendShevPayloadText() {
    var sel = document.getElementById("sendShevRecipient");
    var opt = sel && sel.selectedOptions && sel.selectedOptions[0];
    var addr = (opt && opt.getAttribute("data-address")) || _sendShevLastAddr || "";
    addr = String(addr).trim();
    var rawAmt = (document.getElementById("sendShevAmt") && document.getElementById("sendShevAmt").value || "").trim();
    if (!addr) return "";
    if (rawAmt) return addr + "\n" + rawAmt + " SHEVELEV";
    return addr;
  }

  async function openSendShevModal() {
    var m = document.getElementById("sendShevModal");
    if (m) m.style.display = "flex";
    var st = document.getElementById("sendShevSt");
    if (st) {
      st.textContent = "";
      st.style.color = "#888";
    }
    var disp = document.getElementById("sendShevAddrDisplay");
    if (disp) disp.textContent = "—";
    _sendShevLastAddr = "";
    var sel = document.getElementById("sendShevRecipient");
    if (!sel) return;
    sel.innerHTML = '<option value="">Загрузка…</option>';
    sel.disabled = true;
    try {
      var r = await fetch("/profile/wallet-recipients", { credentials: "same-origin" });
      var d = await r.json().catch(function () {
        return {};
      });
      if (!r.ok || !d.ok) throw new Error((d && d.error) || "Ошибка загрузки");
      var list = d.recipients || [];
      sel.innerHTML = '<option value="">Выберите получателя</option>';
      list.forEach(function (rec) {
        var o = document.createElement("option");
        o.value = String(rec.id);
        o.setAttribute("data-address", rec.wallet_address);
        var shortA = rec.wallet_address.slice(0, 10) + "…" + rec.wallet_address.slice(-4);
        o.textContent = (rec.name || "Участник") + " — " + shortA;
        sel.appendChild(o);
      });
      if (!list.length) {
        sel.innerHTML = '<option value="">Нет аккаунтов с адресом кошелька</option>';
      }
    } catch (e) {
      sel.innerHTML = '<option value="">Не удалось загрузить список</option>';
      if (st) {
        st.style.color = "#f87171";
        st.textContent = (e && e.message) || "Ошибка сети";
      }
    }
    sel.disabled = false;
  }

  async function onSendShevRecipientChange() {
    var st = document.getElementById("sendShevSt");
    var disp = document.getElementById("sendShevAddrDisplay");
    var sel = document.getElementById("sendShevRecipient");
    var opt = sel && sel.selectedOptions && sel.selectedOptions[0];
    var addr = opt && opt.getAttribute("data-address");
    addr = addr ? String(addr).trim() : "";
    if (!addr) {
      _sendShevLastAddr = "";
      if (disp) disp.textContent = "—";
      return;
    }
    _sendShevLastAddr = addr;
    if (disp) disp.textContent = addr;
    try {
      await _copyTextToClipboard(addr);
      if (st) {
        st.style.color = "#4ade80";
        st.textContent = "Адрес скопирован в буфер обмена";
      }
      showNotification("Адрес скопирован", "success");
    } catch (e) {
      if (st) {
        st.style.color = "#f87171";
        st.textContent = "Не удалось скопировать адрес";
      }
    }
  }

  function onSendShevAmountInput() {
    var st = document.getElementById("sendShevSt");
    if (st && st.textContent && st.textContent.indexOf("скопирован") !== -1) {
      st.textContent = "";
      st.style.color = "#888";
    }
  }

  async function copySendShevPayload() {
    var st = document.getElementById("sendShevSt");
    var t = _sendShevPayloadText();
    if (!t) {
      if (st) {
        st.style.color = "#f87171";
        st.textContent = "Выберите получателя";
      }
      showNotification("Выберите получателя", "error");
      return;
    }
    try {
      await _copyTextToClipboard(t);
      if (st) {
        st.style.color = "#4ade80";
        st.textContent = "Адрес и сумма скопированы в буфер обмена";
      }
      showNotification("Скопировано в буфер обмена", "success");
    } catch (e) {
      if (st) {
        st.style.color = "#f87171";
        st.textContent = "Не удалось скопировать";
      }
      showNotification("Не удалось скопировать", "error");
    }
  }

  async function openDecimalWalletStore(which) {
    var st = document.getElementById("sendShevSt");
    var t = _sendShevPayloadText();
    if (!t) {
      if (st) {
        st.style.color = "#f87171";
        st.textContent = "Выберите получателя";
      }
      showNotification("Выберите получателя", "error");
      return;
    }
    try {
      await _copyTextToClipboard(t);
      if (st) {
        st.style.color = "#4ade80";
        st.textContent = "Данные скопированы. Откройте приложение и вставьте в перевод.";
      }
    } catch (e) {
      if (st) {
        st.style.color = "#f87171";
        st.textContent = "Не удалось скопировать";
      }
    }
    var url =
      which === "android"
        ? String(WP.decimalWalletAndroid || "").trim()
        : String(WP.decimalWalletIos || "").trim();
    if (url) window.open(url, "_blank", "noopener,noreferrer");
  }

  window.saveWallet = saveWallet;
  window.saveTokenVisibility = saveTokenVisibility;
  window.syncBlockchainBalances = syncBlockchainBalances;
  window.openSendShevModal = openSendShevModal;
  window.onSendShevRecipientChange = onSendShevRecipientChange;
  window.onSendShevAmountInput = onSendShevAmountInput;
  window.copySendShevPayload = copySendShevPayload;
  window.openDecimalWalletStore = openDecimalWalletStore;
  window.saveTokenLampEnabled = saveTokenLampEnabled;

  function boot() {
    initShevelevOnLoad();
    startShevelevAutoSync();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
