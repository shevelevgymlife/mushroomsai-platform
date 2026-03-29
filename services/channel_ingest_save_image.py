"""Сохранение картинки канала на диск как у обычных постов сообщества (/media/community/)."""
from __future__ import annotations

import os
import uuid

_POST_IMAGE_MAX = 8 * 1024 * 1024


def save_channel_ingest_image(data: bytes) -> str | None:
    if not data or len(data) > _POST_IMAGE_MAX:
        return None
    head = data[:12]
    ext = "jpg"
    if head.startswith(b"\xff\xd8"):
        ext = "jpg"
    elif head.startswith(b"\x89PNG"):
        ext = "png"
    elif head.startswith(b"GIF8"):
        ext = "gif"
    elif head.startswith(b"RIFF") and b"WEBP" in head:
        ext = "webp"
    filename = f"tgch_{uuid.uuid4().hex}.{ext}"
    # Как в web.routes.user._save_community_uploaded_image: /media → корень /data или ./media
    base = "/data" if os.path.exists("/data") else "./media"
    save_path = os.path.join(base, "community", filename)
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    try:
        with open(save_path, "wb") as f:
            f.write(data)
        return f"/media/community/{filename}"
    except OSError:
        return None
