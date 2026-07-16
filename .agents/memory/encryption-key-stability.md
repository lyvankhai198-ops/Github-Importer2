---
name: ENCRYPTION_KEY must be stable across restarts
description: Root cause of "every VPS restart breaks saved tokens/API connections" and the fix applied.
---

`crypto.py` encrypts every stored secret (Telegram bot token, supplier API keys, payment
credentials) with a Fernet key from the `ENCRYPTION_KEY` env var. If that env var isn't set,
the old behavior generated a **brand-new random key in memory only** on every process start —
never saved anywhere. Every restart (VPS redeploy, workflow restart) then silently broke
`decrypt()` for everything already in the database, since it was encrypted with the previous
process's throwaway key. Symptom reported by the user: after every update/redeploy, supplier API
connections show disconnected and the Telegram bot token has to be re-entered — nothing was
actually corrupted in the DB, decrypt() just returns "" (see `crypto.py`'s except-swallow) and
call sites report it as an unusable/failed connection.

**Why:** Python has no hot code reload for a long-running asyncio process; restarting to load new
code is unavoidable, so any secret whose key isn't restart-stable will look "lost" every time.

**How to apply:** `crypto._get_fernet()` now falls back to a key persisted at
`artifacts/api-server/.encryption_key` (gitignored, chmod 600) when `ENCRYPTION_KEY` isn't set,
generating it once and reusing it on every subsequent boot. Setting a real `ENCRYPTION_KEY`
env var/secret is still the more robust production option and takes precedence — recommend it,
but the file fallback means the app no longer requires it to survive restarts. If this class of
bug resurfaces (something needs to be redone after every restart), suspect an env var that's
regenerated instead of persisted, not a database/data-loss issue.
