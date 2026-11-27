"""Señales del comprobante de pago (O02 Lote 4). Lógica pura, sin Telegram ni base.

Comparan lo que leyó el OCR (Gemini Vision) con la orden y la configuración de pago:
destinatario (teléfono y cédula), monto, referencia repetida, imagen repetida y fecha.
**Solo marcan**: nunca rechazan ni aprueban; el admin decide (regla de negocio fija).
Un dato que el OCR no leyó es «no leído», no una alarma.
"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

from utils.dates import caracas_date, now_caracas

AMOUNT_TOLERANCE = Decimal("0.50")  # Bs, igual que la validación anterior

OK, WARN, UNKNOWN = "ok", "warn", "unknown"
_ICON = {OK: "✅", WARN: "⚠️", UNKNOWN: "❔"}


def _digits(value) -> str:
    return re.sub(r"\D", "", str(value or ""))


def parse_amount_bs(value) -> Optional[Decimal]:
    """Lee montos como «1250.50», «1.250,50», «1,250.50», «Bs 1.250,50» o «1250». None si no se puede."""
    if value is None or isinstance(value, bool):
        return None
    text = re.sub(r"[^\d.,]", "", str(value))
    if not text or not re.search(r"\d", text):
        return None
    has_dot, has_comma = "." in text, "," in text
    if has_dot and has_comma:
        dec = "," if text.rfind(",") > text.rfind(".") else "."
        thousands = "." if dec == "," else ","
        text = text.replace(thousands, "").replace(dec, ".")
    elif has_comma or has_dot:
        sep = "," if has_comma else "."
        parts = text.split(sep)
        if len(parts) > 2:                       # 1.250.500 → miles
            text = "".join(parts)
        elif len(parts[1]) == 3 and len(parts[0]) <= 3 and parts[0] != "0":
            text = "".join(parts)                # 1.250 → miles (3 dígitos exactos)
        else:
            text = parts[0] + "." + parts[1]
    try:
        d = Decimal(text)
    except InvalidOperation:
        return None
    return d if d.is_finite() and d >= 0 else None


def _compare_digits(read, expected, *, tail: int) -> tuple[str, str]:
    """(nivel, detalle) comparando solo dígitos; para teléfonos se comparan los últimos `tail`."""
    r, e = _digits(read), _digits(expected)
    if not e:
        return UNKNOWN, "sin dato configurado"
    if not r:
        return UNKNOWN, "no leído"
    if tail:
        r, e = r[-tail:], e[-tail:]
    return (OK, "coincide") if r == e else (WARN, "no coincide")


def evaluate_signals(
    extracted: Optional[dict],
    *,
    total_bs,
    payment_cfg: Optional[dict],
    reference_seen: bool = False,
    image_duplicate: bool = False,
    order_created_at: Optional[datetime] = None,
    tolerance: Decimal = AMOUNT_TOLERANCE,
) -> dict:
    """Devuelve las señales como un dict JSON-serializable (se guarda en `checkout_orders.ocr_flags`).

    {"ocr": "ok|unavailable|not_receipt", "monto": {...}, "telefono": {...}, "cedula": {...},
     "referencia": {...}, "imagen_repetida": bool, "fecha": {...}, "alertas": [texto, ...]}
    Cada señal lleva `nivel` ("ok" | "warn" | "unknown") y `detalle`.
    """
    ex = extracted if isinstance(extracted, dict) else {}
    valid = ex.get("is_comprobante_valido")
    ocr_state = "ok" if valid is True else ("not_receipt" if valid is False else "unavailable")
    flags: dict = {"ocr": ocr_state, "imagen_repetida": bool(image_duplicate)}
    alerts: list[str] = []

    if ocr_state != "ok":
        un = {"nivel": UNKNOWN, "detalle": "OCR no disponible"}
        flags.update({"monto": dict(un), "telefono": dict(un), "cedula": dict(un),
                      "referencia": dict(un, valor=None), "fecha": dict(un)})
        alerts.append("La imagen no se reconoció como comprobante" if ocr_state == "not_receipt"
                      else "OCR no disponible: revisa la imagen a mano")
    else:
        # Monto contra el total de la orden.
        expected = parse_amount_bs(total_bs)
        read = parse_amount_bs(ex.get("monto"))
        if read is None or expected is None:
            flags["monto"] = {"nivel": UNKNOWN, "detalle": "no leído", "leido": None,
                              "esperado": str(expected) if expected is not None else None}
        elif abs(read - expected) <= tolerance:
            flags["monto"] = {"nivel": OK, "detalle": "coincide", "leido": str(read), "esperado": str(expected)}
        else:
            flags["monto"] = {"nivel": WARN, "detalle": "no coincide", "leido": str(read), "esperado": str(expected)}
            alerts.append(f"Monto del comprobante Bs {read:,.2f} ≠ total de la orden Bs {expected:,.2f}")

        cfg = payment_cfg or {}
        lvl, det = _compare_digits(ex.get("celular_destino"), cfg.get("telefono"), tail=10)
        flags["telefono"] = {"nivel": lvl, "detalle": det}
        if lvl == WARN:
            alerts.append("El teléfono destino no coincide con el de cobro")
        lvl, det = _compare_digits(ex.get("cedula_receptor"), cfg.get("cedula"), tail=0)
        flags["cedula"] = {"nivel": lvl, "detalle": det}
        if lvl == WARN:
            alerts.append("La cédula del receptor no coincide con la de cobro")

        ref = str(ex.get("referencia") or "").strip() or None
        if ref is None:
            flags["referencia"] = {"nivel": UNKNOWN, "detalle": "no leída", "valor": None}
        elif reference_seen:
            flags["referencia"] = {"nivel": WARN, "detalle": "ya usada en otro pago", "valor": ref}
            alerts.append(f"La referencia {ref} ya figura en otro pago")
        else:
            flags["referencia"] = {"nivel": OK, "detalle": "nueva", "valor": ref}

        fecha = _receipt_date(ex.get("fecha"))
        if fecha is None:
            flags["fecha"] = {"nivel": UNKNOWN, "detalle": "no leída"}
        else:
            created = caracas_date(order_created_at or now_caracas())
            if fecha < created:
                flags["fecha"] = {"nivel": WARN, "detalle": "anterior a la orden", "valor": fecha.isoformat()}
                alerts.append(f"La fecha del comprobante ({fecha.strftime('%d/%m/%Y')}) es anterior a la orden")
            else:
                flags["fecha"] = {"nivel": OK, "detalle": "posterior o igual a la orden", "valor": fecha.isoformat()}

    if image_duplicate:
        alerts.append("Esta misma imagen ya se envió antes")
    flags["alertas"] = alerts
    return flags


def _receipt_date(value):
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value).strip(), fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def summary_lines(flags: dict) -> list[str]:
    """Líneas legibles para el ticket del admin (texto plano; el llamador escapa el HTML)."""
    if not flags:
        return ["❔ Sin señales"]

    def row(label: str, sig: dict, extra: str = "") -> str:
        s = sig or {"nivel": UNKNOWN, "detalle": "sin dato"}
        return f"{_ICON.get(s.get('nivel'), '❔')} {label}: {s.get('detalle', '')}{extra}"

    monto = flags.get("monto") or {}
    extra_monto = f" (leído Bs {monto['leido']} / esperado Bs {monto['esperado']})" if monto.get("leido") and monto.get("esperado") else ""
    lines = [
        row("Monto", monto, extra_monto),
        row("Teléfono destino", flags.get("telefono")),
        row("Cédula receptor", flags.get("cedula")),
        row("Referencia", flags.get("referencia")),
        row("Fecha", flags.get("fecha")),
    ]
    if flags.get("imagen_repetida"):
        lines.append("⚠️ Imagen repetida: ya se envió antes")
    return lines
