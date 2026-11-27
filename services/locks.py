"""Candado distribuido en Redis (`SET NX EX`) para jobs y aprobaciones (O02, Lote 1).

- `acquire_lock` toma el candado o devuelve None; `release_lock` solo lo libera quien lo tiene
  (compara el token con un script Lua atómico), así un candado vencido que otro ya tomó no se borra.
- Falla cerrada: si Redis no responde, `acquire_lock` devuelve None y el llamador NO debe proceder
  (mejor saltarse una pasada de un job que correrla dos veces). Es una defensa adicional: la
  idempotencia de la aprobación la garantiza la función SQL del Lote 3, no este candado.
- Mientras el flag `CHECKOUT_V2_ENABLED` esté apagado, ningún flujo en producción lo usa.
"""
from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

LOCK_PREFIX = "lock:"

_RELEASE_SCRIPT = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"
)


def _default_client():
    from bot.middleware import _redis_client
    return _redis_client


def _key(name: str) -> str:
    if not name or not name.strip() or any(c.isspace() for c in name):
        raise ValueError("El nombre del candado no puede estar vacío ni tener espacios")
    return f"{LOCK_PREFIX}{name}"


def acquire_lock(name: str, ttl_seconds: int, client=None) -> Optional[str]:
    """Toma el candado `name` por `ttl_seconds`. Devuelve el token, o None si ya está tomado
    o si Redis falla."""
    if ttl_seconds < 1:
        raise ValueError("ttl_seconds debe ser >= 1")
    key = _key(name)
    token = uuid.uuid4().hex
    try:
        r = client if client is not None else _default_client()
        return token if r.set(key, token, nx=True, ex=int(ttl_seconds)) else None
    except Exception as e:
        logger.error(f"acquire_lock({name}) falló, se trata como no obtenido: {e}")
        return None


def release_lock(name: str, token: str, client=None) -> bool:
    """Libera el candado solo si el token coincide. True si lo liberó."""
    key = _key(name)
    try:
        r = client if client is not None else _default_client()
        return int(r.eval(_RELEASE_SCRIPT, 1, key, token)) == 1
    except Exception as e:
        logger.error(f"release_lock({name}) falló: {e}")
        return False


@contextmanager
def lock(name: str, ttl_seconds: int, client=None) -> Iterator[Optional[str]]:
    """Uso: `with lock("job:debt", 600) as token:` y `if token is None: return`.

    Libera al salir si lo obtuvo. Si no lo obtuvo (ocupado o Redis caído) entrega None.
    """
    token = acquire_lock(name, ttl_seconds, client)
    try:
        yield token
    finally:
        if token is not None:
            release_lock(name, token, client)
