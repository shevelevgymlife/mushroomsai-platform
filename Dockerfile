# Запасной вариант деплоя на Render, если native Python build падает:
# Dashboard → New Web Service → подключить этот репо → Environment: Docker
# Те же env: DATABASE_URL, JWT_SECRET, …
FROM python:3.11-slim-bookworm

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

# Render подставляет PORT при старте
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
