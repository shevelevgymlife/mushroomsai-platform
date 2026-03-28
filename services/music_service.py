"""Local filesystem music service for MushroomsAI radio player."""
import logging
import os
import uuid

_logger = logging.getLogger(__name__)

MUSIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "music")
BASE_URL = "https://mushroomsai.ru/static/music"


def _ensure_dir():
    os.makedirs(MUSIC_DIR, exist_ok=True)


async def upload_track(file_bytes: bytes, filename: str) -> tuple[str, str]:
    """Save MP3 to static/music/, return (stored_filename, public_url)."""
    _ensure_dir()
    safe_name = filename.replace("/", "_").replace("..", "_")
    unique_name = f"{uuid.uuid4().hex}_{safe_name}"
    path = os.path.join(MUSIC_DIR, unique_name)
    with open(path, "wb") as f:
        f.write(file_bytes)
    url = f"{BASE_URL}/{unique_name}"
    return unique_name, url


async def delete_track(stored_filename: str) -> bool:
    """Delete file from static/music/."""
    if not stored_filename:
        return True
    path = os.path.join(MUSIC_DIR, os.path.basename(stored_filename))
    try:
        if os.path.exists(path):
            os.remove(path)
        return True
    except Exception as e:
        _logger.warning("Failed to delete music file %s: %s", stored_filename, e)
        return False
