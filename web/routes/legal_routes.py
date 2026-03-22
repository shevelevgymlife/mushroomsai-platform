"""Публичные юридические документы и принятие условий."""
from __future__ import annotations

import urllib.parse
from datetime import datetime

import sqlalchemy as sa
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from auth.session import get_user_from_request
from db.database import database
from db.models import users
from services.legal import LEGAL_DOCS_VERSION
from web.templates_utils import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")


@router.get("/legal/terms", response_class=HTMLResponse)
async def legal_terms(request: Request):
    user = await get_user_from_request(request)
    return templates.TemplateResponse(
        "legal/terms.html",
        {"request": request, "user": user, "legal_version": LEGAL_DOCS_VERSION},
    )


@router.get("/legal/privacy", response_class=HTMLResponse)
async def legal_privacy(request: Request):
    user = await get_user_from_request(request)
    return templates.TemplateResponse(
        "legal/privacy.html",
        {"request": request, "user": user, "legal_version": LEGAL_DOCS_VERSION},
    )


@router.get("/legal/accept", response_class=HTMLResponse)
async def legal_accept_page(request: Request):
    user = await get_user_from_request(request)
    if not user:
        nxt = request.query_params.get("next") or "/dashboard"
        safe = urllib.parse.quote(nxt, safe="")
        return RedirectResponse(f"/login?next=/legal/accept?next={safe}", status_code=302)
    nxt = request.query_params.get("next") or "/dashboard"
    if not nxt.startswith("/") or nxt.startswith("//"):
        nxt = "/dashboard"
    return templates.TemplateResponse(
        "legal/accept.html",
        {
            "request": request,
            "user": user,
            "next_url": nxt,
            "legal_version": LEGAL_DOCS_VERSION,
        },
    )


@router.post("/legal/accept")
async def legal_accept_submit(
    request: Request,
    accept: str = Form(""),
    next_url: str = Form("/dashboard", alias="next"),
):
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    dest = next_url if next_url.startswith("/") and not next_url.startswith("//") else "/dashboard"
    if accept != "1":
        return templates.TemplateResponse(
            "legal/accept.html",
            {
                "request": request,
                "user": user,
                "next_url": dest,
                "legal_version": LEGAL_DOCS_VERSION,
                "error": "Отметьте согласие с документами, чтобы продолжить.",
            },
            status_code=400,
        )
    uid = user.get("primary_user_id") or user["id"]
    await database.execute(
        users.update()
        .where(users.c.id == uid)
        .values(legal_accepted_at=datetime.utcnow(), legal_docs_version=LEGAL_DOCS_VERSION)
    )
    return RedirectResponse(dest, status_code=302)
