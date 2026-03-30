"""
Custom Jinja2Templates that auto-injects `t` (translations) and `lang`
from request.state into every template context.
User's language preference (user["language"]) takes priority over the
cookie/Accept-Language detected by the middleware.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse

from fastapi.templating import Jinja2Templates as _Jinja2Templates
from config import shevelev_token_address
from services.in_app_notifications import merge_prefs
from services.mention_html import jinja_linkify_mentions
from web.community_media import post_image_urls as jinja_post_image_urls
from web.translations import TRANSLATIONS, SUPPORTED_LANGS


def _replace_query_filter(url, **kwargs):
    """Jinja2 filter: replace/add URL query params. {{ request.url | replace_query(page=2) }}"""
    parsed = urlparse(str(url))
    params = {k: v[0] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
    params.update({k: str(v) for k, v in kwargs.items()})
    return urlunparse(parsed._replace(query=urlencode(params)))


class Jinja2Templates(_Jinja2Templates):
    def __init__(
        self,
        directory: str | Path | list[str | Path] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(directory=directory, **kwargs)
        self.env.filters["linkify_mentions"] = jinja_linkify_mentions
        self.env.filters["post_image_urls"] = lambda row: jinja_post_image_urls(dict(row) if row is not None else None)
        self.env.filters["replace_query"] = _replace_query_filter

    def TemplateResponse(self, *args, **kwargs):
        # Support both positional and keyword forms
        if args:
            name = args[0]
            context = args[1] if len(args) > 1 else kwargs.get("context", {})
        else:
            name = kwargs.get("name", "")
            context = kwargs.get("context", {})

        request = context.get("request")
        if request:
            lang = getattr(request.state, "lang", "ru")

            # Тема/фон профиля: подставляем залогиненного пользователя, если роут не передал user
            if "user" not in context:
                context["user"] = getattr(request.state, "_auth_user", None)

            # Authenticated user's saved language preference wins
            user = context.get("user")
            if user and isinstance(user, dict):
                user_lang = user.get("language")
                if user_lang and user_lang in SUPPORTED_LANGS:
                    lang = user_lang

            # Слой ru → язык пользователя: непереведённые строки остаются на русском
            ru = TRANSLATIONS["ru"]
            loc = TRANSLATIONS.get(lang, ru)
            context.setdefault("t", {**ru, **loc} if lang != "ru" else dict(ru))
            context.setdefault("lang", lang)
            context.setdefault("shevelev_token", shevelev_token_address())
            context.setdefault("global_radio_enabled", getattr(request.state, "global_radio_enabled", True))
            context.setdefault("video_calls_enabled", getattr(request.state, "video_calls_enabled", True))
            ujson = (user.get("notification_prefs_json") if user and isinstance(user, dict) else None)
            context.setdefault("notification_prefs", merge_prefs(ujson))

        return super().TemplateResponse(*args, **kwargs)
