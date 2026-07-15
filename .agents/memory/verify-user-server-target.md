---
name: Verify which server the user is actually hitting
description: A user's bug report (data missing, feature broken, screenshot showing something the workspace DB doesn't have) can be caused by them testing a completely different deployment, not this workspace.
---

Before spending a long time chasing a "this data disappeared" or "this feature works for me but not for the customer" bug, ask the user for the exact browser URL/address bar content they are testing against — early, not after exhausting code-level hypotheses.

**Why:** Spent a long investigation (workflow restarts, DB forensics, simulated handler calls, migration audits) chasing why a freshly-created row (a tenant account) kept vanishing from the workspace's SQLite DB, including verifying single-process/single-file/no-caching to rule out every code-level explanation. The real cause: the user's browser was pointed at `ip:port` for an entirely separate self-hosted VPS deployment of the same codebase, not the Replit workspace at all. All the "disappearing data" was normal — it just never touched this workspace's database.

**How to apply:** When a report is "I did X and saw Y" but your direct inspection of this workspace's code/DB/logs shows no trace of X ever happening, treat "wrong server" as a first-class hypothesis, not a last resort. Ask for the literal URL early. A raw IP:port (not a `*.replit.dev`/`*.replit.app` domain) is a strong signal it's an external deployment.
