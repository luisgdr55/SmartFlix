from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from database import get_supabase
from utils.helpers import venezuela_now

logger = logging.getLogger(__name__)


async def create_subscription(
    user_id: str,
    platform_id: str,
    plan_type: str,
    price_usd: float,
    price_bs: float,
    rate_used: float,
    end_date: datetime,
) -> Optional[dict]:
    """Create a new pending subscription."""
    try:
        sb = get_supabase()
        result = sb.table("subscriptions").insert({
            "user_id": user_id,
            "platform_id": platform_id,
            "plan_type": plan_type,
            "price_usd": price_usd,
            "price_bs": price_bs,
            "rate_used": rate_used,
            "end_date": end_date.isoformat(),
            "start_date": venezuela_now().isoformat(),
            "status": "pending_payment",
            "reminder_sent": False,
            "expiry_notified": False,
        }).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"Error in create_subscription: {e}")
        return None


async def create_active_subscription(
    user_id: str,
    platform_id: str,
    plan_type: str,
    price_usd: float,
    price_bs: float,
    rate_used: float,
    end_date: datetime,
    profile_id: str,
    payment_reference: str,
) -> Optional[dict]:
    """Create a subscription already in 'active' status with a profile assigned.
    Used for manual affiliations where payment was confirmed externally."""
    try:
        sb = get_supabase()
        now = venezuela_now()
        result = sb.table("subscriptions").insert({
            "user_id": user_id,
            "platform_id": platform_id,
            "plan_type": plan_type,
            "price_usd": price_usd,
            "price_bs": price_bs,
            "rate_used": rate_used,
            "start_date": now.isoformat(),
            "end_date": end_date.isoformat(),
            "status": "active",
            "profile_id": profile_id,
            "payment_reference": payment_reference,
            "payment_confirmed_at": now.isoformat(),
            "reminder_sent": False,
            "expiry_notified": False,
        }).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"Error in create_active_subscription: {e}")
        return None


async def get_user_active_subscriptions(user_id: str) -> list[dict]:
    """List all active subscriptions for a user."""
    try:
        sb = get_supabase()
        result = (
            sb.table("subscriptions")
            .select("*, platforms(name, slug, icon_emoji), profiles(profile_name, pin, account_id, is_extra_member, extra_email, extra_password, accounts(email, password))")
            .eq("user_id", user_id)
            .in_("status", ["active", "pending_payment"])
            .order("end_date", desc=False)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"Error in get_user_active_subscriptions: {e}")
        return []


async def get_user_platform_active_subscription(user_id: str, platform_id: str) -> Optional[dict]:
    """Find the most recent active/expired/cancelled subscription for user+platform.
    Used to detect renewals at approval time. Includes cancelled so the scheduler
    cutting a sub before admin approval still routes as renewal (not new client)."""
    try:
        sb = get_supabase()
        result = (
            sb.table("subscriptions")
            .select("*, profiles(id, profile_name, pin, account_id, accounts(email, password))")
            .eq("user_id", user_id)
            .eq("platform_id", platform_id)
            .in_("status", ["active", "expired", "cancelled"])
            .order("end_date", desc=True)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"Error in get_user_platform_active_subscription: {e}")
        return None


