"""
Global "chợ" (shared marketplace) pricing defaults.

- default_markup_percent: applied ONCE, at the moment a product is first
  pulled/attached from a supplier API with no explicit sale_price typed in
  by the admin/tenant. sale_price = source_price * (1 + markup/100). After
  that, price_sync_service's existing margin-preserving auto-adjust takes
  over (keeps the resulting VND markup fixed as the source price moves) —
  this module does NOT touch ongoing sync, only the initial default.

Stored as one JSON blob in the generic Setting(key, value) table (same
pattern as "exchange_rate_config" in routers/settings.py) rather than
dedicated columns, since this is a single global owner-level config, not
per-tenant or per-product.
"""
import json

from models import Setting

_SETTING_KEY = "market_pricing_config"

DEFAULT_MARKUP_PERCENT = 10.0


def get_market_pricing_config(db) -> dict:
    s = db.query(Setting).filter(Setting.key == _SETTING_KEY).first()
    if not s or not s.value:
        return {"default_markup_percent": DEFAULT_MARKUP_PERCENT}
    try:
        cfg = json.loads(s.value)
    except Exception:
        cfg = {}
    return {
        "default_markup_percent": float(cfg.get("default_markup_percent", DEFAULT_MARKUP_PERCENT)),
    }


def save_market_pricing_config(db, default_markup_percent: float) -> dict:
    cfg = {"default_markup_percent": max(0.0, float(default_markup_percent))}
    s = db.query(Setting).filter(Setting.key == _SETTING_KEY).first()
    if not s:
        s = Setting(key=_SETTING_KEY)
        db.add(s)
    s.value = json.dumps(cfg)
    db.commit()
    return cfg


def default_sale_price(db, source_price: float) -> float:
    """source_price marked up by default_markup_percent, rounded to whole VND."""
    cfg = get_market_pricing_config(db)
    markup = cfg["default_markup_percent"]
    return round((source_price or 0.0) * (1 + markup / 100.0))
