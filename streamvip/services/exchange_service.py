from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Optional

import httpx
import redis

from config import settings
from database import get_supabase
from utils.helpers import venezuela_now

logger = logging.getLogger(__name__)

RATE_CACHE_KEY = "exchange_rate:current"
RATE_TTL_SECONDS = 1800  # 30 minutes
RATE_STALE_HOURS = 8

# ─────────────────────────────────────────────────────────────────
# AUTO-FETCH DESDE BINANCE P2P (USDT → VES)
# ─────────────────────────────────────────────────────────────────

async def fetch_binance_p2p_rate() -> Optional[float]:
    """
    Consulta el mercado P2P de Binance para obtener la tasa USDT/VES.
    Toma el promedio de los 3 primeros vendedores (SELL).
    Retorna None si falla.
    """
    url = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
    payload = {
        "fiat": "VES",
        "page": 1,
        "rows": 5,
        "tradeType": "SELL",
        "asset": "USDT",
        "countries": [],
        "proMerchantAds": False,
        "shieldMerchantAds": False,
        "filterType": "all",
        "periods": [],
        "additionalKycVerifyFilter": 0,
        "publisherType": None,
        "payTypes": [],
        "classifies": ["mass", "profession"],
    }
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        ads = data.get("data", [])
        if not ads:
            logger.warning("Binance P2P: no ads returned for USDT/VES")
            return None

        prices = []
        for ad in ads[:3]:
            price_str = ad.get("adv", {}).get("price")
            if price_str:
                prices.append(float(price_str))

        if not prices:
            return None

        avg = round(sum(prices) / len(prices), 2)
        logger.info(f"Binance P2P tasa obtenida: {avg} VES/USDT (promedio de {len(prices)} ads)")
        return avg

    except Exception as e:
        logger.error(f"Error fetching Binance P2P rate: {e}")
        return None


async def auto_update_rate(admin_telegram_id: int = 0) -> Optional[float]:
    """
    Obtiene la tasa Binance P2P automáticamente y la guarda en BD.
    Retorna la nueva tasa o None si falló.
    admin_telegram_id=0 significa actualización automática del scheduler.
    """
    rate = await fetch_binance_p2p_rate()
    if rate is None:
        logger.warning("Auto-update de tasa fallido: no se pudo obtener de Binance P2P")
        return None

    success = await update_rate(rate, admin_telegram_id)
    if success:
        logger.info(f"Tasa Binance P2P auto-actualizada: {rate} Bs/USD")
        return rate
    return None


def _get_redis() -> redis.Redis:
    return redis.from_url(settings.REDIS_URL, decode_responses=True)


async def get_current_rate() -> Optional[dict]:
    """Get current exchange rate from Redis cache or DB."""
    try:
        r = _get_redis()
        cached = r.get(RATE_CACHE_KEY)
        if cached:
            return json.loads(cached)
    except Exception as e:
        logger.warning(f"Redis cache miss for exchange rate: {e}")

    # Fall back to DB
    try:
        sb = get_supabase()
        result = (
            sb.table("exchange_rates")
            .select("*")
            .order("updated_at", desc=True)
            .limit(1)
            .execute()
        )
        row = result.data[0] if result.data else None
        if row:
            try:
                r = _get_redis()
                r.setex(RATE_CACHE_KEY, RATE_TTL_SECONDS, json.dumps(row, default=str))
            except Exception:
                pass
            return row
    except Exception as e:
        logger.error(f"Error fetching exchange rate from DB: {e}")

    return None


async def calculate_price_bs(price_usd: float) -> float:
    """Convert USD to Bolivares using current Binance rate."""
    rate = await get_current_rate()
    if not rate or not rate.get("usd_binance"):
        # Fallback rate
        return round(price_usd * 36.0, 2)
    usd_binance = float(rate["usd_binance"])
    return round(price_usd * usd_binance, 2)


async def format_price_display(price_usd: float) -> str:
    """Return formatted price string in USD and Bs."""
    price_bs = await calculate_price_bs(price_usd)
    rate = await get_current_rate()
    rate_value = float(rate["usd_binance"]) if rate and rate.get("usd_binance") else 36.0
    return f"${price_usd:.2f} USD = Bs {price_bs:,.2f} (tasa Bs {rate_value:.2f}/USD)"


async def update_rate(usd_binance: float, updated_by: int, usd_bcv: Optional[float] = None, eur_bcv: Optional[float] = None) -> bool:
    """Update exchange rate in DB and invalidate cache."""
    try:
        sb = get_supabase()
        data: dict = {
            "usd_binance": usd_binance,
            "updated_at": venezuela_now().isoformat(),
            "updated_by": updated_by,
        }
        if usd_bcv is not None:
            data["usd_bcv"] = usd_bcv
        if eur_bcv is not None:
            data["eur_bcv"] = eur_bcv
        sb.table("exchange_rates").insert(data).execute()

        # Invalidate Redis cache
        try:
            r = _get_redis()
            r.delete(RATE_CACHE_KEY)
        except Exception:
            pass

        return True
    except Exception as e:
        logger.error(f"Error in update_rate: {e}")
        return False


async def check_rate_staleness() -> Optional[str]:
    """
    Check if exchange rate is stale (> RATE_STALE_HOURS hours old).
    Returns warning message or None.
    """
    try:
        rate = await get_current_rate()
        if not rate:
            return "⚠️ No hay tasa de cambio configurada."

        from datetime import datetime
        import pytz

        updated_at_str = rate.get("updated_at")
        if not updated_at_str:
            return "⚠️ Tasa de cambio sin fecha de actualización."

        if isinstance(updated_at_str, str):
            # Parse ISO format
            updated_at = datetime.fromisoformat(updated_at_str.replace("Z", "+00:00"))
        else:
            updated_at = updated_at_str

        now = venezuela_now()
        if updated_at.tzinfo is None:
            import pytz
            updated_at = pytz.utc.localize(updated_at)

        diff = now - updated_at.astimezone(pytz.timezone("America/Caracas"))
        hours_old = diff.total_seconds() / 3600

        if hours_old > RATE_STALE_HOURS:
            return f"⚠️ La tasa de cambio tiene {hours_old:.0f} horas sin actualizar. Por favor actualiza con /tasa"

        return None
    except Exception as e:
        logger.error(f"Error in check_rate_staleness: {e}")
        return None
