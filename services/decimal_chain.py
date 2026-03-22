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
