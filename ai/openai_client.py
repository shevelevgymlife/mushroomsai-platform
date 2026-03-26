from datetime import datetime
from itertools import groupby
import re

from openai import AsyncOpenAI
import sqlalchemy as sa
from config import settings
from db.database import database
from db.models import ai_settings, messages, ai_training_posts
from ai.system_prompt import DEFAULT_SYSTEM_PROMPT
from typing import Optional

client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
_MAX_KNOWLEDGE_CHARS = 60000
_MAX_POST_CONTENT_CHARS = 2200
_MAX_RELEVANT_POSTS = 24
_MAX_RECENT_FALLBACK = 8


def _query_terms(text: str) -> list[str]:
    terms = re.findall(r"[A-Za-zА-Яа-я0-9_]{3,}", (text or "").lower())
    out: list[str] = []
    for t in terms:
        if t not in out:
            out.append(t)
        if len(out) >= 10:
            break
    return out


async def _fetch_relevant_training_posts(user_message: str) -> list[dict]:
    terms = _query_terms(user_message)
    try:
        if not terms:
            rows = await database.fetch_all(
                ai_training_posts.select()
                .where(ai_training_posts.c.is_active == True)
                .order_by(ai_training_posts.c.created_at.desc())
                .limit(_MAX_RECENT_FALLBACK)
            )
            return [dict(r) for r in rows]

        # 1) Ключевая логика: сначала максимально бьем по title/folder/category,
        # затем уже учитываем совпадения в content.
        rank = None
        filters = []
        for t in terms:
            p = f"%{t}%"
            title_hit = sa.case(
                (
                    sa.or_(
                        ai_training_posts.c.title.ilike(p),
                        ai_training_posts.c.folder.ilike(p),
                        ai_training_posts.c.category.ilike(p),
                    ),
                    1,
                ),
                else_=0,
            )
            content_hit = sa.case(
                (ai_training_posts.c.content.ilike(p), 1),
                else_=0,
            )
            # Вес title/folder/category выше, чем content.
            weighted = (title_hit * 4) + content_hit
            filters.append(weighted > 0)
            rank = weighted if rank is None else (rank + weighted)

        q = (
            ai_training_posts.select()
            .where(ai_training_posts.c.is_active == True)
            .where(sa.or_(*filters))
            .order_by(rank.desc(), ai_training_posts.c.created_at.desc())
            .limit(_MAX_RELEVANT_POSTS)
        )
        rows = await database.fetch_all(q)
        if rows and len(rows) >= min(6, _MAX_RELEVANT_POSTS):
            return [dict(r) for r in rows]

        # 2) FTS fallback (PostgreSQL): тоже с приоритетом title > content.
        # Если БД не поддерживает FTS функцию, quietly fallback ниже.
        try:
            qtxt = " ".join(terms)[:280]
            fts_title = sa.func.ts_rank_cd(
                sa.func.to_tsvector("russian", sa.func.coalesce(ai_training_posts.c.title, "")),
                sa.func.plainto_tsquery("russian", qtxt),
            )
            fts_body = sa.func.ts_rank_cd(
                sa.func.to_tsvector("russian", sa.func.coalesce(ai_training_posts.c.content, "")),
                sa.func.plainto_tsquery("russian", qtxt),
            )
            fts_rank = (fts_title * 3.0) + fts_body
            q2 = (
                ai_training_posts.select()
                .where(ai_training_posts.c.is_active == True)
                .where(
                    sa.or_(
                        sa.func.to_tsvector("russian", sa.func.coalesce(ai_training_posts.c.title, ""))
                        .op("@@")(sa.func.plainto_tsquery("russian", qtxt)),
                        sa.func.to_tsvector("russian", sa.func.coalesce(ai_training_posts.c.content, ""))
                        .op("@@")(sa.func.plainto_tsquery("russian", qtxt)),
                    )
                )
                .order_by(fts_rank.desc(), ai_training_posts.c.created_at.desc())
                .limit(_MAX_RELEVANT_POSTS)
            )
            fts_rows = await database.fetch_all(q2)
            if fts_rows:
                return [dict(r) for r in fts_rows]
        except Exception:
            pass

        # Если ничего не совпало по словам — берем свежие активные материалы.
        rows = await database.fetch_all(
            ai_training_posts.select()
            .where(ai_training_posts.c.is_active == True)
            .order_by(ai_training_posts.c.created_at.desc())
            .limit(_MAX_RECENT_FALLBACK)
        )
        return [dict(r) for r in rows]
    except Exception:
        return []

async def get_system_prompt(user_message: str = "") -> str:
    try:
        row = await database.fetch_one(
            ai_settings.select().order_by(ai_settings.c.updated_at.desc()).limit(1)
        )
        base_prompt = row["system_prompt"] if row else DEFAULT_SYSTEM_PROMPT
    except Exception:
        base_prompt = DEFAULT_SYSTEM_PROMPT

    try:
        posts = await _fetch_relevant_training_posts(user_message)
        posts.sort(
            key=lambda r: (
                ((r.get("folder") or "").strip().lower()),
                r["created_at"] or datetime.min,
            )
        )
        if posts:
            blocks = []
            used = 0
            for folder, group_iter in groupby(
                posts, key=lambda r: (r.get("folder") or "").strip() or "Общее"
            ):
                group = list(group_iter)
                folder_hdr = f"═══ Папка / раздел: {folder} ═══"
                if used + len(folder_hdr) > _MAX_KNOWLEDGE_CHARS:
                    break
                blocks.append(folder_hdr)
                used += len(folder_hdr)
                for p in group:
                    cat = f"[{p['category']}] " if p.get("category") else ""
                    content = (p.get("content") or "")[:_MAX_POST_CONTENT_CHARS]
                    block = f"{cat}{p['title']}:\n{content}"
                    img = (p.get("image_url") or "").strip()
                    if img:
                        block += f"\n[Изображение к материалу: {img}]"
                    if used + len(block) > _MAX_KNOWLEDGE_CHARS:
                        break
                    blocks.append(block)
                    used += len(block)
                if used >= _MAX_KNOWLEDGE_CHARS:
                    break
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
    system_prompt = await get_system_prompt(user_message=user_message)

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
        last_err = None
        for mdl in ("gpt-4o", "gpt-4o-mini"):
            try:
                response = await client.chat.completions.create(
                    model=mdl,
                    messages=[{"role": "system", "content": system_prompt}] + history,
                    max_tokens=2000,
                    temperature=0.7,
                )
                answer = response.choices[0].message.content
                break
            except Exception as e:
                last_err = e
        else:
            raise RuntimeError(f"{last_err}")  # pragma: no cover
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
