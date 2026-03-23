"""CLI для служебных уведомлений по задачам/деплою.

Примеры:
  python scripts/task_notify.py accepted --task "Правка адаптива"
  python scripts/task_notify.py done --result "Правка выполнена"
  python scripts/task_notify.py deploy_sent --task "Правка адаптива"
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

# Allow running as: `python scripts/task_notify.py ...` from repo root.
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from services.task_notify import notify_task_accepted, notify_task_done, notify_deploy_sent
from services.task_approval import create_confirmation_request, wait_for_confirmation


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Task/deploy notifications")
    p.add_argument("event", choices=["accepted", "done", "deploy_sent", "deploying", "confirm"], help="Тип события")
    p.add_argument("--task", default="", help="Краткое описание задачи")
    p.add_argument("--result", default="", help="Краткий итог выполненной задачи")
    p.add_argument("--question", default="", help="Короткий вопрос для подтверждения (для event=confirm)")
    p.add_argument("--details", default="", help="Короткие детали для подтверждения (для event=confirm)")
    p.add_argument("--request-id", default="", help="Внешний id шага (для event=confirm)")
    p.add_argument("--wait-seconds", type=int, default=1800, help="Таймаут ожидания ответа, сек (для event=confirm)")
    p.add_argument("--actor", default="AI Agent", help="Кто выполняет задачу")
    p.add_argument("--site-url", default="", help="Ссылка на сайт для сообщения")
    return p


async def _run_async(args: argparse.Namespace) -> None:
    if args.event == "accepted":
        await notify_task_accepted(task_text=args.task)
        return
    if args.event == "done":
        await notify_task_done(done_text=args.result or args.task)
        return
    if args.event in ("deploying", "deploy_sent"):
        await notify_deploy_sent(task_text=args.task)
        return
    if args.event == "confirm":
        q = (args.question or args.task or "").strip() or "Подтвердите действие"
        req = await create_confirmation_request(
            question=q,
            details=(args.details or args.result or "").strip(),
            action_key=(args.request_id or "").strip(),
        )
        if not req:
            raise SystemExit(3)
        approved = await wait_for_confirmation(
            request_id=str(req.get("request_id") or ""),
            timeout_sec=max(30, int(args.wait_seconds or 1800)),
        )
        # Non-zero exit code when rejected or timeout, so caller can stop pipeline.
        if not approved:
            raise SystemExit(2)


def main() -> None:
    args = _build_parser().parse_args()
    asyncio.run(_run_async(args))


if __name__ == "__main__":
    main()
