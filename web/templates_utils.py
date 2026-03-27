"""
Custom Jinja2Templates that auto-injects `t` (translations) and `lang`
from request.state into every template context.
User's language preference (user["language"]) takes priority over the
cookie/Accept-Language detected by the middleware.
"""
from fastapi.templating import Jinja2Templates as _Jinja2Templates
from config import shevelev_token_address
from web.translations import TRANSLATIONS, SUPPORTED_LANGS


class Jinja2Templates(_Jinja2Templates):
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

        return super().TemplateResponse(*args, **kwargs)
