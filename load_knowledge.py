"""
Скрипт загрузки базы знаний из Google Drive в PostgreSQL.
Использование: python load_knowledge.py

Credentials приоритет:
  1. Переменная окружения GOOGLE_SERVICE_ACCOUNT (JSON-строка) — используется на Render
  2. Файл service_account.json — для локального запуска
"""

import os
import io
import sys
import json
import tempfile
from pathlib import Path

# Принудительно переключаем вывод на UTF-8 для поддержки всех Unicode-символов
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.oauth2 import service_account
import psycopg2

# Парсинг текста из файлов
try:
    import PyPDF2
except ImportError:
    PyPDF2 = None

try:
    from docx import Document as DocxDocument
except ImportError:
    DocxDocument = None

try:
    import openpyxl
except ImportError:
    openpyxl = None

load_dotenv()

DRIVE_FOLDER_ID = "1hT6KDF4V_4Dm1-Iz2diHx8ssCSI-cIiy"
SERVICE_ACCOUNT_FILE = "service_account.json"
SUPPORTED_MIME_TYPES = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/plain": ".txt",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/msword": ".doc",
}

CREATE_KNOWLEDGE_BASE = """
CREATE TABLE IF NOT EXISTS knowledge_base (
    id SERIAL PRIMARY KEY,
    title TEXT,
    content TEXT,
    category TEXT,
    source_file TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);
"""

CREATE_SHOP_PRODUCTS = """
CREATE TABLE IF NOT EXISTS shop_products (
    id SERIAL PRIMARY KEY,
    name TEXT,
    description TEXT,
    price INTEGER,
    url TEXT,
    mushroom_type TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);
"""


def get_credentials_dict() -> dict:
    """Read service account credentials from env var or file."""
    env_json = os.getenv("GOOGLE_SERVICE_ACCOUNT", "")
    if env_json:
        try:
            return json.loads(env_json)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"GOOGLE_SERVICE_ACCOUNT содержит невалидный JSON: {e}")

    if Path(SERVICE_ACCOUNT_FILE).exists():
        with open(SERVICE_ACCOUNT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    raise RuntimeError(
        f"Credentials не найдены: задайте переменную GOOGLE_SERVICE_ACCOUNT "
        f"или положите файл {SERVICE_ACCOUNT_FILE}"
    )


def get_drive_service(creds_dict: dict = None):
    if creds_dict is None:
        creds_dict = get_credentials_dict()
    credentials = service_account.Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/drive.readonly"],
    )
    return build("drive", "v3", credentials=credentials)


