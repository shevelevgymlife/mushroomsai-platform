import os
import io
import logging
from typing import List, Dict
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.oauth2 import service_account
import google.auth
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
import json

logger = logging.getLogger(__name__)

FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

def get_drive_service():
    """Подключение к Google Drive API через API key"""
    try:
        from googleapiclient.discovery import build
        import httplib2
        service = build('drive', 'v3', developerKey=os.getenv('GOOGLE_API_KEY', ''))
        return service
    except Exception as e:
        logger.error(f"Drive connection error: {e}")
        return None

def list_files_in_folder(folder_id: str) -> List[Dict]:
    """Получить список файлов из папки Google Drive"""
    service = get_drive_service()
    if not service:
        return []
    try:
        results = service.files().list(
            q=f"'{folder_id}' in parents",
            fields="files(id, name, mimeType)"
        ).execute()
        return results.get('files', [])
    except Exception as e:
        logger.error(f"Error listing files: {e}")
        return []

def extract_text_from_pdf(content: bytes) -> str:
    """Извлечь текст из PDF"""
    try:
        import PyPDF2
        reader = PyPDF2.PdfReader(io.BytesIO(content))
        text = ""
        for page in reader.pages:
            text += page.extract_text() + "\n"
        return text
    except Exception as e:
        logger.error(f"PDF extract error: {e}")
        return ""

def extract_text_from_docx(content: bytes) -> str:
    """Извлечь текст из DOCX"""
    try:
        import docx
        doc = docx.Document(io.BytesIO(content))
        return "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
    except Exception as e:
        logger.error(f"DOCX extract error: {e}")
        return ""

def extract_text_from_json(content: bytes) -> str:
    """Извлечь текст из JSON (посты Telegram)"""
    try:
        data = json.loads(content.decode('utf-8'))
        messages = data.get('messages', [])
        texts = []
        for msg in messages:
            if msg.get('type') == 'message':
                text = msg.get('text', '')
                if isinstance(text, list):
                    text = ''.join(p if isinstance(p, str) else p.get('text', '') for p in text)
                if text.strip() and len(text) > 50:
                    texts.append(text.strip())
        return "\n\n".join(texts)
    except Exception as e:
        logger.error(f"JSON extract error: {e}")
        return ""

# Кэш базы знаний в памяти
_knowledge_cache: List[Dict] = []
_cache_loaded = False

def get_knowledge_base() -> List[Dict]:
    """Получить базу знаний (из кэша или загрузить)"""
    global _knowledge_cache, _cache_loaded
    if _cache_loaded:
        return _knowledge_cache
    return _knowledge_cache

def search_knowledge(query: str, top_k: int = 3) -> str:
    """Простой поиск по базе знаний через OpenAI embeddings"""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        
        if not _knowledge_cache:
            return ""
        
        # Простой поиск по ключевым словам
        query_lower = query.lower()
        results = []
        
        for chunk in _knowledge_cache:
            text = chunk.get('text', '')
            score = sum(1 for word in query_lower.split() if word in text.lower())
            if score > 0:
                results.append((score, text))
        
        results.sort(key=lambda x: x[0], reverse=True)
        top_results = [r[1] for r in results[:top_k]]
        
        return "\n\n---\n\n".join(top_results)
    except Exception as e:
        logger.error(f"Search error: {e}")
        return ""
