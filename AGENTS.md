# AGENTS.md

## Cursor Cloud specific instructions

### Overview

MushroomsAI is a monolithic Python 3.11 FastAPI application combining a web platform, Telegram bot, and background scheduler. See `README.md` for general project structure and quick-start guide.

### Services

| Service | Required | Notes |
|---|---|---|
| **PostgreSQL** | Yes | App will not start without `DATABASE_URL`. Tables are auto-created on startup. |
| **OpenAI API** | For AI chat only | Set `OPENAI_API_KEY`. The web app starts fine with a dummy key; AI chat requests will fail. |
| **Telegram Bot** | No | Bot startup is skipped if `TELEGRAM_TOKEN` is empty. |
| **Google OAuth** | No | Google sign-in disabled if `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` are empty. |

### Running the app

```bash
source /workspace/venv/bin/activate
sudo service postgresql start
uvicorn main:app --reload --port 8000
```

The app is then available at `http://localhost:8000`. Health check: `GET /health`.

### Key caveats

- **bcrypt compatibility**: The project uses `passlib[bcrypt]`. The latest `bcrypt>=4.1` breaks `passlib`'s wrap-bug detection. Pin `bcrypt==4.0.1` in the venv to avoid `ValueError` on password hashing. The update script handles this.
- **PostgreSQL must be running** before starting the app. Use `sudo service postgresql start`.
- **Login page UI** only shows Telegram/Google OAuth buttons. Email registration is available via `POST /register/email` (form fields: `email`, `password`, `name`) but there is no visible form in the UI by default.
- **No existing test suite or lint configuration** in the repo. Use `ruff` for ad-hoc linting: `ruff check .`.
- **Media files** are stored under `./media/` locally (or `/data/` on Render).
- **Database migrations** run inline during app startup in `main.py` lifespan — no separate migration command needed.

### Environment variables

Copy `.env.example` to `.env` and fill in values. For local dev, the minimum required is:
- `DATABASE_URL=postgresql://mushroomsai:mushroomsai@localhost:5432/mushroomsai`
- `JWT_SECRET=<any-32+-char-string>`

All other variables have sensible defaults or are optional.
