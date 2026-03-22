# AGENTS.md

## Cursor Cloud specific instructions

### Overview

MushroomsAI is a monolithic Python 3.11 FastAPI application — an AI-powered platform for functional mushrooms with an AI consultant (GPT-4o), e-commerce marketplace, and social network features. It serves server-rendered HTML (Jinja2 + Tailwind CSS) and includes a Telegram bot in the same process.

### Required services

| Service | How to run |
|---|---|
| **PostgreSQL** | `sudo pg_ctlcluster 16 main start` — must be running before the app starts |
| **FastAPI app** | `source .venv/bin/activate && uvicorn main:app --reload --port 8000` |

### Key caveats

- **bcrypt compatibility**: The `passlib[bcrypt]` dependency requires `bcrypt==4.0.1`. Newer bcrypt versions (5.x) break password hashing. The update script pins this automatically.
- **No test suite**: The project has no automated tests (`pytest`, etc.) and no lint configuration (`flake8`, `pyright`, etc.). Syntax-check all Python files with `python -m py_compile`.
- **No Docker**: The project runs directly with Python + PostgreSQL; there are no Dockerfiles or docker-compose configs.
- **Database migrations are inline**: Tables are created on startup via `metadata.create_all(engine)`, and column migrations run as `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` SQL in the `lifespan` function of `main.py`.
- **Telegram bot is optional**: If `TELEGRAM_TOKEN` is empty in `.env`, the bot is gracefully skipped. The web app works independently.
- **OpenAI API key is optional for startup**: The app starts without `OPENAI_API_KEY`, but the AI chat feature will fail at runtime without it.
- **Auth methods**: Email/password registration works without external services. Telegram and Google OAuth require their respective API keys.
- **Media directory**: The app uses `./media` locally (auto-created on startup) and `/data` in production (Render Disk).
- **README** (`README.md`) documents the quick-start, env vars, and project structure.
