"""Не перезаписывать аватар, загруженный на сервер (/media/avatars/), при OAuth."""


def is_server_uploaded_avatar(url) -> bool:
    if not url:
        return False
    s = str(url).strip()
    return s.startswith("/media/avatars/")
