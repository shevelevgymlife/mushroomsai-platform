"""Webhook endpoints: GitHub и Render → уведомления в Telegram."""
from __future__ import annotations

import hashlib
import hmac
import logging

from fastapi import APIRouter, Request, Header
from starlette.responses import JSONResponse

from config import settings
from services.tg_notify import (
    notify_github_push,
    notify_github_pr,
    notify_render_webhook,
)
from services.cloudpayments_service import handle_cloudpayments_notification
from services.yookassa_pay_service import handle_yookassa_http_notification

router = APIRouter(prefix="/webhooks")
logger = logging.getLogger(__name__)


def _verify_github_sig(body: bytes, sig: str | None) -> bool:
    secret = (settings.GITHUB_WEBHOOK_SECRET or "").strip()
    if not secret:
        return True  # Секрет не настроен — пропускаем
    if not sig or not sig.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


@router.post("/github")
async def github_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(None),
    x_github_event: str | None = Header(None),
):
    body = await request.body()
    if not _verify_github_sig(body, x_hub_signature_256):
        return JSONResponse({"error": "invalid signature"}, status_code=403)

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False})

    event = (x_github_event or "").strip().lower()
    repo = (data.get("repository") or {}).get("full_name", "—")

    if event == "push":
        commits = data.get("commits") or []
        head = commits[-1] if commits else {}
        author = (head.get("author") or {}).get("name", "—")
        message = (head.get("message") or "").split("\n")[0]
        branch = (data.get("ref") or "").replace("refs/heads/", "")
        url = head.get("url", "")
        await notify_github_push(repo=repo, branch=branch, author=author, message=message, url=url)

    elif event == "pull_request":
        pr = data.get("pull_request") or {}
        action = data.get("action", "")
        title = pr.get("title", "")
        author = (pr.get("user") or {}).get("login", "—")
        url = pr.get("html_url", "")
        merged = pr.get("merged", False)
        if action == "closed" and merged:
            action = "merged"
        await notify_github_pr(repo=repo, title=title, author=author, action=action, url=url)

    return JSONResponse({"ok": True})


@router.post("/render")
async def render_webhook(request: Request):
    """Render Deploy Hook — отправляем статус деплоя в Telegram."""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False})

    service = (data.get("service") or {}).get("name", "—") or data.get("serviceId", "—")
    deploy = data.get("deploy") or {}
    status = (deploy.get("status") or data.get("type") or "unknown").lower()
    deploy_id = deploy.get("id", "")
    commit = (deploy.get("commit") or {}).get("id", "")

    await notify_render_webhook(
        service=str(service),
        status=status,
        deploy_id=str(deploy_id),
        commit=str(commit),
    )
    return JSONResponse({"ok": True})


@router.post("/cloudpayments")
async def cloudpayments_webhook(request: Request):
    """Уведомления CloudPayments: проверка HMAC, активация подписки."""
    body = await request.body()
    h = request.headers
    content_hmac = (
        h.get("Content-HMAC")
        or h.get("content-hmac")
        or h.get("X-Content-Hmac")
        or h.get("x-content-hmac")
    )
    ok, msg = await handle_cloudpayments_notification(body, content_hmac)
    if ok and msg in ("ok", "ok_gift", "duplicate"):
        logger.info(
            "cloudpayments webhook ok: %s body_len=%s ct=%s",
            msg,
            len(body or b""),
            (request.headers.get("content-type") or "").split(";")[0].strip() or "(none)",
        )
    if not ok:
        logger.warning("cloudpayments webhook rejected: %s", msg)
        if msg in (
            "bad_hmac",
            "cloudpayments_disabled",
            "no_api_secret",
            "bad_json",
            "bad_user",
            "bad_plan",
            "bad_price_config",
            "amount_mismatch",
            "user_not_found",
            "activate_failed",
            "bad_gift_users",
        ) or (isinstance(msg, str) and msg.startswith("gift_")):
            return JSONResponse({"code": 0}, status_code=403 if msg == "bad_hmac" else 400)
        return JSONResponse({"code": 0}, status_code=400)
    return JSONResponse({"code": 0})


@router.post("/yookassa")
async def yookassa_webhook(request: Request):
    """HTTP-уведомления ЮKassa (payment.succeeded с metadata user_id/plan — см. личный кабинет)."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "bad json"}, status_code=400)
    ok, msg = await handle_yookassa_http_notification(body)
    if not ok:
        logger.warning("yookassa webhook: %s", msg)
    return JSONResponse({"status": "ok"})
