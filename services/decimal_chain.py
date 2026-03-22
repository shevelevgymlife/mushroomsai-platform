"""Чтение нативного баланса DEL через JSON-RPC Decimal Smart Chain."""
from __future__ import annotations

import httpx

from config import settings


def _rpc_url() -> str:
    u = (getattr(settings, "DECIMAL_RPC_URL", None) or "https://node.decimalchain.com/web3/").strip()
    return u if u.endswith("/") else u + "/"


async def fetch_native_del_balance(address: str) -> float | None:
    """Возвращает баланс DEL (native) или None при ошибке."""
    if not address or not isinstance(address, str):
        return None
    addr = address.strip()
    if not addr.startswith("0x") or len(addr) < 10:
        return None
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_getBalance",
        "params": [addr, "latest"],
        "id": 1,
    }
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.post(_rpc_url(), json=payload)
            r.raise_for_status()
            data = r.json()
    except Exception:
        return None
    res = data.get("result")
    if not res or not isinstance(res, str) or not res.startswith("0x"):
        return None
    try:
        wei = int(res, 16)
    except ValueError:
        return None
    return wei / 10**18


def _pad_addr_param(addr: str) -> str:
    h = addr.strip().lower().removeprefix("0x")
    return h.rjust(64, "0")


async def _eth_call(to_contract: str, data: str) -> str | None:
    to_contract = to_contract.strip()
    if not to_contract.startswith("0x"):
        to_contract = "0x" + to_contract
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_call",
        "params": [{"to": to_contract, "data": data}, "latest"],
        "id": 1,
    }
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.post(_rpc_url(), json=payload)
            r.raise_for_status()
            data_j = r.json()
    except Exception:
        return None
    if data_j.get("error"):
        return None
    res = data_j.get("result")
    if not res or not isinstance(res, str) or not res.startswith("0x"):
        return None
    return res


async def fetch_erc20_balance(token_address: str, wallet_address: str) -> float | None:
    """balanceOf(wallet) через eth_call; decimals читаем отдельно."""
    if not token_address or not wallet_address:
        return None
    w = wallet_address.strip()
    if not w.startswith("0x"):
        return None
    tok = token_address.strip()
    if not tok.startswith("0x"):
        tok = "0x" + tok
    data_bal = "0x70a08231" + _pad_addr_param(w)
    raw_hex = await _eth_call(tok, data_bal)
    if raw_hex is None:
        return None
    try:
        raw = int(raw_hex, 16)
    except ValueError:
        return None
    dec_hex = await _eth_call(tok, "0x313ce567")
    dec = 18
    if dec_hex and dec_hex.startswith("0x"):
        try:
            d = int(dec_hex, 16)
            if 0 <= d <= 36:
                dec = d
        except ValueError:
            pass
    return raw / (10**dec)
