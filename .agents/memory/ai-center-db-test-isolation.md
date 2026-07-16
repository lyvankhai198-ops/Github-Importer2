---
name: ai-center DB test isolation trap
description: config.py hardcodes DATABASE_URL to sqlite:///{BASE_DIR}/ai_center.db and ignores the DATABASE_URL env var entirely — any ad-hoc test script must monkeypatch database.engine/SessionLocal, not set an env var, or it will silently write into the real dev database.
---

In `artifacts/api-server`, `config.py` sets `DATABASE_URL = f"sqlite:///{BASE_DIR}/ai_center.db"` as a hardcoded literal — it does **not** read `os.environ["DATABASE_URL"]`. Setting the env var before importing the app's modules has zero effect.

**Why this matters:** a quick verification script that does `os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")` before importing `database`/`models`/`services` will still bind to the real `ai_center.db`, silently inserting fake users/deposits/config rows into the actual dev database.

**How to apply:** for any throwaway test/verification script against this app, import `database` first, then reassign `database.engine` and `database.SessionLocal` to a fresh engine pointed at a temp sqlite file, and only *then* import `models`/`services` (so their `from database import SessionLocal` picks up the patched one). After the script, delete the temp db file. Always spot-check row counts in the real `ai_center.db` before and after running any test script that touches the DB layer, in case this trap is hit again.
