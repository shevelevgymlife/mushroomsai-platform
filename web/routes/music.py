"""Music radio router — admin management + public API + user settings."""
import logging
from typing import Optional

import sqlalchemy as sa
from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from auth.session import get_user_from_request
from db.database import database
from web.templates_utils import Jinja2Templates

_logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="web/templates")


async def _require_admin(request: Request):
    user = await get_user_from_request(request)
    if not user or user.get("role") != "admin":
        return None, user
    return user, user


async def _get_tracks():
    return await database.fetch_all(
        sa.text("SELECT * FROM music_tracks ORDER BY position ASC, id ASC")
    )


# ── Admin pages ──────────────────────────────────────────────────────────────

@router.get("/admin/music", response_class=HTMLResponse)
async def admin_music_page(request: Request):
    admin, user = await _require_admin(request)
    if not admin:
        return RedirectResponse("/login", status_code=302)
    tracks = await _get_tracks()
    return templates.TemplateResponse("dashboard/admin_music.html", {
        "request": request,
        "user": dict(admin),
        "tracks": [dict(t) for t in tracks],
    })


@router.post("/admin/music/upload")
async def admin_upload_track(request: Request, file: UploadFile = File(...), title: str = Form("")):
    admin, _ = await _require_admin(request)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    data = await file.read()
    if len(data) > 50 * 1024 * 1024:
        return JSONResponse({"error": "Файл слишком большой (макс 50MB)"}, status_code=400)

    filename = file.filename or "track.mp3"
    track_title = title.strip() or filename.rsplit(".", 1)[0]

    try:
        from services.music_service import upload_track
        gdrive_file_id, gdrive_url = await upload_track(data, filename)
    except Exception as e:
        _logger.error("Music upload error: %s", e)
        return JSONResponse({"error": f"Ошибка загрузки: {e}"}, status_code=500)

    # Get next position
    max_pos = await database.fetch_val(sa.text("SELECT COALESCE(MAX(position),0) FROM music_tracks"))
    track_id = await database.execute(
        sa.text("""
            INSERT INTO music_tracks (title, gdrive_file_id, gdrive_url, is_active, position)
            VALUES (:title, :fid, :url, true, :pos) RETURNING id
        """),
        {"title": track_title, "fid": gdrive_file_id, "url": gdrive_url, "pos": int(max_pos or 0) + 1},
    )
    return JSONResponse({"ok": True, "id": track_id, "title": track_title, "url": gdrive_url})


@router.delete("/admin/music/{track_id}")
async def admin_delete_track(request: Request, track_id: int):
    admin, _ = await _require_admin(request)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    row = await database.fetch_one(sa.text("SELECT gdrive_file_id FROM music_tracks WHERE id=:id"), {"id": track_id})
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)

    if row["gdrive_file_id"]:
        try:
            from services.music_service import delete_track
            await delete_track(row["gdrive_file_id"])
        except Exception as e:
            _logger.warning("Music delete error: %s", e)

    await database.execute(sa.text("DELETE FROM music_tracks WHERE id=:id"), {"id": track_id})
    return JSONResponse({"ok": True})


@router.patch("/admin/music/{track_id}/toggle")
async def admin_toggle_track(request: Request, track_id: int):
    admin, _ = await _require_admin(request)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    await database.execute(
        sa.text("UPDATE music_tracks SET is_active = NOT is_active WHERE id=:id"),
        {"id": track_id},
    )
    row = await database.fetch_one(sa.text("SELECT is_active FROM music_tracks WHERE id=:id"), {"id": track_id})
    return JSONResponse({"ok": True, "is_active": bool(row["is_active"]) if row else False})


@router.patch("/admin/music/{track_id}/title")
async def admin_rename_track(request: Request, track_id: int):
    admin, _ = await _require_admin(request)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    body = await request.json()
    title = (body.get("title") or "").strip()
    if not title:
        return JSONResponse({"error": "title required"}, status_code=400)
    await database.execute(sa.text("UPDATE music_tracks SET title=:t WHERE id=:id"), {"t": title, "id": track_id})
    return JSONResponse({"ok": True})


@router.post("/admin/music/reorder")
async def admin_reorder_tracks(request: Request):
    admin, _ = await _require_admin(request)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    body = await request.json()
    ids: list[int] = body.get("ids") or []
    for pos, tid in enumerate(ids):
        await database.execute(
            sa.text("UPDATE music_tracks SET position=:pos WHERE id=:id"),
            {"pos": pos, "id": tid},
        )
    return JSONResponse({"ok": True})


@router.patch("/admin/music/global-toggle")
async def admin_global_toggle(request: Request):
    admin, _ = await _require_admin(request)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    body = await request.json()
    enabled = bool(body.get("enabled", True))
    val = "true" if enabled else "false"
    await database.execute(
        sa.text("""
            INSERT INTO site_settings (key, value, updated_at)
            VALUES ('radio_enabled', :v, NOW())
            ON CONFLICT (key) DO UPDATE SET value=:v, updated_at=NOW()
        """),
        {"v": val},
    )
    # Bust cache
    import main as _main
    _main._gsettings_cache["ts"] = 0.0
    return JSONResponse({"ok": True, "enabled": enabled})


# ── Public API ────────────────────────────────────────────────────────────────

@router.get("/api/music/tracks")
async def api_get_tracks(request: Request):
    tracks = await database.fetch_all(
        sa.text("SELECT id, title, gdrive_url FROM music_tracks WHERE is_active=true ORDER BY position ASC, id ASC")
    )
    return JSONResponse([dict(t) for t in tracks])


@router.post("/api/music/player-settings")
async def api_player_settings(request: Request):
    user = await get_user_from_request(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    body = await request.json()
    updates = {}
    if "enabled" in body:
        updates["music_player_enabled"] = bool(body["enabled"])
    if "volume" in body:
        vol = float(body["volume"])
        updates["music_player_volume"] = max(0.0, min(1.0, vol))
    if "position" in body:
        updates["music_player_position"] = str(body["position"])[:50]
    if "position_px" in body and isinstance(body.get("position_px"), dict):
        px = body["position_px"]
        try:
            lx = int(px.get("left", 0))
            ty = int(px.get("top", 0))
            updates["music_player_position"] = f"free:{lx},{ty}"[:50]
        except (TypeError, ValueError):
            pass

    if updates:
        set_clause = ", ".join(f"{k}=:{k}" for k in updates)
        updates["uid"] = user["id"]
        await database.execute(
            sa.text(f"UPDATE users SET {set_clause} WHERE id=:uid"),
            updates,
        )
    return JSONResponse({"ok": True})
