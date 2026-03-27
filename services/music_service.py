"""Google Drive music service for MushroomsAI radio player."""
import io
import json
import logging
import os
from typing import Optional

_logger = logging.getLogger(__name__)

FOLDER_NAME = "MushroomsAI_Music"
_SCOPES = ["https://www.googleapis.com/auth/drive"]


def _get_credentials():
    """Build service account credentials from env var."""
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT", "")
    if not sa_json:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT env var is not set")
    try:
        from google.oauth2 import service_account
        info = json.loads(sa_json)
        return service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
    except Exception as e:
        raise ValueError(f"Failed to parse GOOGLE_SERVICE_ACCOUNT: {e}")


def _build_service():
    from googleapiclient.discovery import build
    creds = _get_credentials()
    return build("drive", "v3", credentials=creds, cache_discovery=False)


async def get_or_create_folder() -> str:
    """Return folder id for MushroomsAI_Music, creating if needed."""
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _get_or_create_folder_sync)


def _get_or_create_folder_sync() -> str:
    service = _build_service()
    query = f"name='{FOLDER_NAME}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    res = service.files().list(q=query, fields="files(id,name)").execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]
    meta = {"name": FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"}
    folder = service.files().create(body=meta, fields="id").execute()
    return folder["id"]


async def upload_track(file_bytes: bytes, filename: str) -> tuple[str, str]:
    """Upload MP3 to Google Drive, make public, return (file_id, public_url)."""
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _upload_track_sync, file_bytes, filename)


def _upload_track_sync(file_bytes: bytes, filename: str) -> tuple[str, str]:
    from googleapiclient.http import MediaIoBaseUpload
    service = _build_service()
    folder_id = _get_or_create_folder_sync()

    file_meta = {
        "name": filename,
        "parents": [folder_id],
    }
    media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype="audio/mpeg", resumable=False)
    uploaded = service.files().create(
        body=file_meta,
        media_body=media,
        fields="id",
    ).execute()
    file_id = uploaded["id"]

    # Make public
    service.permissions().create(
        fileId=file_id,
        body={"role": "reader", "type": "anyone"},
    ).execute()

    url = f"https://drive.google.com/uc?export=download&id={file_id}"
    return file_id, url


async def delete_track(gdrive_file_id: str) -> bool:
    """Delete file from Google Drive."""
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _delete_track_sync, gdrive_file_id)


def _delete_track_sync(gdrive_file_id: str) -> bool:
    try:
        service = _build_service()
        service.files().delete(fileId=gdrive_file_id).execute()
        return True
    except Exception as e:
        _logger.warning("Failed to delete Drive file %s: %s", gdrive_file_id, e)
        return False
