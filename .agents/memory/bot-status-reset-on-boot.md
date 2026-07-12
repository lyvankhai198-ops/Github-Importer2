---
name: Reset persisted process-state on boot
description: Why a fresh process must not trust status fields left over from a previous process's lifetime
---

`TelegramBotConfig.bot_status` (running/starting/reconnecting/error/stopped) is persisted in the DB so the admin UI can read it, but it describes an in-memory task from a previous process. A freshly booted process has no live task yet regardless of what's stored — always reset such status fields to a safe baseline (`stopped`) at the very start of app boot, before any auto-start logic runs, or the admin UI shows a stale "running" badge for a bot that isn't actually connected.

**Why:** discovered when the DB still said `running` after a full process restart with the bot disabled, because nothing had ever corrected the stale value.

**How to apply:** any "current runtime state of a background worker" column that's persisted for UI display (not just for historical logging) needs this reset-on-boot step. Pattern is not required for plain historical fields like `last_sync_at`/`last_error`.
