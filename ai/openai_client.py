from datetime import datetime
from itertools import groupby
import re
import time

from openai import AsyncOpenAI
import sqlalchemy as sa
from config import settings
from db.database import database
from db.models import ai_settings, messages, ai_training_posts, users
from ai.system_prompt import DEFAULT_SYSTEM_PROMPT
from typing import Optional
from services.tg_notify import notify_error

client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
_MAX_KNOWLEDGE_CHARS = 45000
_MAX_POST_CONTENT_CHARS = 2200
_MAX_RELEVANT_POSTS = 24
_MAX_RECENT_FALLBACK = 8
_HARD_TOP_K_CAP = 24
_NOTIFY_LAST_TS: dict[str, float] = {}
_RETRIEVAL_PROFILES = {
    "title_first": {"title_w": 4.0, "content_w": 1.0, "use_fts": True},
    "strict_titles": {"title_w": 6.0, "content_w": 0.2, "use_fts": False},
    "balanced": {"title_w": 3.0, "content_w": 2.0, "use_fts": True},
    "content_deep": {"title_w": 2.0, "content_w": 4.0, "use_fts": True},
    "hybrid_fts": {"title_w": 3.5, "content_w": 1.5, "use_fts": True},
    "broad_recall": {"title_w": 3.0, "content_w": 2.5, "use_fts": True},
    "precise_shortlist": {"title_w": 5.0, "content_w": 1.0, "use_fts": True},
    "recent_only": {"title_w": 0.0, "content_w": 0.0, "use_fts": False},
}


def _query_terms(text: str) -> list[str]:
    terms = re.findall(r"[A-Za-zА-Яа-я0-9_]{3,}", (text or "").lower())
    out: list[str] = []
    for t in terms:
        if t not in out:
            out.append(t)
        if len(out) >= 10:
            break
    return out


async def _notify_ai_issue(key: str, title: str, details: str, cooldown_sec: int = 900) -> None:
    now = time.time()
    last = _NOTIFY_LAST_TS.get(key, 0.0)
    if now - last < cooldown_sec:
        return
    _NOTIFY_LAST_TS[key] = now
    try:
        await notify_error(title, details)
    except Exception:
        pass


async def _fetch_relevant_training_posts(
    user_message: str,
    retrieval_mode: str = "title_first",
    top_k: int = _MAX_RELEVANT_POSTS,
) -> list[dict]:
    terms = _query_terms(user_message)
    mode = retrieval_mode if retrieval_mode in _RETRIEVAL_PROFILES else "title_first"
    profile = _RETRIEVAL_PROFILES.get(mode, _RETRIEVAL_PROFILES["title_first"])
    requested_top_k = int(top_k or _MAX_RELEVANT_POSTS)
    top_k = max(6, min(_HARD_TOP_K_CAP, requested_top_k))
    if requested_top_k > _HARD_TOP_K_CAP:
        await _notify_ai_issue(
            "ai_topk_cap",
            "AI top-k ограничен автоматически",
            f"Запрошено top_k={requested_top_k}, применён безопасный лимит {_HARD_TOP_K_CAP}.",
            cooldown_sec=6 * 60 * 60,
        )
    if mode == "precise_shortlist":
        top_k = min(top_k, 12)
    if mode == "broad_recall":
        top_k = min(80, max(top_k, 40))
    try:
        if mode == "recent_only" or not terms:
            rows = await database.fetch_all(
                ai_training_posts.select()
                .where(ai_training_posts.c.is_active == True)
                .order_by(ai_training_posts.c.created_at.desc())
                .limit(max(_MAX_RECENT_FALLBACK, min(top_k, 24)))
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
            # Весовые коэффициенты берутся из режима поиска.
            weighted = (title_hit * float(profile["title_w"])) + (content_hit * float(profile["content_w"]))
            filters.append(weighted > 0)
            rank = weighted if rank is None else (rank + weighted)

        q = (
            ai_training_posts.select()
            .where(ai_training_posts.c.is_active == True)
            .where(sa.or_(*filters))
            .order_by(rank.desc(), ai_training_posts.c.created_at.desc())
            .limit(top_k)
        )
        rows = await database.fetch_all(q)
        if rows and len(rows) >= min(6, top_k):
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
            fts_rank = (fts_title * float(profile["title_w"])) + (fts_body * float(profile["content_w"]))
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
                .limit(top_k)
            )
            if bool(profile.get("use_fts")):
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
            .limit(max(_MAX_RECENT_FALLBACK, min(top_k, 24)))
        )
        return [dict(r) for r in rows]
    except Exception:
        return []

