from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse
from auth.session import get_user_from_request
from db.database import database
from db.models import users
from web.translations import SUPPORTED_LANGS

router = APIRouter()


@router.post("/set-language")
async def set_language(request: Request, lang: str = Form(...)):
    if lang not in SUPPORTED_LANGS:
        lang = "ru"

    referer = request.headers.get("referer", "/")
    response = RedirectResponse(referer, status_code=302)
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
