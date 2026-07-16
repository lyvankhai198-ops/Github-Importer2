---
name: Static asset caching hides CSS/JS fixes from real devices
description: /static/* is excluded from the app's global no-cache middleware, so a real phone can silently keep serving stale CSS/JS after a fix ships.
---

`main.py`'s response middleware sets `Cache-Control: no-store, no-cache, must-revalidate, max-age=0` on every response EXCEPT paths starting with `/static/` (intentional exclusion, so static assets can be cached). This means after editing `static/css/style.css` or `static/js/main.js`, a user's phone/browser can keep serving the old cached file even after a normal page refresh — it looks like the fix "didn't work" when it actually just never reached the device.

**Why:** discovered when a mobile layout-overflow CSS fix appeared to have no effect on the user's real phone after a plain refresh; the actual file on disk was correct, but the browser was serving a cached copy.

**How to apply:** `templates/base.html` now loads `style.css?v=2` — bump that `?v=` query param every time `style.css` (or any other cached static asset linked the same way) changes, or tell the user to hard-refresh / clear cache, otherwise the fix won't be visible to already-loaded sessions.
