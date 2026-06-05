"""
database/hogar.py — Lógica de datos para soporte de restricción de hogar Netflix
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

HEALTH_THRESHOLDS = {'healthy': 2, 'warning': 4}  # ≤2 healthy, ≤4 warning, >4 restricted


def _client():
    from database import get_supabase
    return get_supabase()


async def create_incident(user_id, account_id, profile_id, subscription_id,
                          stage: str, incident_type: str) -> Optional[dict]:
    try:
        result = _client().table('household_incidents').insert({
            'user_id': str(user_id),
            'account_id': str(account_id),
            'profile_id': str(profile_id),
            'subscription_id': str(subscription_id),
            'stage': stage,
            'type': incident_type,
            'resolved': False,
        }).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"[hogar] create_incident: {e}")
        return None


async def update_incident(incident_id: str, **kwargs) -> Optional[dict]:
    try:
        result = _client().table('household_incidents').update(kwargs).eq('id', str(incident_id)).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"[hogar] update_incident: {e}")
        return None


async def get_open_incident(user_id: str) -> Optional[dict]:
    try:
        result = _client().table('household_incidents').select('*') \
            .eq('user_id', str(user_id)).eq('resolved', False) \
            .order('created_at', desc=True).limit(1).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"[hogar] get_open_incident: {e}")
        return None


async def update_account_health(account_id: str) -> str:
    """Recalcula y actualiza account_health según incidentes de los últimos 60 días."""
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        result = _client().table('household_incidents').select('id', count='exact') \
            .eq('account_id', str(account_id)).gte('created_at', cutoff).execute()
        count = result.count or 0

        if count <= HEALTH_THRESHOLDS['healthy']:
            health = 'healthy'
        elif count <= HEALTH_THRESHOLDS['warning']:
            health = 'warning'
        else:
            health = 'restricted'

        _client().table('accounts').update({
            'household_incidents': count,
            'account_health': health,
            'last_incident_at': datetime.now(timezone.utc).isoformat(),
        }).eq('id', str(account_id)).execute()

        return health
    except Exception as e:
        logger.error(f"[hogar] update_account_health: {e}")
        return 'healthy'


async def get_available_profiles_for_migration(user_id: str, exclude_account_id: str = None) -> list:
    """
    Perfiles disponibles para migración.
    Excluye perfiles donde este usuario tuvo restricción de hogar en los últimos 45 días.
    """
    try:
        cutoff_45 = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()

        restricted_result = _client().table('household_incidents').select('profile_id') \
            .eq('user_id', str(user_id)) \
            .in_('type', ['express', 'history']) \
            .gte('created_at', cutoff_45).execute()
        restricted_ids = [r['profile_id'] for r in (restricted_result.data or []) if r.get('profile_id')]

        query = _client().table('profiles').select(
            'id, profile_name, pin, account_id, last_released,'
            'accounts!inner(id, email, account_health, household_incidents)'
        ).eq('status', 'available').eq('profile_type', 'monthly')

        if exclude_account_id:
            query = query.neq('account_id', str(exclude_account_id))

        result = query.execute()
        profiles = result.data or []

        # 3. Filtro Python: excluir restringidos y cuentas restricted
        profiles = [
            p for p in profiles
            if p['id'] not in restricted_ids
            and p.get('accounts', {}).get('account_health') not in ('restricted',)
        ]

        # Ordenar: cuentas healthy primero, luego por last_released más antiguo
        profiles.sort(key=lambda p: (
            0 if p.get('accounts', {}).get('account_health') == 'healthy' else 1,
            p.get('last_released') or '',
        ))
        logger.info(f"[hogar] profiles disponibles para migración: {len(profiles)} "
                    f"(excluidos: {len(restricted_ids)} restringidos para este usuario)")
        return profiles
    except Exception as e:
        logger.error(f"[hogar] get_available_profiles_for_migration: {e}")
        return []


async def get_netflix_subscription_for_user(user_id: str) -> Optional[dict]:
    """Retorna la suscripción Netflix activa del usuario con datos de cuenta y perfil."""
    try:
        result = _client().table('subscriptions').select(
            'id, end_date, profile_id,'
            'profiles!inner(id, profile_name, pin, account_id,'
            '  accounts!inner(id, email, account_health, household_incidents)),'
            'platforms!inner(name)'
        ).eq('user_id', str(user_id)).eq('status', 'active') \
            .ilike('platforms.name', '%netflix%').limit(1).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"[hogar] get_netflix_subscription_for_user: {e}")
        return None


async def get_incident_history(user_id: str, limit: int = 10) -> list:
    try:
        result = _client().table('household_incidents').select('*') \
            .eq('user_id', str(user_id)).order('created_at', desc=True).limit(limit).execute()
        return result.data or []
    except Exception as e:
        logger.error(f"[hogar] get_incident_history: {e}")
        return []


async def get_accounts_needing_health_alert() -> list:
    """Cuentas que cambiaron a warning o restricted en las últimas 24 horas."""
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        result = _client().table('accounts').select('*') \
            .neq('account_health', 'healthy').gte('last_incident_at', cutoff).execute()
        return result.data or []
    except Exception as e:
        logger.error(f"[hogar] get_accounts_needing_health_alert: {e}")
        return []
