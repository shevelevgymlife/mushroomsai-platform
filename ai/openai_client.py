from openai import AsyncOpenAI
from config import settings
from db.database import database
from db.models import ai_settings, messages
from ai.system_prompt import DEFAULT_SYSTEM_PROMPT
from ai.knowledge_base import search_knowledge
from typing import Optional

client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

async def get_system_prompt() -> str:
    try:
        row = await database.fetch_one(
            ai_settings.select().order_by(ai_settings.c.updated_at.desc()).limit(1)
        )
        if row:
            return row["system_prompt"]
    except Exception:
        pass
    return DEFAULT_SYSTEM_PROMPT

async def chat_with_ai(
    user_message: str,
    user_id: Optional[int] = None,
    session_key: Optional[str] = None,
    history_limit: int = 10,
) -> str:
    system_prompt = await get_system_prompt()
    
    # Ищем релевантную информацию из базы знаний
    knowledge_context = search_knowledge(user_message, top_k=3)
    if knowledge_context:
        system_prompt += f"\n\nРЕЛЕВАНТНАЯ ИНФОРМАЦИЯ ИЗ БАЗЫ ЗНАНИЙ:\n{knowledge_context}"
    
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

    response = await client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": system_prompt}] + history,
        max_tokens=1500,
        temperature=0.7,
    )
    answer = response.choices[0].message.content

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
