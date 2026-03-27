"""Плейлист радио Down Tempo: версия в platform_settings, треки в radio_downtempo_tracks."""
from __future__ import annotations

import os
import re
import uuid

import sqlalchemy as sa

from db.database import database
from db.models import platform_settings, radio_downtempo_tracks

VERSION_KEY = "radio_downtempo_playlist_version"
MEDIA_SUBDIR = "radio/downtempo"


async def get_playlist_version() -> int:
    row = await database.fetch_one(
        platform_settings.select().where(platform_settings.c.key == VERSION_KEY)
    )
    if not row:
        return 1
    try:
        return max(1, int((row.get("value") or "1").strip() or "1"))
    except ValueError:
        return 1


async def bump_playlist_version() -> int:
    v = await get_playlist_version() + 1
    existing = await database.fetch_one(
        platform_settings.select().where(platform_settings.c.key == VERSION_KEY)
    )
    if existing:
        await database.execute(
            platform_settings.update()
            .where(platform_settings.c.key == VERSION_KEY)
            .values(value=str(v))
        )
    else:
        await database.execute(
            platform_settings.insert().values(key=VERSION_KEY, value=str(v))
        )
    return v


def media_base_dir() -> str:
    return "/data" if os.path.exists("/data") else "./media"


def radio_save_dir() -> str:
    d = os.path.join(media_base_dir(), MEDIA_SUBDIR)
    os.makedirs(d, exist_ok=True)
    return d


def safe_audio_ext(filename: str) -> str | None:
    if not filename or "." not in filename:
        return None
    ext = filename.rsplit(".", 1)[-1].lower().strip()
    if ext in ("mp3", "mpeg", "ogg", "opus", "wav", "flac", "m4a", "aac", "mp4"):
        if ext == "mpeg":
            return "mp3"
        return ext
    return None


def public_url_for_storage_name(storage_name: str) -> str:
    return f"/media/{MEDIA_SUBDIR}/{storage_name}"


def slug_title(name: str) -> str:
    base = os.path.splitext(name)[0]
    base = re.sub(r"[_\-]+", " ", base)
    return (base.strip() or "Трек")[:200]


ALLOWED_AUDIO_CT = frozenset(
    {
        "audio/mpeg",
        "audio/mp3",
        "audio/ogg",
        "audio/opus",
        "audio/wav",
        "audio/x-wav",
        "audio/flac",
        "audio/x-flac",
        "audio/mp4",
        "audio/aac",
        "audio/x-m4a",
        "video/mp4",
    }
)


def normalize_audio_content_type(raw: str | None, filename: str) -> str | None:
    r = (raw or "").lower()
    if ";" in r:
        r = r.split(";", 1)[0].strip()
    if r in ALLOWED_AUDIO_CT:
        return r
    ext = safe_audio_ext(filename)
    if ext == "mp3":
        return "audio/mpeg"
    if ext in ("m4a", "aac", "mp4"):
        return "audio/mp4"
    if ext == "ogg":
        return "audio/ogg"
    if ext == "wav":
        return "audio/wav"
    if ext == "flac":
        return "audio/flac"
    return None


async def list_tracks_ordered() -> list[dict]:
    rows = await database.fetch_all(
        radio_downtempo_tracks.select().order_by(
            radio_downtempo_tracks.c.sort_order.asc(), radio_downtempo_tracks.c.id.asc()
        )
    )
    out = []
    for r in rows:
        d = dict(r)
        d["url"] = public_url_for_storage_name(d["storage_name"])
        out.append(d)
    return out


async def next_sort_order() -> int:
    v = await database.fetch_val(
        sa.select(sa.func.coalesce(sa.func.max(radio_downtempo_tracks.c.sort_order), -1))
    )
    return int(v or -1) + 1
