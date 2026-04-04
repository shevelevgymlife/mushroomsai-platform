"""Мост для сброса in-memory кеша GlobalSettingsMiddleware без import main из services."""

from __future__ import annotations

from typing import Callable

_invalidate: Callable[[], None] | None = None


def register_global_settings_invalidate(fn: Callable[[], None]) -> None:
    global _invalidate
    _invalidate = fn


def invalidate_global_settings_cache() -> None:
    if _invalidate is not None:
        _invalidate()
