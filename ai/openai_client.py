from datetime import datetime
from itertools import groupby

from openai import AsyncOpenAI
from config import settings
from db.database import database
from db.models import ai_settings, messages, ai_training_posts
from ai.system_prompt import DEFAULT_SYSTEM_PROMPT
from typing import Optional

client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

async def get_system_prompt() -> str:
    try:
        row = await database.fetch_one(
            ai_settings.select().order_by(ai_settings.c.updated_at.desc()).limit(1)
        )
        base_prompt = row["system_prompt"] if row else DEFAULT_SYSTEM_PROMPT
    except Exception:
        base_prompt = DEFAULT_SYSTEM_PROMPT

    try:
        posts = list(
            await database.fetch_all(
                ai_training_posts.select().where(ai_training_posts.c.is_active == True)
            )
        )
        posts.sort(
            key=lambda r: (
                ((r.get("folder") or "").strip().lower()),
                r["created_at"] or datetime.min,
            )
        )
        if posts:
            blocks = []
            for folder, group_iter in groupby(
                posts, key=lambda r: (r.get("folder") or "").strip() or "Общее"
            ):
                group = list(group_iter)
                blocks.append(f"═══ Папка / раздел: {folder} ═══")
                for p in group:
                    cat = f"[{p['category']}] " if p.get("category") else ""
                    blocks.append(f"{cat}{p['title']}:\n{p['content']}")
            base_prompt += (
                "\n\nДОПОЛНИТЕЛЬНЫЕ ЗНАНИЯ (по папкам) — единственный источник фактов для предметной области; "
                "не опирайся на внешние базы и не выдумывай то, чего нет в этих блоках:\n"
                + "\n\n".join(blocks)
            )
    except Exception:
        pass

    return base_prompt

async def chat_with_ai(
    user_message: str,
    user_id: Optional[int] = None,
    session_key: Optional[str] = None,
    history_limit: int = 20,
) -> str:
    system_prompt = await get_system_prompt()

    history = []
    try:
        if user_id:
            rows = await database.fetch_all(
                messages.select()
                .where(messages.c.user_id == user_id)
                .order_by(messages.c.created_at.desc())
                .limit(history_limit)
            )
        elif session_key:
            rows = await database.fetch_all(
                messages.select()
                .where(messages.c.session_key == session_key)
                .order_by(messages.c.created_at.desc())
                .limit(history_limit)
            )
        else:
            rows = []
        for row in reversed(rows):
            history.append({"role": row["role"], "content": row["content"]})
    except Exception:
        pass

    history.append({"role": "user", "content": user_message})

    if not client.api_key:
        raise RuntimeError("OPENAI_API_KEY не задан в .env / переменных окружения")
    try:
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": system_prompt}] + history,
            max_tokens=2000,
            temperature=0.7,
        )
        answer = response.choices[0].message.content
    except Exception as e:
        raise RuntimeError(f"OpenAI API error: {e}") from e

    try:
        await database.execute(
            messages.insert().values(
                user_id=user_id,
                session_key=session_key,
                role="user",
                content=user_message,
            )
        )
        await database.execute(
            messages.insert().values(
                user_id=user_id,
                session_key=session_key,
                role="assistant",
                content=answer,
            )
        )
    except Exception:
        pass

    return answer
