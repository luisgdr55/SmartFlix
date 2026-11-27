"""Único helper de fechas de la orden O02 (Lote 1).

Reglas:
- Las fechas se guardan en UTC y se muestran/calculan en America/Caracas.
- Un periodo termina a las 23:59:59 (hora de Caracas) del día de vencimiento.
- Los meses son meses de calendario (stdlib `calendar`, sin `dateutil`): 31/01 + 1 mes = 28/02
  (29/02 si es bisiesto).
- NUNCA se recorta un timestamp como texto (`end_date[:10]`): ese recorte lee el día en UTC,
  que a las 20:00-23:59 de Caracas ya es el día siguiente. Usar `caracas_date`.

Este módulo es lógica pura: no toca base de datos, Redis ni Telegram. Mientras el flag
`CHECKOUT_V2_ENABLED` esté apagado, ningún flujo en producción lo usa todavía.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, time
from typing import Optional, Union

import pytz

CARACAS = pytz.timezone("America/Caracas")
UTC = pytz.utc
END_OF_DAY = time(23, 59, 59)

DateLike = Union[datetime, str]


def now_caracas() -> datetime:
    """Ahora, con zona America/Caracas."""
    return datetime.now(CARACAS)


def parse_db_datetime(value: DateLike) -> datetime:
    """Convierte un valor leído de la base (texto ISO o datetime) a datetime con zona UTC.

    Un valor sin zona se interpreta como UTC (así devuelve Supabase las columnas `timestamptz`).
    """
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        # Postgres puede devolver «+00» sin minutos y fracciones de 1 a 6 dígitos.
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            if len(text) > 3 and text[-3] in "+-" and text[-2:].isdigit():
                dt = datetime.fromisoformat(text + ":00")
            else:
                raise ValueError(f"Fecha no válida: {value!r}")
    else:
        raise ValueError(f"Fecha no válida: {value!r}")
    if dt.tzinfo is None:
        return UTC.localize(dt)
    return dt.astimezone(UTC)


def to_utc(dt: datetime) -> datetime:
    """A UTC. Exige un datetime con zona: un datetime ingenuo es ambiguo y se rechaza."""
    if dt.tzinfo is None:
        raise ValueError("datetime sin zona horaria: usa parse_db_datetime o localiza primero")
    return dt.astimezone(UTC)


def to_caracas(value: DateLike) -> datetime:
    """A hora de Caracas (acepta texto de la base o datetime con zona)."""
    dt = parse_db_datetime(value) if isinstance(value, str) else value
    if dt.tzinfo is None:
        raise ValueError("datetime sin zona horaria")
    return dt.astimezone(CARACAS)


def caracas_date(value: DateLike) -> date:
    """Fecha calendario en Caracas. Reemplaza el recorte `end_date[:10]` (que lee el día en UTC)."""
    return to_caracas(value).date()


def end_of_day_caracas(d: date) -> datetime:
    """23:59:59 de ese día, hora de Caracas (con zona)."""
    return CARACAS.localize(datetime.combine(d, END_OF_DAY))


def add_months(d: date, months: int, anchor_day: Optional[int] = None) -> date:
    """Suma meses de calendario. Si el día no existe en el mes destino, usa el último día.

    `anchor_day` (opcional) es el día de aniversario original: permite que 31/01 → 28/02 → 31/03
    en vez de derivar a 28/03. Sin `anchor_day` se usa el día de `d`.
    """
    total = d.year * 12 + (d.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    day = anchor_day if anchor_day is not None else d.day
    if not 1 <= day <= 31:
        raise ValueError("anchor_day debe estar entre 1 y 31")
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def whole_months_between(start: date, end: date) -> int:
    """Meses de calendario completos que caben entre `start` y `end` (0 si end <= start)."""
    if end <= start:
        return 0
    n = (end.year - start.year) * 12 + (end.month - start.month)
    if add_months(start, n) > end:
        n -= 1
    return max(n, 0)


def period_end(start: date, months: int, anchor_day: Optional[int] = None) -> datetime:
    """Fin de un periodo de `months` meses que arranca en `start`: 23:59:59 Caracas del día destino."""
    if months < 1:
        raise ValueError("Un periodo dura al menos 1 mes")
    return end_of_day_caracas(add_months(start, months, anchor_day))


def to_db(dt: datetime) -> str:
    """Texto ISO en UTC para guardar en la base (`2026-12-01T03:59:59+00:00`)."""
    return to_utc(dt).isoformat()
