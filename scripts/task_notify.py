"""CLI для служебных уведомлений по задачам/деплою.

Примеры:
  python scripts/task_notify.py accepted --task "Правка адаптива"
  python scripts/task_notify.py deploying --task "Правка адаптива"
"""
from __future__ import annotations

import argparse
import asyncio

from services.task_notify import notify_task_accepted, notify_deploy_sent


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Task/deploy notifications")
    p.add_argument("event", choices=["accepted", "deploying"], help="Тип события")
    p.add_argument("--task", default="", help="Краткое название задачи")
    p.add_argument("--actor", default="AI Agent", help="Кто выполняет задачу")
    p.add_argument("--site-url", default="", help="Ссылка на сайт для сообщения")
    return p


async def _run_async(args: argparse.Namespace) -> None:
    if args.event == "accepted":
        await notify_task_accepted(task=args.task, actor=args.actor)
        return
    if args.event == "deploying":
        await notify_deploy_sent(task=args.task, actor=args.actor, site_url=args.site_url)


def main() -> None:
    args = _build_parser().parse_args()
    asyncio.run(_run_async(args))


if __name__ == "__main__":
    main()
