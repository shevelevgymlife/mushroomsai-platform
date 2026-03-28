"""Helpers for community post images (gallery up to 5 URLs)."""
from __future__ import annotations

import json
from typing import Any, Mapping


def post_image_urls(row: Mapping[str, Any] | None) -> list[str]:
    if not row:
        return []
    raw = (row.get("images_json") or "").strip()
    if raw:
        try:
            arr = json.loads(raw)
            if isinstance(arr, list):
                out = [str(x).strip() for x in arr if x is not None and str(x).strip()]
                return out[:5]
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    u = (row.get("image_url") or "").strip()
    return [u] if u else []
