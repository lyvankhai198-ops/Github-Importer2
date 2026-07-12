---
name: Telegram bot watchdog/reconnect pattern
description: How the bot supervisor auto-starts, reconnects with backoff, and distinguishes fatal auth errors from transient drops
---

The bot runs under a supervisor loop (not a bare task) so a dropped connection never kills the web app and never needs a manual restart from the admin UI.

- Backoff schedule: 5s/15s/30s/60s, cycling for up to 10 rounds (40 attempts), then a slow 5-minute retry forever. Implement as `attempt <= 40 ? seq[(attempt-1) % 4] : 300`, not a hand-rolled counter — off-by-one errors here silently change the retry cadence.
- Distinguish **fatal auth errors** (invalid/revoked token — `telegram.error.InvalidToken`/`Forbidden` in PTB v20+, `Unauthorized` was removed) from transient errors. Fatal errors should stop retrying and surface a distinct `error` status; transient errors go through the backoff and use a `reconnecting` status. Without this split, a bad token retries forever and never gives the admin a clear signal.

**Why:** an unhandled drop previously just died to a generic `error` state with no recovery path; conflating "bad token" with "network hiccup" either spams retries against a token that will never work, or gives up too early on a recoverable blip.

**How to apply:** any long-lived external connection (webhooks, polling loops, payment gateway sockets) in this app should follow the same supervisor-with-backoff shape, reusing this distinction between fatal-config errors and transient errors.