async def get_system_prompt(user_message: str = "") -> str:
    retrieval_mode = "title_first"
    retrieval_top_k = _MAX_RELEVANT_POSTS
    try:
        row = await database.fetch_one(
            ai_settings.select().order_by(ai_settings.c.updated_at.desc()).limit(1)
        )
        base_prompt = row["system_prompt"] if row else DEFAULT_SYSTEM_PROMPT
        if row:
            row_d = dict(row)
            try:
                retrieval_mode = (row_d.get("retrieval_mode") or "title_first").strip() or "title_first"
            except Exception:
                retrieval_mode = "title_first"
            try:
                retrieval_top_k = int(row_d.get("retrieval_top_k") or _MAX_RELEVANT_POSTS)
            except Exception:
                retrieval_top_k = _MAX_RELEVANT_POSTS
    except Exception:
        base_prompt = DEFAULT_SYSTEM_PROMPT

    try:
        posts = await _fetch_relevant_training_posts(
            user_message=user_message,
            retrieval_mode=retrieval_mode,
            top_k=retrieval_top_k,
        )
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


async def _shop_links_system_extra(user_id: int) -> str:
    """Те же URL магазинов, что у кнопки «Магазин» и веб (реферал амбассадора / стандарт)."""
    from services.referral_shop_prefs import shop_urls_for_user
    from services.shop_referral_hub import (
        single_link_ai_for_exclusive_enabled,
        viewer_exclusive_referrer_id,
        viewer_in_partner_shop_transition_hold,
    )

    try:
        if await viewer_in_partner_shop_transition_hold(int(user_id)):
            ru, eu = await shop_urls_for_user(user_id)
            return (
                "\n\nВитрина магазина у этого пользователя сейчас на перенастройке (закончился льготный период продавца). "
                "Не предлагай ссылки пригласившего продавца. Только официальные площадки до открытия стандартного каталога:\n"
                f"Россия и Беларусь: {ru}\n"
                f"Европа и Америка: {eu}\n"
            )
        if await single_link_ai_for_exclusive_enabled():
            rid = await viewer_exclusive_referrer_id(int(user_id))
            if rid:
                ref = await database.fetch_one(users.select().where(users.c.id == int(rid)))
                u = (ref.get("referral_shop_url") or "").strip() if ref else ""
                if u:
                    return (
                        "\n\nМагазин для этого пользователя — одна ссылка пригласившего (Макси/витрина). "
                        "При вопросах «где купить», «ссылка на магазин» давай только её:\n"
                        f"{u}\n"
                    )
        ru, eu = await shop_urls_for_user(user_id)
        return (
            "\n\nСсылки на магазины для этого пользователя (используй только их при вопросах куда купить, "
            "не подставляй другие URL):\n"
            f"Россия и Беларусь (СДЭК/Почта РФ, Telegram): {ru}\n"
            f"Европа и Америка (Grimmurk): {eu}\n"
        )
    except Exception:
        return ""


async def chat_with_ai(
    user_message: str,
    user_id: Optional[int] = None,
    session_key: Optional[str] = None,
    history_limit: int = 20,
) -> str:
    system_prompt = await get_system_prompt(user_message=user_message)
    if user_id:
        system_prompt += await _shop_links_system_extra(user_id)

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
        await _notify_ai_issue(
            "ai_no_key",
            "AI недоступен: нет OPENAI_API_KEY",
            "Проверьте переменную OPENAI_API_KEY в Render Environment.",
            cooldown_sec=15 * 60,
        )
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
        msg = str(e).lower()
        if "context" in msg or "maximum context length" in msg or "tokens" in msg:
            await _notify_ai_issue(
                "ai_context_limit",
                "AI достиг лимита контекста",
                f"Ошибка контекста/tokens: {str(e)[:550]}",
                cooldown_sec=10 * 60,
            )
        else:
            await _notify_ai_issue(
                "ai_runtime_error",
                "AI ошибка запроса",
                f"{str(e)[:550]}",
                cooldown_sec=10 * 60,
            )
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