async def confirm_renewal_subscription(
    sub_id: str,
    profile_id: str,
    payment_reference: str,
    new_end_date: datetime,
) -> bool:
    """Confirm a renewal: mark as active with the existing profile and a new end_date."""
    try:
        sb = get_supabase()
        sb.table("subscriptions").update({
            "status": "active",
            "profile_id": profile_id,
            "payment_reference": payment_reference,
            "payment_confirmed_at": venezuela_now().isoformat(),
            "end_date": new_end_date.isoformat(),
            # O02 Lote 6: una renovación empieza un ciclo nuevo de avisos (antes quedaban «gastados»).
            "reminder_sent": False,
            "expiry_notified": False,
            "debt_reminder_count": 0,
        }).eq("id", sub_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error in confirm_renewal_subscription: {e}")
        return False


async def get_user_attention_subscriptions(user_id: str) -> dict:
    """Return subs that need user attention: unpaid debt or expired/vencidas.
    Covers all statuses (pending_payment, expired_payment, active-past-end, expired).
    Returns {'pending': [...], 'expired': [...]}
    """
    try:
        sb = get_supabase()
        now = venezuela_now()
        three_days_later = (now + timedelta(days=3)).strftime("%Y-%m-%d")
        result = (
            sb.table("subscriptions")
            .select("*, platforms(name, slug, icon_emoji)")
            .eq("user_id", user_id)
            .not_.in_("status", ["cancelled"])
            .execute()
        )
        subs = result.data or []
        pending = [s for s in subs if s.get("status") in ("pending_payment", "expired_payment")]
        # O02 Lote 6: la mora se deriva de la fecha (active o expired con end_date vencida); ya no hay
        # un job que pase las vencidas a `expired`.
        from services.checkout_service import is_overdue
        from utils.dates import caracas_date
        expired = [s for s in subs if is_overdue(s, now)]
        expiring_soon = [
            s for s in subs
            if s.get("status") == "active"
            and s.get("end_date")
            and not is_overdue(s, now)
            and caracas_date(s["end_date"]).isoformat() <= three_days_later
        ]
        return {"pending": pending, "expired": expired, "expiring_soon": expiring_soon}
    except Exception as e:
        logger.error(f"Error in get_user_attention_subscriptions: {e}")
        return {"pending": [], "expired": []}


async def get_user_pending_subscription(user_id: str) -> Optional[dict]:
    """Return the most recent pending_payment subscription for a user."""
    try:
        sb = get_supabase()
        result = (
            sb.table("subscriptions")
            .select("*, platforms(name, slug, icon_emoji)")
            .eq("user_id", user_id)
            .eq("status", "pending_payment")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"Error in get_user_pending_subscription: {e}")
        return None


async def has_pending_renewal_for_platform(user_id: str, platform_id: str) -> bool:
    """Return True if the user has a pending_payment subscription for this platform.
    Used by the scheduler to avoid cutting a subscription that is already being renewed."""
    try:
        sb = get_supabase()
        result = (
            sb.table("subscriptions")
            .select("id")
            .eq("user_id", user_id)
            .eq("platform_id", platform_id)
            .eq("status", "pending_payment")
            .limit(1)
            .execute()
        )
        return bool(result.data)
    except Exception as e:
        logger.error(f"Error in has_pending_renewal_for_platform: {e}")
        return False


async def save_payment_proof(sub_id: str, payment_reference: str, payment_image_url: str) -> bool:
    """Save payment proof to a pending subscription (awaiting admin approval)."""
    try:
        sb = get_supabase()
        sb.table("subscriptions").update({
            "payment_reference": payment_reference,
            "payment_image_url": payment_image_url,
        }).eq("id", sub_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error in save_payment_proof: {e}")
        return False


async def confirm_subscription(
    sub_id: str,
    profile_id: str,
    payment_reference: str,
    payment_image_url: str,
) -> bool:
    """Confirm payment and assign profile to subscription.

    El UPDATE es condicional (solo si sigue `pending_payment`): devuelve False si no
    actualizó ninguna fila, para que dos aprobaciones simultáneas no asignen dos perfiles."""
    try:
        sb = get_supabase()
        res = sb.table("subscriptions").update({
            "status": "active",
            "profile_id": profile_id,
            "payment_reference": payment_reference,
            "payment_image_url": payment_image_url,
            "payment_confirmed_at": venezuela_now().isoformat(),
        }).eq("id", sub_id).eq("status", "pending_payment").execute()
        return bool(res.data)
    except Exception as e:
        logger.error(f"Error in confirm_subscription: {e}")
        return False


async def expire_subscription(sub_id: str) -> bool:
    """Mark subscription as expired."""
    try:
        sb = get_supabase()
        sb.table("subscriptions").update({"status": "expired"}).eq("id", sub_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error in expire_subscription: {e}")
        return False


async def cancel_subscription(sub_id: str) -> bool:
    """Mark subscription as cancelled."""
    try:
        sb = get_supabase()
        sb.table("subscriptions").update({"status": "cancelled"}).eq("id", sub_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error in cancel_subscription: {e}")
        return False


async def delete_subscription(sub_id: str) -> bool:
    """Permanently delete a subscription record, unlinking dependent household_incidents."""
    try:
        sb = get_supabase()
        # Unlink (not delete) dependent rows to preserve incident history
        sb.table("household_incidents").update({"subscription_id": None}).eq("subscription_id", sub_id).execute()
        sb.table("subscriptions").delete().eq("id", sub_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error in delete_subscription: {e}")
        raise


async def delete_pending_subscriptions_for_platform(user_id: str, platform_id: str) -> int:
    """Delete all pending_payment subscriptions for a user+platform.
    Called before creating a new pending sub to avoid duplicates that confuse the admin."""
    try:
        sb = get_supabase()
        result = (
            sb.table("subscriptions")
            .select("id")
            .eq("user_id", user_id)
            .eq("platform_id", platform_id)
            .eq("status", "pending_payment")
            .execute()
        )
        ids = [r["id"] for r in (result.data or [])]
        for sub_id in ids:
            sb.table("subscriptions").delete().eq("id", sub_id).execute()
        return len(ids)
    except Exception as e:
        logger.error(f"Error in delete_pending_subscriptions_for_platform: {e}")
        return 0


async def get_expiring_subscriptions(days_ahead: int = 3) -> list[dict]:
    """Get subscriptions expiring in the next N days (for reminders)."""
    try:
        sb = get_supabase()
        now = venezuela_now()
        target_date = now + timedelta(days=days_ahead)
        result = (
            sb.table("subscriptions")
            .select("*, users(telegram_id, name), platforms(name, slug, icon_emoji)")
            .eq("status", "active")
            .eq("reminder_sent", False)
            .lte("end_date", target_date.isoformat())
            .gte("end_date", now.isoformat())
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"Error in get_expiring_subscriptions: {e}")
        return []


async def get_pending_subscriptions() -> list[dict]:
    """List all pending payment subscriptions."""
    try:
        sb = get_supabase()
        result = (
            sb.table("subscriptions")
            .select("*, users(telegram_id, name, username), platforms(name, slug, icon_emoji)")
            .eq("status", "pending_payment")
            .order("created_at", desc=True)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"Error in get_pending_subscriptions: {e}")
        return []


async def get_expired_subscriptions_to_notify() -> list[dict]:
    """Suscripciones vencidas que aún no recibieron el aviso D+0: estado `active` o `expired` (legado),
    `end_date` pasada pero **dentro de las últimas 48 h** (así no se avisa de golpe a vencimientos viejos)."""
    try:
        sb = get_supabase()
        now = venezuela_now()
        result = (
            sb.table("subscriptions")
            .select("*, users(telegram_id, name), platforms(name, slug, icon_emoji), profiles(id)")
            .in_("status", ["active", "expired"])
            .eq("expiry_notified", False)
            .lte("end_date", now.isoformat())
            .gte("end_date", (now - timedelta(hours=48)).isoformat())
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"Error in get_expired_subscriptions_to_notify: {e}")
        return []


async def get_expired_express_subscriptions() -> list[dict]:
    """Get express subscriptions that have expired (to release profiles).

    Incluye las `active` vencidas y, por la carrera con `auto_expire`, las `expired` de las
    últimas 24 h cuyo perfil sigue ocupado (O02-H). Las `expired` más viejas no se tocan."""
    try:
        from datetime import timezone

        from services.payment_guards import EXPRESS_EXPIRED_WINDOW, express_release_candidates

        sb = get_supabase()
        now = venezuela_now()
        cutoff = (now - EXPRESS_EXPIRED_WINDOW).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        result = (
            sb.table("subscriptions")
            .select("*, users(telegram_id, name), platforms(name, slug, icon_emoji), profiles(id, profile_name, pin, status, account_id, accounts(email, password))")
            .eq("plan_type", "express")
            .or_(f"status.eq.active,and(status.eq.expired,end_date.gte.{cutoff})")
            .lte("end_date", now.isoformat())
            .execute()
        )
        return express_release_candidates(result.data or [], now)
    except Exception as e:
        logger.error(f"Error in get_expired_express_subscriptions: {e}")
        return []


async def check_payment_reference_exists(reference: str) -> bool:
    """Check if a payment reference has already been used (anti-duplicate)."""
    try:
        sb = get_supabase()
        result = (
            sb.table("subscriptions")
            .select("id")
            .eq("payment_reference", reference)
            .execute()
        )
        return len(result.data or []) > 0
    except Exception as e:
        logger.error(f"Error in check_payment_reference_exists: {e}")
        return False


async def mark_reminder_sent(sub_id: str) -> bool:
    """Mark that 3-day reminder has been sent."""
    try:
        sb = get_supabase()
        sb.table("subscriptions").update({"reminder_sent": True}).eq("id", sub_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error in mark_reminder_sent: {e}")
        return False


async def mark_expiry_notified(sub_id: str) -> bool:
    """Mark that expiry notification has been sent."""
    try:
        sb = get_supabase()
        sb.table("subscriptions").update({"expiry_notified": True}).eq("id", sub_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error in mark_expiry_notified: {e}")
        return False


async def get_subscriptions_for_debt_reminders() -> list[dict]:
    """Mensuales en mora (`active` o `expired` heredado) con 1 a 6 días de mora (días calendario de
    Caracas) a los que todavía NO se les envió el recordatorio de ese día. Cada fila trae `days_overdue`.
    Después del día 6 no hay más recordatorios (opción B: sin corte automático)."""
    try:
        from services.checkout_service import days_overdue
        sb = get_supabase()
        now = venezuela_now()
        result = (
            sb.table("subscriptions")
            .select("*, users(telegram_id, name, phone, notes), platforms(name, slug, icon_emoji), profiles(id, profile_name)")
            .in_("status", ["active", "expired"])
            .eq("plan_type", "monthly")
            .lt("end_date", now.isoformat())
            .gte("end_date", (now - timedelta(days=8)).isoformat())
            .execute()
        )
        out = []
        for row in (result.data or []):
            n = days_overdue(row, now)
            if 1 <= n <= 6 and (row.get("debt_reminder_count") or 0) < n:
                row["days_overdue"] = n
                out.append(row)
        return out
    except Exception as e:
        logger.error(f"Error in get_subscriptions_for_debt_reminders: {e}")
        return []


async def claim_debt_reminder(sub_id: str, day: int) -> bool:
    """Reclama de forma atómica el recordatorio del día `day`: solo uno de dos procesos simultáneos lo
    consigue (UPDATE condicional `debt_reminder_count < day`). True si lo reclamó."""
    try:
        sb = get_supabase()
        res = (
            sb.table("subscriptions")
            .update({"debt_reminder_count": day})
            .eq("id", sub_id)
            .lt("debt_reminder_count", day)
            .execute()
        )
        return bool(res.data)
    except Exception as e:
        logger.error(f"Error in claim_debt_reminder: {e}")
        return False


async def release_debt_reminder(sub_id: str, day: int) -> None:
    """Devuelve el reclamo si el envío falló (para reintentar en la siguiente pasada)."""
    try:
        get_supabase().table("subscriptions").update({"debt_reminder_count": day - 1}).eq("id", sub_id).eq("debt_reminder_count", day).execute()
    except Exception as e:
        logger.error(f"Error in release_debt_reminder: {e}")


async def claim_flag(sub_id: str, field: str) -> bool:
    """Reclama de forma atómica `reminder_sent` o `expiry_notified` (UPDATE condicional a que siga en
    False). True si lo reclamó: así dos pasadas simultáneas no envían el mismo aviso dos veces."""
    if field not in ("reminder_sent", "expiry_notified"):
        raise ValueError("campo no permitido")
    try:
        sb = get_supabase()
        res = sb.table("subscriptions").update({field: True}).eq("id", sub_id).eq(field, False).execute()
        return bool(res.data)
    except Exception as e:
        logger.error(f"Error in claim_flag({field}): {e}")
        return False


async def release_flag(sub_id: str, field: str) -> None:
    """Devuelve el reclamo si el envío falló."""
    if field not in ("reminder_sent", "expiry_notified"):
        raise ValueError("campo no permitido")
    try:
        get_supabase().table("subscriptions").update({field: False}).eq("id", sub_id).execute()
    except Exception as e:
        logger.error(f"Error in release_flag({field}): {e}")


async def get_subscription_by_id(sub_id: str) -> Optional[dict]:
    """Get subscription by UUID with related data."""
    try:
        sb = get_supabase()
        result = (
            sb.table("subscriptions")
            .select("*, users(telegram_id, name), platforms(name, slug, icon_emoji), profiles(profile_name, pin)")
            .eq("id", sub_id)
            
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"Error in get_subscription_by_id: {e}")
        return None


async def get_active_subscriptions_by_user(user_id: str) -> list[dict]:
    """Get all active subscriptions for a user with platform and profile data."""
    try:
        sb = get_supabase()
        result = (
            sb.table("subscriptions")
            .select("*, platforms(name, slug, icon_emoji), profiles(id, profile_name, pin)")
            .eq("user_id", user_id)
            .eq("status", "active")
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"Error in get_active_subscriptions_by_user: {e}")
        return []


async def get_renewable_subscriptions_by_user(user_id: str) -> list[dict]:
    """Get active and expired subscriptions for renewal selection."""
    try:
        sb = get_supabase()
        result = (
            sb.table("subscriptions")
            .select("*, platforms(name, slug, icon_emoji), profiles(profile_name, pin)")
            .eq("user_id", user_id)
            .in_("status", ["active", "expired"])
            .order("end_date", desc=True)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"Error in get_renewable_subscriptions_by_user: {e}")
        return []


async def get_expired_subscriptions(limit: int = 50) -> list[dict]:
    """
    Get subscriptions that are effectively expired:
      - status = 'expired' or 'expired_payment'
      - status = 'active' but end_date is already in the past
    Fetches broadly (excludes only pending_payment/cancelled) and filters in
    Python to avoid timezone comparison issues with Supabase timestamptz.
    """
    try:
        sb = get_supabase()
        now = venezuela_now()
        fields = "id, user_id, end_date, plan_type, price_usd, status, users(telegram_id, name, username), platforms(name, icon_emoji)"

        # Fetch all non-pending, non-cancelled subscriptions (includes expired_payment)
        result = (
            sb.table("subscriptions")
            .select(fields)
            .not_.in_("status", ["pending_payment", "cancelled"])
            .order("end_date", desc=True)
            .limit(300)
            .execute()
        )

        rows = result.data or []
        logger.info(f"get_expired_subscriptions raw rows: {len(rows)}")

        expired = []
        for row in rows:
            status = row.get("status")
            # Definitively expired statuses
            if status in ("expired", "expired_payment"):
                expired.append(row)
                continue
            # 'active' but end_date already past → also expired
            if status == "active":
                end_raw = row.get("end_date")
                if not end_raw:
                    continue
                try:
                    end_dt = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
                    if end_dt.tzinfo is None:
                        import pytz
                        end_dt = pytz.utc.localize(end_dt)
                    if end_dt < now:
                        expired.append(row)
                except Exception:
                    pass

        logger.info(f"get_expired_subscriptions filtered expired: {len(expired)}")

        # Fallback: if users FK join returned null, fetch user data by user_id
        missing_user_ids = [
            r["user_id"] for r in expired
            if not r.get("users") and r.get("user_id")
        ]
        if missing_user_ids:
            try:
                u_res = sb.table("users").select(
                    "id, telegram_id, name, username"
                ).in_("id", missing_user_ids).execute()
                user_map = {u["id"]: u for u in (u_res.data or [])}
                for r in expired:
                    if not r.get("users") and r.get("user_id"):
                        r["users"] = user_map.get(r["user_id"])
            except Exception as ue:
                logger.warning(f"get_expired_subscriptions user fallback error: {ue}")

        expired.sort(key=lambda x: x.get("end_date") or "", reverse=True)
        return expired[:limit]
    except Exception as e:
        logger.error(f"Error in get_expired_subscriptions: {e}")
        return []


async def cancel_expired_pending_subscriptions() -> int:
    """Cancel pending subscriptions older than 45 minutes. Returns count of cancelled."""
    try:
        sb = get_supabase()
        cutoff = venezuela_now() - timedelta(hours=4)
        result = (
            sb.table("subscriptions")
            .select("id")
            .eq("status", "pending_payment")
            .lte("created_at", cutoff.isoformat())
            .execute()
        )
        ids = [row["id"] for row in (result.data or [])]
        if ids:
            for sub_id in ids:
                sb.table("subscriptions").update({"status": "expired_payment"}).eq("id", sub_id).execute()
        return len(ids)
    except Exception as e:
        logger.error(f"Error in cancel_expired_pending_subscriptions: {e}")
        return 0


async def void_subscription(sub_id: str, actor: str, reason: str) -> dict:
    """Anula una suscripción con motivo (O02 Lote 3): estado `voided`, motivo y hora, y libera su
    perfil con la regla del corte (PIN nuevo) **solo si** ningún otra suscripción activa lo usa.
    Nunca borra la fila (el historial de pagos depende de ella). No se usa aún en producción: el
    Lote 5 la conecta a la pantalla de anomalías del admin, que registra la acción en `admin_log`.

    Devuelve {"voided": bool, "already": bool, "release": dict|None}; `release` es el resultado de
    `release_profile_for_cut` (con el PIN anterior y el nuevo para avisar al admin). Lanza
    `ValueError` si falta actor o motivo.
    """
    if not actor or not str(actor).strip():
        raise ValueError("Falta quién anula la suscripción")
    if not reason or not str(reason).strip():
        raise ValueError("Anular una suscripción exige un motivo")
    try:
        sb = get_supabase()
        res = sb.table("subscriptions").select("id, status, profile_id").eq("id", sub_id).limit(1).execute()
        if not res.data:
            return {"voided": False, "already": False, "release": None, "error": "not_found"}
        sub = res.data[0]
        if sub.get("status") == "voided":
            return {"voided": True, "already": True, "release": None}

        upd = (
            sb.table("subscriptions")
            .update({
                "status": "voided",
                "void_reason": f"[{str(actor).strip()}] {str(reason).strip()}",
                "voided_at": venezuela_now().isoformat(),
            })
            .eq("id", sub_id)
            .eq("status", sub["status"])
            .execute()
        )
        if not upd.data:
            # Cambió de estado entre la lectura y la escritura: no se toca el perfil.
            return {"voided": False, "already": False, "release": None, "error": "state_changed"}

        release = None
        if sub.get("profile_id"):
            from database.profiles import release_profile_for_cut
            release = await release_profile_for_cut(str(sub["profile_id"]), exclude_sub_id=sub_id)
        return {"voided": True, "already": False, "release": release}
    except Exception as e:
        logger.error(f"Error in void_subscription: {e}")
        return {"voided": False, "already": False, "release": None, "error": "db_error"}


async def get_overdue_subscriptions(min_days: int = 7, limit: int = 2000) -> list[dict]:
    """Mensuales en mora (`active` o `expired` heredado) con `min_days` días o más, con cliente y plataforma.
    Alimenta el resumen diario de mora al admin (O02 Lote 6)."""
    try:
        sb = get_supabase()
        cutoff = (venezuela_now() - timedelta(days=min_days)).isoformat()
        result = (
            sb.table("subscriptions")
            .select("id, user_id, profile_id, status, plan_type, price_usd, end_date, users(name, username, telegram_id), platforms(name, icon_emoji)")
            .in_("status", ["active", "expired"])
            .eq("plan_type", "monthly")
            .lt("end_date", cutoff)
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"Error in get_overdue_subscriptions: {e}")
        return []
