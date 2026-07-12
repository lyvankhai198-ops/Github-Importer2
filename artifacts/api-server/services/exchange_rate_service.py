"""
Exchange rate service: VND → USDT conversion.

Admin configures one of two modes:
  1. fixed_rate: 1 USDT = N VND (admin-set)
  2. auto_rate:  fetched from Binance/CoinGecko + crypto_markup_percent buffer

Config stored in Setting table under key "exchange_rate_config" as JSON:
{
  "mode": "fixed" | "auto",
  "fixed_rate": 26500,          // VND per 1 USDT
  "crypto_markup_percent": 2.0, // e.g. 2% buffer
  "round_to_decimals": 4,       // USDT rounding precision
  "auto_source": "binance"      // "binance" | "coingecko"
}
"""

import json
import logging
import asyncio
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "mode": "fixed",
    "fixed_rate": 26500.0,
    "crypto_markup_percent": 2.0,
    "round_to_decimals": 4,
    "auto_source": "binance",
}

# Simple in-memory cache for auto rate
_rate_cache: dict = {"rate": None, "fetched_at": 0.0}
_CACHE_TTL = 300  # 5 minutes


def get_exchange_config(db) -> dict:
    from models import Setting
    row = db.query(Setting).filter(Setting.key == "exchange_rate_config").first()
    if row and row.value:
        try:
            return {**DEFAULT_CONFIG, **json.loads(row.value)}
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_exchange_config(db, config: dict):
    from models import Setting
    from datetime import datetime
    row = db.query(Setting).filter(Setting.key == "exchange_rate_config").first()
    if row:
        row.value = json.dumps(config)
        row.updated_at = datetime.utcnow()
    else:
        row = Setting(key="exchange_rate_config", value=json.dumps(config))
        db.add(row)
    db.commit()


async def _fetch_usdt_vnd_rate_binance() -> Optional[float]:
    """Fetch USDT/VND rate from Binance P2P (USDT to VND)."""
    try:
        import httpx
        url = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
        payload = {
            "asset": "USDT",
            "fiat": "VND",
            "tradeType": "SELL",
            "page": 1,
            "rows": 5,
            "payTypes": [],
        }
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, json=payload)
        if r.status_code == 200:
            data = r.json()
            prices = [float(ad["adv"]["price"]) for ad in (data.get("data") or []) if ad.get("adv", {}).get("price")]
            if prices:
                return sum(prices) / len(prices)
    except Exception as e:
        logger.warning(f"[exchange_rate] Binance P2P fetch failed: {e}")
    return None


async def _fetch_usdt_vnd_rate_coingecko() -> Optional[float]:
    try:
        import httpx
        url = "https://api.coingecko.com/api/v3/simple/price?ids=tether&vs_currencies=vnd"
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url)
        if r.status_code == 200:
            data = r.json()
            return float(data["tether"]["vnd"])
    except Exception as e:
        logger.warning(f"[exchange_rate] CoinGecko fetch failed: {e}")
    return None


async def get_current_rate(db) -> float:
    """Return VND per 1 USDT. Uses fixed or auto mode from config."""
    import time
    cfg = get_exchange_config(db)

    if cfg["mode"] == "fixed":
        return float(cfg.get("fixed_rate") or 26500.0)

    # Auto mode — check cache
    now = time.time()
    if _rate_cache["rate"] and (now - _rate_cache["fetched_at"]) < _CACHE_TTL:
        return _rate_cache["rate"]

    source = cfg.get("auto_source", "binance")
    rate = None
    if source == "binance":
        rate = await _fetch_usdt_vnd_rate_binance()
    if rate is None:
        rate = await _fetch_usdt_vnd_rate_coingecko()
    if rate is None:
        # Fallback to fixed_rate
        rate = float(cfg.get("fixed_rate") or 26500.0)
        logger.warning(f"[exchange_rate] auto fetch failed, using fixed_rate={rate}")

    _rate_cache["rate"] = rate
    _rate_cache["fetched_at"] = now
    return rate


def vnd_to_usdt(vnd_amount: float, rate: float, markup_percent: float = 0.0,
                round_to: int = 4) -> float:
    """
    Convert VND amount to USDT.
    markup_percent adds a buffer for volatility/fees.
    """
    if rate <= 0:
        raise ValueError("Exchange rate must be positive")
    usdt = vnd_amount / rate
    if markup_percent:
        usdt *= (1 + markup_percent / 100.0)
    return round(usdt, round_to)


async def calculate_crypto_amount(db, vnd_amount: float) -> tuple[float, float]:
    """
    Returns (usdt_amount, rate_used).
    usdt_amount includes markup.
    """
    cfg = get_exchange_config(db)
    rate = await get_current_rate(db)
    markup = float(cfg.get("crypto_markup_percent") or 0.0)
    round_to = int(cfg.get("round_to_decimals") or 4)
    usdt = vnd_to_usdt(vnd_amount, rate, markup, round_to)
    return usdt, rate


def generate_unique_crypto_amount(db, base_amount: float, network: str) -> float:
    """
    Generate a unique USDT amount by adding a tiny offset to distinguish concurrent orders.
    Scans pending orders on the same network for collision.
    The offset is at most 0.0099 USDT (< 1%).
    """
    from models import Order, OrderStatus, PaymentStatus
    from datetime import datetime, timedelta

    # Find all pending crypto orders on same network in last 24h
    cutoff = datetime.utcnow() - timedelta(hours=24)
    existing_amounts = set()
    orders = (
        db.query(Order.expected_crypto_amount)
        .filter(
            Order.payment_network == network,
            Order.payment_status.in_([
                PaymentStatus.pending.value,
                PaymentStatus.detected.value,
                PaymentStatus.confirming.value,
            ]),
            Order.created_at >= cutoff,
        )
        .all()
    )
    for (amt,) in orders:
        if amt:
            existing_amounts.add(round(float(amt), 4))

    candidate = round(base_amount, 4)
    offset = 0.0001
    for _ in range(99):
        if candidate not in existing_amounts:
            return candidate
        candidate = round(base_amount + offset, 4)
        offset += 0.0001

    return candidate
