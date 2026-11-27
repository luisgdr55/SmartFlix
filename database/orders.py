"""Acceso a `checkout_orders` y a la aprobación atómica (O02 Lote 3).

Capa fina sobre Supabase: toda la lógica de negocio vive en `services/checkout_service.py` (puro) y
en la función SQL `approve_checkout_order` (docs/migraciones/o02-lote2-checkout.sql). Requiere que la
migración del Lote 2 esté ejecutada; mientras el flag `CHECKOUT_V2_ENABLED` esté apagado, nada en
producción llama a este módulo.

Convención del repo: las lecturas devuelven None / [] ante un error de base (y lo registran); la
aprobación NO oculta errores: lanza `ApprovalError` con un mensaje para el admin.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from database import get_supabase
from services.checkout_service import (
    ApprovalError,
    CheckoutError,
    InvalidTransition,
    check_order_transition,
    order_expires_at,
    parse_approval_error,
    validate_manifest,
)
from utils.dates import now_caracas, to_db

logger = logging.getLogger(__name__)

TABLE = "checkout_orders"
RPC_APPROVE = "approve_checkout_order"
# Estados a los que `decide_order` puede llevar una orden. Aprobar usa `approve_order`;
# anular una aprobada (con asiento contrario) llega en el Lote 5.
DECIDE_TARGETS = ("rejected", "cancelled", "expired")


async def create_order(
    user_id: str,
    manifest: dict,
    *,
    payment_reference: Optional[str] = None,
    receipt_text: Optional[str] = None,
    ocr_flags: Optional[dict] = None,
    now: Optional[datetime] = None,
) -> Optional[dict]:
    """Crea una orden `pending` con su manifiesto (se valida antes: `CheckoutError` si no cuadra).
    Vence a las 24 h. Devuelve la fila creada o None si falló la base."""
    validate_manifest(manifest)
    created = now or now_caracas()
    try:
        sb = get_supabase()
        res = sb.table(TABLE).insert({
            "user_id": user_id,
            "status": "pending",
            "manifest": manifest,
            "total_usd": manifest["total_usd"],
            "total_bs": manifest["total_bs"],
            "rate_used": manifest["rate_used"],
            "payment_reference": payment_reference,
            "receipt_text": receipt_text,
            "ocr_flags": ocr_flags,
            "expires_at": to_db(order_expires_at(created)),
        }).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"Error in create_order: {e}")
        return None


async def get_order(order_id: str) -> Optional[dict]:
    try:
        sb = get_supabase()
        res = (
            sb.table(TABLE)
            .select("*, users(telegram_id, name, username)")
            .eq("id", order_id)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"Error in get_order: {e}")
        return None


async def get_pending_order_for_user(user_id: str) -> Optional[dict]:
    """La orden pendiente más reciente del cliente (el comprobante se asocia a ella)."""
    try:
        sb = get_supabase()
        res = (
            sb.table(TABLE)
            .select("*")
            .eq("user_id", user_id)
            .eq("status", "pending")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"Error in get_pending_order_for_user: {e}")
        return None


async def list_pending_orders(limit: int = 50) -> list[dict]:
    try:
        sb = get_supabase()
        res = (
            sb.table(TABLE)
            .select("*, users(telegram_id, name, username)")
            .eq("status", "pending")
            .order("created_at", desc=False)
            .limit(limit)
            .execute()
        )
        return res.data or []
    except Exception as e:
        logger.error(f"Error in list_pending_orders: {e}")
        return []


async def attach_receipt(
    order_id: str,
    payment_reference: Optional[str],
    receipt_text: Optional[str],
    ocr_flags: Optional[dict] = None,
) -> bool:
    """Guarda comprobante, referencia y señales OCR en una orden **pendiente** (solo marcan; aprueba el admin).
    False si la orden ya no está pendiente o falló la base."""
    try:
        sb = get_supabase()
        res = (
            sb.table(TABLE)
            .update({
                "payment_reference": payment_reference,
                "receipt_text": receipt_text,
                "ocr_flags": ocr_flags,
                "updated_at": to_db(now_caracas()),
            })
            .eq("id", order_id)
            .eq("status", "pending")
            .execute()
        )
        return bool(res.data)
    except Exception as e:
        logger.error(f"Error in attach_receipt: {e}")
        return False


async def decide_order(order_id: str, target: str, actor: str, reason: Optional[str] = None) -> bool:
    """Rechaza, cancela o vence una orden pendiente (reglas de `check_order_transition`).

    - `CheckoutError` (`InvalidTransition`) si la transición no se permite o falta actor/motivo
      (por ejemplo, rechazar sin motivo).
    - False si otro proceso la decidió primero (el UPDATE es condicional al estado leído) o si
      falló la base; True si se aplicó.
    """
    if target not in DECIDE_TARGETS:
        raise InvalidTransition(f"Estado de destino no válido para esta acción: {target!r}")
    order = await get_order(order_id)
    if not order:
        raise CheckoutError("La orden no existe")
    info = check_order_transition(order["status"], target, actor, reason)
    try:
        sb = get_supabase()
        res = (
            sb.table(TABLE)
            .update({
                "status": target,
                "decided_by": info["actor"],
                "decided_at": to_db(now_caracas()),
                "decision_reason": info["reason"],
                "version": int(order.get("version") or 1) + 1,
                "updated_at": to_db(now_caracas()),
            })
            .eq("id", order_id)
            .eq("status", order["status"])
            .execute()
        )
        return bool(res.data)
    except Exception as e:
        logger.error(f"Error in decide_order: {e}")
        return False


async def expire_stale_orders(now: Optional[datetime] = None) -> int:
    """Pasa a `expired` las órdenes pendientes cuyo `expires_at` ya venció (una sola sentencia
    condicional). Devuelve cuántas. Aún no está programado: el job llega con el Lote 6."""
    try:
        sb = get_supabase()
        stamp = to_db(now or now_caracas())
        res = (
            sb.table(TABLE)
            .update({"status": "expired", "decided_by": "sistema", "decided_at": stamp, "updated_at": stamp})
            .eq("status", "pending")
            .lte("expires_at", stamp)
            .execute()
        )
        return len(res.data or [])
    except Exception as e:
        logger.error(f"Error in expire_stale_orders: {e}")
        return 0


def _rpc(name: str, params: dict, order_id: str) -> dict:
    """Llama a una función SQL de órdenes y traduce sus errores a `ApprovalError`."""
    try:
        sb = get_supabase()
        return sb.rpc(name, params).execute().data
    except ApprovalError:
        raise
    except Exception as e:
        # postgrest.exceptions.APIError trae `message` (código de la función) y `details` (JSON).
        message = getattr(e, "message", None)
        if message is not None and getattr(e, "code", None) is not None:
            raise parse_approval_error(message, getattr(e, "details", None)) from e
        logger.error(f"{name}: error de conexión o inesperado para {order_id}: {e}")
        raise ApprovalError("unavailable", raw=str(e)[:200]) from e


async def approve_order(
    order_id: str,
    actor: str,
    reason: Optional[str] = None,
    payment_reference: Optional[str] = None,
) -> dict:
    """Llama a la función SQL atómica. Úsala a través de `services.checkout_service.approve`.

    Errores de la función (APIError con MESSAGE = código) → `ApprovalError` traducido. Un fallo de
    conexión se informa como `unavailable`: la orden pudo o no haberse aprobado; repetir es seguro
    porque la función es idempotente.
    """
    return _rpc(RPC_APPROVE, {
        "p_order_id": order_id, "p_actor": actor, "p_reason": reason, "p_payment_reference": payment_reference,
    }, order_id)


async def approve_order_partial(order_id: str, actor: str, keep: list[int], reason: Optional[str] = None,
                                payment_reference: Optional[str] = None) -> dict:
    """Aprobación parcial (divide la orden). Úsala a través de `checkout_service.approve_partial`."""
    return _rpc("split_and_approve_checkout_order", {
        "p_order_id": order_id, "p_actor": actor, "p_keep": keep, "p_reason": reason,
        "p_payment_reference": payment_reference,
    }, order_id)


async def void_order(order_id: str, actor: str, reason: str) -> dict:
    """Anula una orden aprobada con asiento contrario. Úsala a través de `checkout_service.void_order`."""
    return _rpc("void_checkout_order", {"p_order_id": order_id, "p_actor": actor, "p_reason": reason}, order_id)


async def payment_reference_used(reference: str, exclude_order_id: Optional[str] = None) -> bool:
    """¿Esta referencia ya figura en un pago aprobado (órdenes o suscripciones anteriores)?
    Alimenta la señal «referencia repetida» del comprobante (solo marca; decide el admin)."""
    ref = (reference or "").strip()
    if not ref:
        return False
    try:
        sb = get_supabase()
        q = sb.table(TABLE).select("id").eq("payment_reference", ref).eq("status", "approved")
        if exclude_order_id:
            q = q.neq("id", exclude_order_id)
        if q.limit(1).execute().data:
            return True
        return bool(sb.table("subscriptions").select("id").eq("payment_reference", ref).limit(1).execute().data)
    except Exception as e:
        logger.error(f"Error in payment_reference_used: {e}")
        return False


async def list_orders(statuses: Optional[list[str]] = None, limit: int = 30) -> list[dict]:
    """Órdenes recientes (con el cliente), por defecto todas las del estado dado; más nuevas primero
    salvo las pendientes, que van de la más antigua a la más nueva (las que llevan más tiempo esperando)."""
    try:
        sb = get_supabase()
        q = sb.table(TABLE).select("*, users(telegram_id, name, username)")
        if statuses:
            q = q.in_("status", statuses)
        oldest_first = statuses == ["pending"]
        return q.order("created_at", desc=not oldest_first).limit(limit).execute().data or []
    except Exception as e:
        logger.error(f"Error in list_orders: {e}")
        return []


async def get_subscriptions_with_access(sub_ids: list[str]) -> dict[str, dict]:
    """Suscripciones con plataforma, perfil y cuenta (para entregar el acceso tras aprobar)."""
    ids = [str(i) for i in sub_ids if i]
    if not ids:
        return {}
    try:
        sb = get_supabase()
        res = (
            sb.table("subscriptions")
            .select("*, platforms(name, slug, icon_emoji), profiles(profile_name, pin, accounts(email, password))")
            .in_("id", ids)
            .execute()
        )
        return {str(r["id"]): r for r in (res.data or [])}
    except Exception as e:
        logger.error(f"Error in get_subscriptions_with_access: {e}")
        return {}


async def count_subscription_periods(sub_id: str) -> int:
    """Cuántos periodos pagados tiene una suscripción (el borrado solo se permite con 0)."""
    try:
        sb = get_supabase()
        res = sb.table("subscription_periods").select("id", count="exact").eq("subscription_id", sub_id).limit(1).execute()
        return int(res.count or 0)
    except Exception as e:
        if _table_missing(e):
            return 0   # migración del Lote 2 aún sin ejecutar: no hay historial que proteger
        # Ante la duda se trata como «con historial»: nunca se borra por un error de lectura.
        logger.error(f"Error in count_subscription_periods: {e}")
        return 1


def _table_missing(e: Exception) -> bool:
    text = f"{getattr(e, 'message', '')} {e}".lower()
    return "subscription_periods" in text and any(k in text for k in ("does not exist", "schema cache", "could not find"))


async def count_user_periods(user_id: str) -> int:
    """Periodos pagados de todas las suscripciones del cliente (borrar al cliente solo si es 0)."""
    try:
        sb = get_supabase()
        ids = [r["id"] for r in (sb.table("subscriptions").select("id").eq("user_id", user_id).execute().data or [])]
        if not ids:
            return 0
        res = sb.table("subscription_periods").select("id", count="exact").in_("subscription_id", ids).limit(1).execute()
        return int(res.count or 0)
    except Exception as e:
        if _table_missing(e):
            return 0
        logger.error(f"Error in count_user_periods: {e}")
        return 1


async def pending_for_user(user_id: str) -> dict:
    """Órdenes (y pedidos del flujo anterior) pendientes de un cliente, para avisar al admin antes de
    un corte manual (O02 Lote 6). {"orders": [ids cortos], "legacy": [ids cortos]}. Un error de lectura
    se registra y devuelve vacío (no bloquea el corte; el aviso es una ayuda, no una guarda dura)."""
    out: dict = {"orders": [], "legacy": []}
    sb = get_supabase()
    try:
        out["orders"] = [str(r["id"])[:8].upper() for r in (
            sb.table(TABLE).select("id").eq("user_id", user_id).eq("status", "pending").execute().data or [])]
    except Exception as e:
        if not _table_missing_orders(e):
            logger.error(f"Error in pending_for_user (órdenes): {e}")
    try:
        out["legacy"] = [str(r["id"])[:8].upper() for r in (
            sb.table("subscriptions").select("id").eq("user_id", user_id).eq("status", "pending_payment").execute().data or [])]
    except Exception as e:
        logger.error(f"Error in pending_for_user (pedidos): {e}")
    return out


def _table_missing_orders(e: Exception) -> bool:
    text = f"{getattr(e, 'message', '')} {e}".lower()
    return "checkout_orders" in text and any(k in text for k in ("does not exist", "schema cache", "could not find"))
