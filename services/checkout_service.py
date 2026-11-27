"""Servicio de checkout de la orden O02 (Lote 1): periodos, mora, manifiesto, totales y
transiciones de la orden de pago.

Lógica pura: sin base de datos, Redis ni Telegram, y agnóstica al almacenamiento de sesión
(el bot guarda en Redis, `/renovar` en `context.user_data`, el motor de aprobación en la base).
Dinero siempre en `Decimal`; en el manifiesto (JSON) viaja como texto con 2 decimales.
Mientras el flag `CHECKOUT_V2_ENABLED` esté apagado, ningún flujo en producción lo usa todavía.

Decisiones del Orquestador (27/11/2026) que implementa:
- Total en Bs = total USD × tasa, UNA sola vez, `Decimal`, `ROUND_HALF_UP`, 2 decimales; los
  montos por ítem suman exactamente el total (decisión 5).
- Sin tasa válida no hay checkout: no existe tasa de respaldo (N-C).
- Cada ítem apunta a una `subscription_id` concreta; nunca se resuelve por usuario+plataforma.
- `expired` heredado se lee como mora (decisión 2); `is_overdue` es el predicado único.
- TTL de la orden: 24 h (decisión 4).
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Iterable, Optional

from utils.dates import (
    add_months,
    caracas_date,
    now_caracas,
    parse_db_datetime,
    whole_months_between,
)

logger = logging.getLogger(__name__)

MONEY = Decimal("0.01")
RATE_Q = Decimal("0.0001")
MANIFEST_VERSION = 1
MAX_PERIODS = 12
MAX_ITEMS = 20
ORDER_TTL = timedelta(hours=24)

ITEM_RENEWAL = "renovacion"
ITEM_NEW = "nueva"
ITEM_CHANGE = "cambio"
ITEM_TYPES = (ITEM_RENEWAL, ITEM_NEW, ITEM_CHANGE)

# Suscripción: `expired` es legado y se lee como mora (decisión 2).
OVERDUE_STATUSES = ("active", "expired")

# Orden de pago.
ORDER_PENDING, ORDER_APPROVED, ORDER_REJECTED = "pending", "approved", "rejected"
ORDER_EXPIRED, ORDER_CANCELLED, ORDER_VOIDED = "expired", "cancelled", "voided"
ORDER_STATUSES = (ORDER_PENDING, ORDER_APPROVED, ORDER_REJECTED, ORDER_EXPIRED, ORDER_CANCELLED, ORDER_VOIDED)
# Transiciones permitidas. `approved` es inmutable salvo anularla (`voided`, con motivo).
# `expired -> approved` es el comprobante tardío: el admin aprueba con motivo.
ORDER_TRANSITIONS = {
    ORDER_PENDING: {ORDER_APPROVED, ORDER_REJECTED, ORDER_EXPIRED, ORDER_CANCELLED},
    ORDER_EXPIRED: {ORDER_APPROVED},
    ORDER_APPROVED: {ORDER_VOIDED},
    ORDER_REJECTED: set(),
    ORDER_CANCELLED: set(),
    ORDER_VOIDED: set(),
}
# (origen, destino) que exigen motivo escrito.
REASON_REQUIRED = {
    (ORDER_PENDING, ORDER_REJECTED),
    (ORDER_EXPIRED, ORDER_APPROVED),
    (ORDER_APPROVED, ORDER_VOIDED),
}


class CheckoutError(ValueError):
    """Error de validación del checkout (mensaje apto para mostrar al admin)."""


class RateUnavailable(CheckoutError):
    """No hay una tasa válida: el checkout se bloquea, no hay tasa de respaldo."""


class InvalidTransition(CheckoutError):
    """Transición de estado de la orden no permitida."""


class AlreadyApplied(CheckoutError):
    """La orden ya estaba aprobada: la llamada repetida no debe producir efectos nuevos."""


# ── Dinero ──────────────────────────────────────────────────────────────────

def to_decimal(value, field: str = "monto") -> Decimal:
    """Convierte a `Decimal` finito. Los `float` pasan por `str` (sin ruido binario)."""
    if value is None or isinstance(value, bool):
        raise CheckoutError(f"{field} no válido")
    try:
        d = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise CheckoutError(f"{field} no válido: {value!r}")
    if not d.is_finite():
        raise CheckoutError(f"{field} no válido: {value!r}")
    return d


def money(value) -> Decimal:
    """Redondea a 2 decimales con ROUND_HALF_UP."""
    return to_decimal(value).quantize(MONEY, rounding=ROUND_HALF_UP)


def _positive_money(value, field: str) -> Decimal:
    d = money(to_decimal(value, field))
    if d <= 0:
        raise CheckoutError(f"{field} debe ser mayor que 0")
    return d


def normalize_rate(rate) -> Decimal:
    """Tasa Bs/USD con 4 decimales (la misma precisión que se guardará). Sin tasa válida,
    `RateUnavailable`: el checkout nuevo no usa tasa de respaldo (N-C)."""
    if isinstance(rate, dict):
        rate = rate.get("usd_binance")
    if rate is None or isinstance(rate, bool):
        raise RateUnavailable("No hay una tasa de cambio válida")
    try:
        d = to_decimal(rate, "tasa")
    except CheckoutError:
        raise RateUnavailable("No hay una tasa de cambio válida")
    d = d.quantize(RATE_Q, rounding=ROUND_HALF_UP)
    if d <= 0:
        raise RateUnavailable("No hay una tasa de cambio válida")
    return d


def usd_to_bs(total_usd, rate) -> Decimal:
    """Total en Bs: total USD × tasa, una sola vez, 2 decimales ROUND_HALF_UP."""
    return money(to_decimal(total_usd, "total USD") * normalize_rate(rate))


def allocate_bs(amounts_usd: Iterable, rate) -> tuple[Decimal, list[Decimal]]:
    """Reparte el total en Bs entre los ítems de modo que **sumen exactamente el total**.

    Cada ítem se redondea (ROUND_HALF_UP) y la diferencia (±0,01 por ítem como máximo) se carga
    al último. El total es el de `usd_to_bs(suma USD)`, no la suma de ítems redondeados.
    """
    usd = [money(a) for a in amounts_usd]
    if not usd:
        raise CheckoutError("No hay ítems que repartir")
    r = normalize_rate(rate)
    total = usd_to_bs(sum(usd, Decimal("0")), r)
    parts = [money(a * r) for a in usd]
    parts[-1] += total - sum(parts, Decimal("0"))
    return total, parts


def _fmt(d: Decimal) -> str:
    return f"{d.quantize(MONEY, rounding=ROUND_HALF_UP):.2f}"


# ── Mora y periodos ─────────────────────────────────────────────────────────

def _end_dt(sub: dict) -> datetime:
    raw = sub.get("end_date")
    if not raw:
        raise CheckoutError("La suscripción no tiene fecha de fin")
    return parse_db_datetime(raw)


def is_overdue(sub: dict, now: Optional[datetime] = None) -> bool:
    """Predicado único de mora (O02 Lote 6): estado `active` o `expired` (legado) con
    `end_date` anterior a ahora. La mora es un dato derivado, no un estado."""
    if sub.get("status") not in OVERDUE_STATUSES or not sub.get("end_date"):
        return False
    return _end_dt(sub) < (now or now_caracas())


def days_overdue(sub: dict, now: Optional[datetime] = None) -> int:
    """Días de mora contando días calendario de Caracas (0 si no está vencida)."""
    now = now or now_caracas()
    if not is_overdue(sub, now):
        return 0
    return max((caracas_date(now) - caracas_date(_end_dt(sub))).days, 0)


def overdue_periods(end_date, now: Optional[datetime] = None) -> int:
    """Meses de calendario completos transcurridos desde la cobertura actual hasta hoy."""
    today = caracas_date(now or now_caracas())
    return whole_months_between(caracas_date(end_date), today)


def periods_to_be_current(end_date, now: Optional[datetime] = None) -> int:
    """Periodos mínimos (≥1) para quedar al día: la nueva cobertura debe terminar después de hoy.

    Ej.: cubierto hasta 19/05 y hoy 19/06 → pagar 1 mes deja la cobertura en 19/06 (sigue
    debiendo el periodo en curso); pagar 2 la deja en 19/07 (al día).
    """
    base = caracas_date(end_date)
    today = caracas_date(now or now_caracas())
    for n in range(1, 121):
        if add_months(base, n) > today:
            return n
    raise CheckoutError("La mora es demasiado antigua para calcular periodos")


def renewal_coverage(end_date, periods: int, anchor_day: Optional[int] = None) -> tuple[date, date]:
    """Cobertura de una renovación: desde el `end_date` actual (siempre, incluso vencido) hasta
    `periods` meses de calendario después. Devuelve (desde, hasta) como fechas de Caracas."""
    if not isinstance(periods, int) or isinstance(periods, bool) or not 1 <= periods <= MAX_PERIODS:
        raise CheckoutError(f"Los periodos deben estar entre 1 y {MAX_PERIODS}")
    base = caracas_date(end_date)
    return base, add_months(base, periods, anchor_day)


# ── Ítems del manifiesto ────────────────────────────────────────────────────

def _require(value, field: str) -> str:
    if not value or not str(value).strip():
        raise CheckoutError(f"Falta {field}")
    return str(value)


def build_renewal_item(sub: dict, periods: int, unit_price_usd, now: Optional[datetime] = None) -> dict:
    """Ítem `renovacion` sobre UNA suscripción concreta (por su id; nunca por usuario+plataforma).

    Solo suscripciones `active` o `expired` (legado) y mensuales. La reactivación de una
    `cancelled` queda fuera de este lote hasta que el Orquestador decida su regla de fechas.
    """
    sub_id = _require(sub.get("id"), "el id de la suscripción")
    if sub.get("status") not in OVERDUE_STATUSES:
        raise CheckoutError(f"No se puede renovar una suscripción en estado «{sub.get('status')}»")
    if sub.get("plan_type", "monthly") != "monthly":
        raise CheckoutError("Solo se renuevan suscripciones mensuales")
    price = _positive_money(unit_price_usd, "El precio")
    desde, hasta = renewal_coverage(sub.get("end_date"), periods)
    return {
        "tipo": ITEM_RENEWAL,
        "subscription_id": sub_id,
        "platform_id": _require(sub.get("platform_id"), "la plataforma"),
        "plan_type": "monthly",
        "periodos": periods,
        "cobertura_desde": desde.isoformat(),
        "cobertura_hasta": hasta.isoformat(),
        "precio_unitario_usd": _fmt(price),
        "cargo_deuda_usd": "0.00",
        "subtotal_usd": _fmt(price * periods),
    }


def build_new_item(platform_id: str, plan_type: str, unit_price_usd, profile_type: Optional[str] = None) -> dict:
    """Ítem `nueva` (compra nueva, un periodo). El perfil se asigna al aprobar; la cobertura
    arranca en la aprobación (decisión N-F), por eso no lleva fechas."""
    if plan_type not in ("monthly", "express"):
        raise CheckoutError(f"Plan no válido: {plan_type!r}")
    price = _positive_money(unit_price_usd, "El precio")
    return {
        "tipo": ITEM_NEW,
        "subscription_id": None,
        "platform_id": _require(platform_id, "la plataforma"),
        "plan_type": plan_type,
        "profile_type": profile_type or plan_type,
        "periodos": 1,
        "precio_unitario_usd": _fmt(price),
        "cargo_deuda_usd": "0.00",
        "subtotal_usd": _fmt(price),
    }


def build_change_item(
    sub: dict,
    new_platform_id: str,
    plan_type: str,
    unit_price_usd,
    debt_usd,
    now: Optional[datetime] = None,
) -> dict:
    """Ítem `cambio` de plataforma: solo sobre una suscripción vencida (mora). Muta la misma
    `subscription_id`. `subtotal = cargo_deuda + precio` (la deuda de la plataforma que se
    abandona se salda antes de cambiar)."""
    sub_id = _require(sub.get("id"), "el id de la suscripción")
    if not is_overdue(sub, now):
        raise CheckoutError("El cambio de plataforma solo aplica a una suscripción vencida")
    if plan_type != "monthly":
        raise CheckoutError("El cambio de plataforma solo aplica a planes mensuales")
    origin = _require(sub.get("platform_id"), "la plataforma de origen")
    dest = _require(new_platform_id, "la plataforma destino")
    if dest == origin:
        raise CheckoutError("La plataforma destino debe ser distinta de la de origen")
    price = _positive_money(unit_price_usd, "El precio")
    debt = money(to_decimal(debt_usd, "La deuda"))
    if debt < 0:
        raise CheckoutError("La deuda no puede ser negativa")
    return {
        "tipo": ITEM_CHANGE,
        "subscription_id": sub_id,
        "platform_id": dest,
        "platform_origen_id": origin,
        "plan_type": plan_type,
        "periodos": 1,
        "precio_unitario_usd": _fmt(price),
        "cargo_deuda_usd": _fmt(debt),
        "subtotal_usd": _fmt(price + debt),
    }


# ── Manifiesto ──────────────────────────────────────────────────────────────

def build_manifest(items: list[dict], rate) -> dict:
    """Arma el manifiesto de una orden: valida, fija la tasa (4 decimales), calcula el total en
    USD y en Bs (una sola vez) y reparte el Bs por ítem para que sume el total."""
    if not items:
        raise CheckoutError("La orden no tiene ítems")
    if len(items) > MAX_ITEMS:
        raise CheckoutError(f"Máximo {MAX_ITEMS} ítems por orden")
    r = normalize_rate(rate)
    subs = [i.get("subscription_id") for i in items if i.get("subscription_id")]
    if len(subs) != len(set(subs)):
        raise CheckoutError("Una misma suscripción no puede aparecer dos veces en la orden")
    out = []
    for item in items:
        if item.get("tipo") not in ITEM_TYPES:
            raise CheckoutError(f"Tipo de ítem no válido: {item.get('tipo')!r}")
        if item.get("tipo") in (ITEM_RENEWAL, ITEM_CHANGE) and not item.get("subscription_id"):
            raise CheckoutError("Este ítem debe apuntar a una suscripción concreta")
        out.append(dict(item))
    subtotals = [money(i["subtotal_usd"]) for i in out]
    total_bs, parts = allocate_bs(subtotals, r)
    for item, part in zip(out, parts):
        item["amount_bs"] = _fmt(part)
    return {
        "version": MANIFEST_VERSION,
        "items": out,
        "total_usd": _fmt(sum(subtotals, Decimal("0"))),
        "total_bs": _fmt(total_bs),
        "rate_used": f"{r:.4f}",
    }


def validate_manifest(manifest: dict) -> None:
    """Comprueba la coherencia de un manifiesto (por ejemplo, el leído de la base antes de
    aprobar). Lanza `CheckoutError` si algo no cuadra; no devuelve nada si todo está bien."""
    if not isinstance(manifest, dict) or manifest.get("version") != MANIFEST_VERSION:
        raise CheckoutError("Versión de manifiesto no soportada")
    items = manifest.get("items") or []
    if not items:
        raise CheckoutError("La orden no tiene ítems")
    r = normalize_rate(manifest.get("rate_used"))
    subs = [i.get("subscription_id") for i in items if i.get("subscription_id")]
    if len(subs) != len(set(subs)):
        raise CheckoutError("Una misma suscripción aparece dos veces en la orden")
    for item in items:
        if item.get("tipo") not in ITEM_TYPES:
            raise CheckoutError(f"Tipo de ítem no válido: {item.get('tipo')!r}")
        unit = _positive_money(item.get("precio_unitario_usd"), "El precio")
        expected = unit * int(item.get("periodos", 1)) + money(item.get("cargo_deuda_usd", "0"))
        if money(item.get("subtotal_usd")) != expected:
            raise CheckoutError("El subtotal de un ítem no coincide con precio × periodos + deuda")
    total_usd = sum((money(i["subtotal_usd"]) for i in items), Decimal("0"))
    if money(manifest.get("total_usd")) != total_usd:
        raise CheckoutError("El total en USD no coincide con la suma de los ítems")
    if money(manifest.get("total_bs")) != usd_to_bs(total_usd, r):
        raise CheckoutError("El total en Bs no coincide con total USD × tasa")
    if sum((money(i.get("amount_bs")) for i in items), Decimal("0")) != money(manifest.get("total_bs")):
        raise CheckoutError("Los montos en Bs de los ítems no suman el total")


# ── Orden de pago: estados y vencimiento ────────────────────────────────────

def order_expires_at(created_at: datetime, ttl: timedelta = ORDER_TTL) -> datetime:
    """Vencimiento de una orden sin pagar (24 h por defecto)."""
    return created_at + ttl


def is_order_expired(expires_at, now: Optional[datetime] = None) -> bool:
    return parse_db_datetime(expires_at) <= (now or now_caracas())


def check_order_transition(current: str, target: str, actor: str, reason: Optional[str] = None) -> dict:
    """Valida una transición de la orden y devuelve lo que se debe registrar (actor y motivo).

    - Aprobar solo desde `pending` (o `expired` con motivo: comprobante tardío).
    - Una orden aprobada es inmutable; solo se puede anular (`voided`) con motivo.
    - Rechazar exige motivo. Toda transición registra actor.
    - Aprobar una orden ya aprobada lanza `AlreadyApplied` (sin efectos; devolver lo ya aplicado).
    """
    if current not in ORDER_STATUSES or target not in ORDER_STATUSES:
        raise InvalidTransition(f"Estado de orden desconocido: {current!r} → {target!r}")
    if current == ORDER_APPROVED and target == ORDER_APPROVED:
        raise AlreadyApplied("La orden ya estaba aprobada")
    if target not in ORDER_TRANSITIONS[current]:
        raise InvalidTransition(f"No se puede pasar una orden de «{current}» a «{target}»")
    if not actor or not str(actor).strip():
        raise InvalidTransition("Toda transición debe registrar quién la hizo")
    reason = (reason or "").strip()
    if (current, target) in REASON_REQUIRED and not reason:
        raise InvalidTransition("Esta acción exige un motivo")
    return {"from": current, "to": target, "actor": str(actor).strip(), "reason": reason or None}


# ── Motor de aprobación (O02 Lote 3) ────────────────────────────────────────
# La aprobación real ocurre en la función SQL `approve_checkout_order` (una transacción: todo o
# nada, idempotente). Aquí solo se valida la entrada, se llama al repositorio, se traducen los
# errores a mensajes para el admin y se resume qué debe atender. Telegram, panel y /renovar usan
# este único punto de entrada (el cableado a los handlers llega con los Lotes 4 y 5).
#
# No se usa `services/locks.py` en la aprobación (decisión de diseño): el candado de fila de la
# función SQL es la garantía real, y un candado Redis que falla cerrado dejaría al admin sin poder
# aprobar pagos cuando Redis cae.

# Mensajes (todos terminan indicando qué hacer; la aprobación es atómica, así que un error no deja nada a medias).
APPROVAL_MESSAGES = {
    "order_not_found": "La orden no existe. Ábrela de nuevo desde la lista de pendientes.",
    "actor_required": "No se pudo identificar quién aprueba. Vuelve a iniciar sesión e inténtalo de nuevo.",
    "reason_required": "Escribe el motivo para continuar (por ejemplo, «comprobante tardío verificado» si la orden caducó).",
    "invalid_state": "La orden ya no está pendiente, no se cambió nada. Revisa su estado en la lista de pendientes.",
    "invalid_manifest": "Los importes de la orden no cuadran, no se aprobó nada. Revisa la orden o pide al cliente que la rehaga.",
    "subscription_not_found": "Una suscripción de la orden ya no existe, no se aprobó nada. Pide al cliente que rehaga el pedido.",
    "subscription_not_owned": "Una suscripción de la orden no pertenece a este cliente, no se aprobó nada. Revísalo antes de reintentar.",
    "subscription_not_renewable": "Una suscripción de la orden ya no se puede renovar (por ejemplo, está cancelada), no se aprobó nada. Revísala en la ficha del cliente.",
    "subscription_not_overdue": "El cambio de plataforma solo aplica a una suscripción vencida; esta no lo está. No se aprobó nada.",
    "profile_unavailable": "No hay un perfil libre para uno de los servicios. La orden sigue pendiente: agrega un perfil y vuelve a aprobar.",
    "duplicate_reference": "Esa referencia de pago ya se aprobó en otra orden, no se aprobó nada. Verifica el comprobante.",
    "invalid_selection": "La selección de ítems no es válida: elige al menos uno y no todos (para aprobar todo usa «Aprobar»). No se cambió nada.",
    "manual_reversal_required": "Esta orden incluye un cambio de plataforma, que no se puede revertir automáticamente (el perfil ya se movió). No se cambió nada: anula la suscripción desde «Anomalías» o corrige el cliente a mano.",
    "subscription_changed": "Una suscripción de esta orden se renovó o cambió después, así que ya no se puede revertir sin dañar lo posterior. No se cambió nada: anula primero la orden más reciente.",
    "unavailable": "No se pudo confirmar el resultado (falla de conexión). Vuelve a pulsar Aprobar: es seguro, no se duplica nada.",
    "unknown": "No se pudo aprobar por un error técnico. La orden no cambió; inténtalo de nuevo o revisa los registros.",
}


class ApprovalError(CheckoutError):
    """Fallo al aprobar una orden. `code` es el código estable; `user_message` es apto para el admin."""

    def __init__(self, code: str, detail: Optional[dict] = None, raw: Optional[str] = None):
        self.code = code
        self.detail = detail or {}
        self.raw = raw
        self.user_message = APPROVAL_MESSAGES.get(code, APPROVAL_MESSAGES["unknown"])
        super().__init__(self.user_message)


def parse_approval_error(message: Optional[str], details: Optional[str] = None) -> ApprovalError:
    """Traduce el error de `approve_checkout_order` (MESSAGE y DETAIL de la excepción SQL)."""
    code = (message or "").strip()
    detail: dict = {}
    if details:
        try:
            parsed = json.loads(details)
            detail = parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            detail = {}
    if code not in APPROVAL_MESSAGES or code in ("unavailable", "unknown"):
        logger.error(f"approve_checkout_order devolvió un error no reconocido: {message!r} / {details!r}")
        return ApprovalError("unknown", detail, raw=message)
    return ApprovalError(code, detail, raw=message)


def _clean_order_id(order_id) -> str:
    try:
        return str(uuid.UUID(str(order_id)))
    except (ValueError, AttributeError, TypeError):
        raise CheckoutError("El identificador de la orden no es válido")


async def approve(
    order_id,
    actor: str,
    reason: Optional[str] = None,
    payment_reference: Optional[str] = None,
    *,
    repo=None,
) -> dict:
    """Aprueba una orden con la función SQL atómica. Devuelve el resultado aplicado:
    {"ok", "already_applied", "order_id", "approved_by", "items": [...]}. Una segunda llamada
    sobre una orden ya aprobada devuelve `already_applied: True` sin efectos nuevos.

    Lanza `ApprovalError` (con `code` y `user_message`) si algo falla; en ese caso no se cambió nada.
    `payment_reference` debe ser una referencia real o None (nunca una marca fija como
    «PANEL-MANUAL»: el índice único de referencias aprobadas rechazaría la segunda orden).
    """
    oid = _clean_order_id(order_id)
    if not actor or not str(actor).strip():
        raise ApprovalError("actor_required")
    if repo is None:
        from database import orders as repo  # import tardío: este módulo sigue siendo puro
    result = await repo.approve_order(oid, str(actor).strip(), reason, payment_reference)
    if not isinstance(result, dict) or not (result.get("ok") or result.get("already_applied")):
        raise ApprovalError("unknown", raw=str(result)[:200])
    return result


def approval_notices(result: dict) -> list[dict]:
    """Lo que el admin debe atender tras aprobar (para armar los avisos en los Lotes 4 y 5):
    - `profile_changed`: se dio otro perfil a una renovación (el cliente recibe credenciales nuevas).
    - `pin_rotated`: se liberó el perfil de la plataforma de origen en un cambio; el admin debe
      entrar con el PIN anterior y ponerlo al nuevo en la plataforma.
    - `profile_not_released`: no se liberó el perfil de origen (otra suscripción activa lo usa).
    """
    notices: list[dict] = []
    for item in (result or {}).get("items") or []:
        idx = item.get("index")
        if item.get("profile_changed") and item.get("tipo") == "renovacion":
            notices.append({
                "kind": "profile_changed", "index": idx, "subscription_id": item.get("subscription_id"),
                "profile_id": item.get("profile_id"), "previous_profile_id": item.get("previous_profile_id"),
            })
        rel = item.get("released_profile")
        if isinstance(rel, dict):
            if rel.get("released"):
                notices.append({
                    "kind": "pin_rotated", "index": idx, "profile_name": rel.get("profile_name"),
                    "account_email": rel.get("account_email"), "old_pin": rel.get("old_pin"),
                    "new_pin": rel.get("new_pin"),
                })
            elif rel.get("reason") == "otra_suscripcion_activa":
                notices.append({
                    "kind": "profile_not_released", "index": idx, "profile_name": rel.get("profile_name"),
                    "previous_profile_id": item.get("previous_profile_id"),
                })
    return notices


async def approve_partial(order_id, actor: str, keep: list[int], reason: Optional[str] = None,
                          payment_reference: Optional[str] = None, *, repo=None) -> dict:
    """Aprobación PARCIAL: aprueba solo los ítems de `keep` (índices del manifiesto, desde 0) y deja el
    resto en una orden nueva pendiente (`remainder_order_id` en el resultado). Todo o nada (función SQL
    `split_and_approve_checkout_order`). Lanza `ApprovalError` (`invalid_selection`, etc.)."""
    oid = _clean_order_id(order_id)
    if not actor or not str(actor).strip():
        raise ApprovalError("actor_required")
    if (not isinstance(keep, (list, tuple)) or not keep or len(set(keep)) != len(keep)
            or any(not isinstance(k, int) or isinstance(k, bool) or k < 0 for k in keep)):
        raise ApprovalError("invalid_selection")
    if repo is None:
        from database import orders as repo
    result = await repo.approve_order_partial(oid, str(actor).strip(), sorted(keep), reason, payment_reference)
    if not isinstance(result, dict) or not (result.get("ok") or result.get("already_applied")):
        raise ApprovalError("unknown", raw=str(result)[:200])
    return result


async def void_order(order_id, actor: str, reason: str, *, repo=None) -> dict:
    """Anula una orden APROBADA con asiento contrario (función SQL `void_checkout_order`): las
    renovaciones vuelven a su fecha anterior, las compras nuevas se anulan y liberan su perfil, y el
    historial recibe renglones de reversa (nada se borra). Un cambio de plataforma no se revierte solo."""
    oid = _clean_order_id(order_id)
    if not actor or not str(actor).strip():
        raise ApprovalError("actor_required")
    if not reason or not str(reason).strip():
        raise ApprovalError("reason_required")
    if repo is None:
        from database import orders as repo
    result = await repo.void_order(oid, str(actor).strip(), str(reason).strip())
    if not isinstance(result, dict) or not (result.get("ok") or result.get("already_applied")):
        raise ApprovalError("unknown", raw=str(result)[:200])
    return result
