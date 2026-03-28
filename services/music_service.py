"""Google Drive music service for MushroomsAI radio player."""
import asyncio
import io
import json
import logging
import os

_logger = logging.getLogger(__name__)

GDRIVE_FOLDER_ID = "192IV0zS3n5novvOgAlgIdDF7LUs0gbKr"


def _get_drive_service():
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT", "")
    if not raw:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT env var not set")
    info = json.loads(raw)
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


async def upload_track(file_bytes: bytes, filename: str) -> tuple[str, str]:
    """Upload MP3 to Google Drive folder, return (file_id, public_url)."""
    def _upload():
        from googleapiclient.http import MediaIoBaseUpload
        service = _get_drive_service()
        meta = {
            "name": filename,
            "parents": [GDRIVE_FOLDER_ID],
        }
        media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype="audio/mpeg", resumable=True)
        f = service.files().create(body=meta, media_body=media, fields="id").execute()
        file_id = f["id"]
        # Make public
        service.permissions().create(
            fileId=file_id,
            body={"role": "reader", "type": "anyone"},
        ).execute()
        url = f"https://drive.google.com/uc?export=download&id={file_id}"
        return file_id, url

    return await asyncio.get_event_loop().run_in_executor(None, _upload)


async def delete_track(file_id: str) -> bool:
    """Delete file from Google Drive."""
    if not file_id:
        return True
    def _delete():
        service = _get_drive_service()
        service.files().delete(fileId=file_id).execute()
    try:
        await asyncio.get_event_loop().run_in_executor(None, _delete)
        return True
    except Exception as e:
        _logger.warning("Drive delete error %s: %s", file_id, e)
        return False
