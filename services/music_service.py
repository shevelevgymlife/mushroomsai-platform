"""Cloudinary music service for MushroomsAI radio player."""
import asyncio
import io
import logging
import os

_logger = logging.getLogger(__name__)


def _get_cloudinary():
    import cloudinary
    import cloudinary.uploader
    cloudinary.config(
        cloud_name=os.environ.get("CLOUDINARY_CLOUD_NAME", "du1aaf27r"),
        api_key=os.environ.get("CLOUDINARY_API_KEY", "189975495191847"),
        api_secret=os.environ.get("CLOUDINARY_API_SECRET", "tqEFmI9ED4i5qUSPApDD6bHc9lw"),
        secure=True,
    )
    return cloudinary.uploader


async def upload_track(file_bytes: bytes, filename: str) -> tuple[str, str]:
    """Upload MP3 to Cloudinary, return (public_id, secure_url)."""
    def _upload():
        uploader = _get_cloudinary()
        result = uploader.upload(
            io.BytesIO(file_bytes),
            resource_type="video",  # Cloudinary uses "video" for audio files
            folder="mushroomsai_music",
            public_id=filename.rsplit(".", 1)[0],
            overwrite=False,
            unique_filename=True,
        )
        return result["public_id"], result["secure_url"]

    return await asyncio.get_event_loop().run_in_executor(None, _upload)


async def delete_track(public_id: str) -> bool:
    """Delete file from Cloudinary."""
    if not public_id:
        return True
    def _delete():
        uploader = _get_cloudinary()
        uploader.destroy(public_id, resource_type="video")
    try:
        await asyncio.get_event_loop().run_in_executor(None, _delete)
        return True
    except Exception as e:
        _logger.warning("Cloudinary delete error %s: %s", public_id, e)
        return False
