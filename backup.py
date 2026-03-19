"""
Backup script: copies important config files and creates a ZIP archive.
"""
import os
import shutil
import zipfile
from datetime import date

today = date.today().strftime("%Y-%m-%d")
backup_dir = f"backup_{today}"
zip_name = f"backup_{today}.zip"

# 1. Create backup folder
os.makedirs(backup_dir, exist_ok=True)
print(f"[1/4] Папка {backup_dir}/ создана")

# 2. Copy important files
files_to_copy = [".env", "service_account.json"]
copied = []
skipped = []

for fname in files_to_copy:
    if os.path.exists(fname):
        shutil.copy2(fname, os.path.join(backup_dir, fname))
        copied.append(fname)
        print(f"[2/4] Скопирован: {fname}")
    else:
        skipped.append(fname)
        print(f"[2/4] Пропущен (не найден): {fname}")

# 3. Create README_RESTORE.txt
readme_path = os.path.join(backup_dir, "README_RESTORE.txt")
readme_content = f"""=============================================================
  MUSHROOMSAI PLATFORM — ИНСТРУКЦИЯ ПО ВОССТАНОВЛЕНИЮ
  Дата резервной копии: {today}
=============================================================

ССЫЛКИ НА СЕРВИСЫ
-----------------
GitHub (исходный код):
  https://github.com/shevelevgymlife/mushroomsai-platform

Render (деплой / продакшн):
  https://dashboard.render.com

Google Cloud Console (OAuth, сервисный аккаунт):
  https://console.cloud.google.com

Telegram Bot:
  @mushrooms_ai_bot
  Управление через @BotFather

=============================================================
ИНСТРУКЦИЯ ПО ВОССТАНОВЛЕНИЮ
=============================================================

Шаг 1. Клонируй репозиторий
  git clone https://github.com/shevelevgymlife/mushroomsai-platform.git
  cd mushroomsai-platform

Шаг 2. Скопируй файлы из этого архива
  Скопируй .env         → в корень проекта (рядом с main.py)
  Скопируй service_account.json → в корень проекта

Шаг 3. Установи зависимости
  pip install -r requirements.txt

Шаг 4. Запусти локально (для проверки)
  uvicorn main:app --reload

Шаг 5. Задеплой на Render
  - Зайди на https://dashboard.render.com
  - Создай новый Web Service из GitHub репозитория
  - Добавь переменные окружения из .env в раздел Environment
  - Нажми Deploy

ФАЙЛЫ В ЭТОМ АРХИВЕ
--------------------
{chr(10).join('  ' + f for f in copied) if copied else '  (нет файлов)'}

ВАЖНО
-----
- Никогда не публикуй .env и service_account.json в GitHub
- Храни этот ZIP в безопасном месте (Google Drive / личное облако)
- Обновляй резервную копию после каждого изменения .env
"""

with open(readme_path, "w", encoding="utf-8") as f:
    f.write(readme_content)
print(f"[3/4] README_RESTORE.txt создан")

# 4. Pack into ZIP
with zipfile.ZipFile(zip_name, "w", zipfile.ZIP_DEFLATED) as zf:
    for root, dirs, files in os.walk(backup_dir):
        for file in files:
            file_path = os.path.join(root, file)
            arcname = os.path.relpath(file_path, start=".")
            zf.write(file_path, arcname)
print(f"[4/4] Архив {zip_name} создан")

# Cleanup temp folder
shutil.rmtree(backup_dir)

# 5. Done
size_kb = os.path.getsize(zip_name) // 1024 or 1
print()
print(f"OK! Резервная копия готова! Загрузи {zip_name} на Google Drive")
print(f"  Размер: {size_kb} КБ")
print(f"  Содержит: {', '.join(copied + ['README_RESTORE.txt']) if copied else 'README_RESTORE.txt'}")
