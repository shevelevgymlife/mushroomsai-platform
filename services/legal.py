"""Версионирование юридических документов и проверка принятия пользователем."""
from __future__ import annotations

import urllib.parse
from typing import Any

from fastapi import Request
from fastapi.responses import RedirectResponse

# При существенном изменении текста /legal/* — увеличить версию (все пользователи перепримут).
LEGAL_DOCS_VERSION = "2025-03-23-v2"


def legal_next_param(request: Request) -> str:
    p = request.url.path or "/community"
    if request.url.query:
        p += "?" + str(request.url.query)
    return urllib.parse.quote(p, safe="")


async def legal_acceptance_redirect(request: Request, user: dict[str, Any] | None) -> RedirectResponse | None:
    """Если пользователь не принял актуальную редакцию — редирект на /legal/accept."""
    if not user:
        return None
    if user.get("role") == "admin":
        return None
    from db.database import database
    import sqlalchemy as sa

    uid = user.get("primary_user_id") or user["id"]
    row = await database.fetch_one(
        sa.text(
            "SELECT legal_accepted_at, COALESCE(legal_docs_version, '') AS legal_docs_version FROM users WHERE id = :uid"
        ).bindparams(uid=uid)
    )
    if not row:
        return RedirectResponse("/login", status_code=302)
    # databases.Record в разных драйверах может не иметь .get()
    try:
        acc = row["legal_accepted_at"]
    except Exception:
        acc = None
    try:
        ver = row["legal_docs_version"] or ""
    except Exception:
        ver = ""
    if acc and ver == LEGAL_DOCS_VERSION:
        return None
    nxt = legal_next_param(request)
    return RedirectResponse(f"/legal/accept?next={nxt}", status_code=302)