def list_files(service, folder_id):
    """Возвращает список файлов из папки Google Drive."""
    files = []
    page_token = None

    mime_filter = " or ".join(
        f"mimeType='{mime}'" for mime in SUPPORTED_MIME_TYPES
    )
    query = f"'{folder_id}' in parents and ({mime_filter}) and trashed=false"

    while True:
        response = service.files().list(
            q=query,
            fields="nextPageToken, files(id, name, mimeType)",
            pageToken=page_token,
            pageSize=100,
        ).execute()

        files.extend(response.get("files", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return files


def download_file(service, file_id, file_name, mime_type):
    """Скачивает файл из Google Drive во временный файл, возвращает путь."""
    ext = SUPPORTED_MIME_TYPES.get(mime_type, "")
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)

    request = service.files().get_media(fileId=file_id)
    downloader = MediaIoBaseDownload(tmp, request)

    done = False
    while not done:
        _, done = downloader.next_chunk()

    tmp.close()
    return tmp.name


def extract_text_pdf(path):
    if PyPDF2 is None:
        return "[PyPDF2 не установлен — текст PDF недоступен]"
    text_parts = []
    with open(path, "rb") as f:
        reader = PyPDF2.PdfReader(f)
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
    return "\n".join(text_parts)


def extract_text_docx(path):
    if DocxDocument is None:
        return "[python-docx не установлен — текст DOCX недоступен]"
    doc = DocxDocument(path)
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def extract_text_txt(path):
    for encoding in ("utf-8", "cp1251", "latin-1"):
        try:
            with open(path, "r", encoding=encoding) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    return "[Не удалось декодировать текстовый файл]"


def extract_text_xlsx(path):
    if openpyxl is None:
        return "[openpyxl не установлен — текст XLSX недоступен]"
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    rows = []
    for sheet in wb.worksheets:
        for row in sheet.iter_rows(values_only=True):
            row_text = "\t".join(str(cell) for cell in row if cell is not None)
            if row_text.strip():
                rows.append(row_text)
    return "\n".join(rows)


def extract_text(path, mime_type):
    if mime_type == "application/pdf":
        return extract_text_pdf(path)
    elif mime_type in (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
    ):
        return extract_text_docx(path)
    elif mime_type == "text/plain":
        return extract_text_txt(path)
    elif mime_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
        return extract_text_xlsx(path)
    return ""


def guess_category(file_name):
    name_lower = file_name.lower()
    if any(k in name_lower for k in ("рецепт", "recipe", "кулинар")):
        return "рецепты"
    if any(k in name_lower for k in ("протокол", "protocol", "схема", "лечение")):
        return "протоколы"
    if any(k in name_lower for k in ("исследован", "research", "наука", "science")):
        return "исследования"
    if any(k in name_lower for k in ("гриб", "mushroom", "чага", "рейши", "шиитаке", "кордицепс", "лев")):
        return "грибы"
    return "общее"


def sync_drive_to_db(database_url: str = None, creds_dict: dict = None) -> dict:
    """
    Callable entry point for the admin sync route.
    Returns {"loaded": N, "updated": N, "errors": N, "log": [...]}
    """
    if database_url is None:
        database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        raise RuntimeError("DATABASE_URL не задан")

    pg_url = database_url.replace("postgresql+asyncpg://", "postgresql://")

    if creds_dict is None:
        creds_dict = get_credentials_dict()

    log = []

    log.append("Подключение к Google Drive...")
    service = get_drive_service(creds_dict)

    log.append(f"Получение файлов из папки {DRIVE_FOLDER_ID}...")
    files = list_files(service, DRIVE_FOLDER_ID)

    if not files:
        log.append("Папка пуста или нет файлов поддерживаемых форматов.")
        return {"loaded": 0, "updated": 0, "errors": 0, "log": log}

    log.append(f"Найдено файлов: {len(files)}")

    conn = psycopg2.connect(pg_url)
    cur = conn.cursor()
    cur.execute(CREATE_KNOWLEDGE_BASE)
    cur.execute(CREATE_SHOP_PRODUCTS)
    conn.commit()

    loaded = 0
    updated = 0
    errors = 0

    for i, file in enumerate(files, 1):
        file_id = file["id"]
        file_name = file["name"]
        mime_type = file["mimeType"]

        tmp_path = None
        try:
            tmp_path = download_file(service, file_id, file_name, mime_type)
            text = extract_text(tmp_path, mime_type)

            if not text.strip():
                log.append(f"[{i}] {file_name} — пропущен (пустой текст)")
                continue

            category = guess_category(file_name)

            cur.execute(
                "SELECT id FROM knowledge_base WHERE source_file = %s LIMIT 1",
                (file_name,),
            )
            existing = cur.fetchone()

            if existing:
                cur.execute(
                    "UPDATE knowledge_base SET title=%s, content=%s, category=%s WHERE source_file=%s",
                    (file_name, text, category, file_name),
                )
                log.append(f"[{i}] {file_name} — обновлён")
                updated += 1
            else:
                cur.execute(
                    "INSERT INTO knowledge_base (title, content, category, source_file) VALUES (%s, %s, %s, %s)",
                    (file_name, text, category, file_name),
                )
                log.append(f"[{i}] {file_name} — загружен")
                loaded += 1

            conn.commit()

        except Exception as e:
            log.append(f"[{i}] {file_name} — ОШИБКА: {e}")
            conn.rollback()
            errors += 1
        finally:
            if tmp_path and Path(tmp_path).exists():
                os.unlink(tmp_path)

    cur.close()
    conn.close()

    log.append(f"Готово! Загружено: {loaded}, обновлено: {updated}, ошибок: {errors}")
    return {"loaded": loaded, "updated": updated, "errors": errors, "log": log}


def main():
    result = sync_drive_to_db()
    for line in result["log"]:
        print(line)


if __name__ == "__main__":
    main()
