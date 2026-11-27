"""Lectura de los datos que necesita el detector de anomalías (O02 Lote 5). Solo lectura."""
from __future__ import annotations

import logging

from database import get_supabase

logger = logging.getLogger(__name__)
_LIMIT = 5000


async def fetch_anomaly_data() -> tuple[list[dict], list[dict]]:
    """(suscripciones relevantes, perfiles). Ante un error de base devuelve listas vacías y lo registra
    (la pantalla lo dice: no se confunde «sin anomalías» con «no se pudo leer»; ver `ok`)."""
    sb = get_supabase()
    subs = (
        sb.table("subscriptions")
        .select("id, user_id, platform_id, profile_id, plan_type, status, end_date, created_at, payment_reference, "
                "debt_reminder_count, users(name, username, telegram_id), platforms(name)")
        .in_("status", ["active", "expired", "expired_payment", "pending_payment"])
        .limit(_LIMIT)
        .execute()
        .data or []
    )
    profiles = (
        sb.table("profiles")
        .select("id, platform_id, profile_name, profile_type, status, platforms(name)")
        .limit(_LIMIT)
        .execute()
        .data or []
    )
    return subs, profiles
