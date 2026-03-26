from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse
from auth.session import get_user_from_request
from db.database import database
from db.models import users
from web.translations import SUPPORTED_LANGS

router = APIRouter()


def _redirect_target_after_language(request: Request, next_path: Optional[str]) -> str:
    """Same-origin path from `next` form field, else full Referer (navbar), else /."""
    n = (next_path or "").strip()
    if n.startswith("/") and not n.startswith("//") and "\r" not in n and "\n" not in n:
        return n[:2048]
    ref = (request.headers.get("referer") or "").strip()
    if not ref:
        return "/"
    try:
        p = urlparse(ref)
        if p.scheme in ("http", "https") and p.netloc:
            return ref[:4096]
    except Exception:
        pass
    if ref.startswith("/") and not ref.startswith("//"):
        return ref[:2048]
    return "/"


@router.post("/set-language")
async def set_language(request: Request, lang: str = Form(...), next: Optional[str] = Form(None)):
    if lang not in SUPPORTED_LANGS:
        lang = "ru"

    target = _redirect_target_after_language(request, next)
    response = RedirectResponse(target, status_code=302)
    response.set_cookie(
        "lang", lang,
        max_age=365 * 24 * 3600,
        samesite="lax",
        httponly=False,
    )

    user = await get_user_from_request(request)
    if user:
        await database.execute(
            users.update().where(users.c.id == user["id"]).values(language=lang)
        )

    return response
