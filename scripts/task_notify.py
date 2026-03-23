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


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Task/deploy notifications")
    p.add_argument("event", choices=["accepted", "done", "deploy_sent", "deploying"], help="Тип события")
    p.add_argument("--task", default="", help="Краткое описание задачи")
    p.add_argument("--result", default="", help="Краткий итог выполненной задачи")
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


def main() -> None:
    args = _build_parser().parse_args()
    asyncio.run(_run_async(args))


if __name__ == "__main__":
    main()
