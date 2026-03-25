"""UI preferences parsed from users.screen_rim_json."""
from __future__ import annotations

import json
from typing import Any

DEFAULT_SCREEN_RIM: dict[str, Any] = {
    "on": False,
    "r": 61,
    "g": 212,
    "b": 224,
    "s": 0.55,
    "w": 0.35,
}


def attach_screen_rim_prefs(user_dict: dict) -> None:
    """Мутирует user_dict: добавляет ключ screen_rim (dict)."""
    raw = user_dict.get("screen_rim_json")
    if raw is None or raw == "":
        user_dict["screen_rim"] = DEFAULT_SCREEN_RIM.copy()
        return
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        out = DEFAULT_SCREEN_RIM.copy()
        if isinstance(data, dict):
            if "on" in data:
                out["on"] = bool(data["on"])
            if "r" in data:
                out["r"] = max(0, min(255, int(data["r"])))
            if "g" in data:
                out["g"] = max(0, min(255, int(data["g"])))
            if "b" in data:
                out["b"] = max(0, min(255, int(data["b"])))
            if "s" in data:
                out["s"] = max(0.05, min(1.0, float(data["s"])))
            if "w" in data:
                out["w"] = max(0.05, min(1.0, float(data["w"])))
        user_dict["screen_rim"] = out
    except (TypeError, ValueError, json.JSONDecodeError):
        user_dict["screen_rim"] = DEFAULT_SCREEN_RIM.copy()
