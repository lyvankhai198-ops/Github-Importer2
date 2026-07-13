# AI Center Bot Manager

A web dashboard + Telegram bot for running a digital-goods shop: manage products, orders, and customers, and accept payments via bank transfer (SePay), Binance Pay, or USDT (BEP20/TRC20).

## Run & Operate

- API + bot: runs via the `artifacts/api-server: API Server` workflow (`python3 main.py` inside `artifacts/api-server`)
- Canvas/mockup sandbox: `artifacts/mockup-sandbox: Component Preview Server` workflow
- Default admin login: `admin` / `admin123` (change from the Settings page)
- Telegram bot token, payment gateway keys, and integration API keys are configured from the web dashboard (Settings / API Connections pages), not env vars — they're stored encrypted in the database
- `ENCRYPTION_KEY` / `SECRET_KEY` are set as environment variables so encrypted settings and sessions survive restarts

## Stack

- Python 3.13, FastAPI + Jinja2 server-rendered dashboard, SQLAlchemy + SQLite (`artifacts/api-server/ai_center.db`)
- `python-telegram-bot` for the Telegram bot, running as a background task alongside the FastAPI app
- Payment methods: SePay (bank transfer), Binance Pay (manual or merchant/API mode), USDT BEP20/TRC20 (on-chain watcher)
- The pnpm workspace / Node.js scaffold (`lib/*`, `mockup-sandbox`) is present from the project template but the product itself is the Python `api-server` app

## Where things live

- `artifacts/api-server/main.py` — FastAPI app entrypoint, lifespan startup (migrations, seeding, background workers)
- `artifacts/api-server/bot/` — Telegram bot handlers, keyboards, i18n, notifier
- `artifacts/api-server/routers/` — dashboard HTTP routes (auth, orders, products, users, settings, API connections, webhooks)
- `artifacts/api-server/services/` — business logic (orders, payments, crypto monitor, exchange rates, product sync)
- `artifacts/api-server/models.py` — SQLAlchemy models
- `artifacts/api-server/templates/` — Jinja2 dashboard pages

## Architecture decisions

- Bot token and third-party API keys live encrypted in the DB (via `crypto.py`, Fernet), configured through the dashboard — not `.env` — so non-technical admins can rotate them without a redeploy.
- Order → payment method is decoupled: an order is created first, then the user picks a payment method, keeping quantity entry independent of payment choice.
- Concurrent crypto payments to the same wallet are disambiguated with tiny per-order amount offsets (see `.agents/memory/crypto-uniqueness.md`).
- SQLite schema changes ship as idempotent `ALTER TABLE` migrations run at startup (`_run_migrations` in `main.py`), each wrapped in try/except so re-running is safe.

## Product

- Customers browse and buy digital products through the Telegram bot, in Vietnamese or English.
- Admins manage products (including synced from external supplier APIs), track orders, and configure payment methods and bot settings from the web dashboard.

## User preferences

- Imported from the user's existing GitHub project (`01022341869m-cmyk/Aicenter`) rather than built fresh — treat the Python `api-server` app as the source of truth going forward.

## Gotchas

- The dashboard/bot only starts correctly with Python dependencies installed (`fastapi`, `uvicorn`, `python-telegram-bot`, `sqlalchemy`, `cryptography`, etc. — already installed).
- If `ENCRYPTION_KEY` is ever unset, the app auto-generates a throwaway one at startup and warns in logs — that would make previously-encrypted settings (bot token, API keys) unreadable. Keep it set.

## Pointers

- See `.agents/memory/` for detailed notes on payment flow, crypto uniqueness, Binance modes, background workers, i18n, and migrations.
- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details (applies to the template scaffolding, not the Python app).
