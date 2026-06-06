"""
bot/handlers/hogar.py
Módulo de soporte para restricción de hogar Netflix.
Maneja el flujo de cliente (autoservicio) y admin (/hogar).
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timezone

import redis
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

# ── Redis keys ──────────────────────────────────────────────────
_STATE_KEY = "hogar_state:{tid}"
_SESSION_KEY = "hogar_session:{tid}"
_RETRIES_KEY = "hogar_retries:{tid}"
_INCIDENT_KEY = "hogar_incident:{tid}"
_ADMIN_SESSION_KEY = "hogar_admin_session:{tid}"
_ADMIN_SEARCH_KEY = "hogar_admin_search:{tid}"
_TTL = 3600  # 1 hora

# ── Estados ─────────────────────────────────────────────────────
STATE_WAITING_PHOTO = "waiting_photo"
STATE_WAITING_EMAIL_CONFIRM = "waiting_email_confirm"
STATE_MIGRATION_CHOICE = "migration_choice"
STATE_EXPRESS_CONFIRM = "express_confirm"


# ── Helpers Redis ────────────────────────────────────────────────

def _redis():
    return redis.from_url(os.environ.get("REDIS_URL", ""), decode_responses=True)

def get_state(telegram_id: int) -> str:
    return _redis().get(_STATE_KEY.format(tid=telegram_id)) or ""

def _set_state(telegram_id: int, state: str, session: dict = None):
    r = _redis()
    r.setex(_STATE_KEY.format(tid=telegram_id), _TTL, state)
    if session is not None:
        r.setex(_SESSION_KEY.format(tid=telegram_id), _TTL, json.dumps(session))

def _get_session(telegram_id: int) -> dict:
    raw = _redis().get(_SESSION_KEY.format(tid=telegram_id))
    return json.loads(raw) if raw else {}

def _clear_state(telegram_id: int):
    r = _redis()
    for key in [_STATE_KEY, _SESSION_KEY, _RETRIES_KEY, _INCIDENT_KEY]:
        r.delete(key.format(tid=telegram_id))

def _is_admin(telegram_id: int) -> bool:
    from utils.validators import is_admin
    from config import settings
    return is_admin(telegram_id, settings.ADMIN_TELEGRAM_IDS)

def _get_admin_ids() -> list[int]:
    from utils.helpers import parse_telegram_ids
    from config import settings
    return parse_telegram_ids(settings.ADMIN_TELEGRAM_IDS)


# ════════════════════════════════════════════════════════════════
# FLUJO CLIENTE — Autoservicio
# ════════════════════════════════════════════════════════════════

async def start_hogar_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Punto de entrada: mensaje directo o callback query del menú de soporte."""
    query = update.callback_query
    if query:
        await query.answer()

    telegram_id = update.effective_user.id

    from database.users import get_or_create_user
    from database.hogar import get_netflix_subscription_for_user

    tg_user = update.effective_user
    user = await get_or_create_user(telegram_id, tg_user.username, tg_user.full_name)
    if not user:
        msg = "❌ No pude identificar tu cuenta. Intenta de nuevo."
        if query:
            await query.edit_message_text(msg)
        else:
            await update.message.reply_text(msg)
        return

    subs = await get_netflix_subscription_for_user(str(user['id']))
    if not subs:
        kb = [[InlineKeyboardButton("🔄 Renovar suscripción", callback_data="menu:subscribe")]]
        msg = (
            "⚠️ *Sin suscripción Netflix activa*\n\n"
            "Para recibir soporte primero debes tener un servicio activo."
        )
        if query:
            await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN,
                                           reply_markup=InlineKeyboardMarkup(kb))
        else:
            await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN,
                                             reply_markup=InlineKeyboardMarkup(kb))
        return
    sub = subs[0]  # Usar la primera para el flujo de soporte cliente

    account = sub['profiles']['accounts']
    session = {
        'user_id': str(user['id']),
        'subscription_id': str(sub['id']),
        'account_id': str(account['id']),
        'profile_id': str(sub['profiles']['id']),
        'account_email': account['email'],
    }
    _set_state(telegram_id, STATE_WAITING_PHOTO, session)
    saved_state = get_state(telegram_id)
    logger.info(f"[hogar] state set for tid={telegram_id}: '{saved_state}'")

    instructions = (
        "🔒 *Soporte — Restricción de Hogar Netflix*\n\n"
        "Sigue estos pasos antes de enviarme la foto:\n\n"
        "1️⃣ En tu TV verás: *\"Tu TV no forma parte del Hogar...\"*\n"
        "2️⃣ Pulsa el botón *\"Esta es mi Cuenta\"*\n"
        "3️⃣ Aparecerá una segunda pantalla con opciones\n"
        "4️⃣ Toma una foto clara de esa *segunda pantalla*\n"
        "5️⃣ Envíame esa foto aquí\n\n"
        "📸 *Cuando tengas la foto lista, envíala en este chat.*"
    )
    if query:
        await query.edit_message_text(instructions, parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(instructions, parse_mode=ParseMode.MARKDOWN)


async def handle_hogar_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Maneja fotos cuando el cliente está en STATE_WAITING_PHOTO.
    Retorna True si la foto fue procesada por este módulo.
    """
    telegram_id = update.effective_user.id
    current_state = get_state(telegram_id)
    logger.info(f"[hogar] handle_hogar_photo tid={telegram_id} state='{current_state}'")
    if current_state != STATE_WAITING_PHOTO:
        return False

    session = _get_session(telegram_id)
    r = _redis()
    retries = int(r.get(_RETRIES_KEY.format(tid=telegram_id)) or 0)

    photo = update.message.photo[-1]
    photo_file = await context.bot.get_file(photo.file_id)
    photo_bytes = bytes(await photo_file.download_as_bytearray())

    await update.message.reply_text("🔍 Analizando tu pantalla...")

    from services.gemini_service import analyze_netflix_screen
    analysis = await analyze_netflix_screen(photo_bytes)
    screen_type = analysis.get('screen_type', 'unknown')

    if screen_type == 'first_warning':
        _set_state(telegram_id, STATE_WAITING_EMAIL_CONFIRM, session)
        kb = [
            [InlineKeyboardButton("✉️ Ya pulsé 'Enviar email'", callback_data=f"hogar:travel_done:{telegram_id}")],
            [InlineKeyboardButton("❓ No me aparece esa opción", callback_data=f"hogar:no_travel:{telegram_id}")],
        ]
        await update.message.reply_text(
            "✅ *Opción de acceso temporal disponible*\n\n"
            "Sigue estos pasos en tu TV:\n"
            "1️⃣ Pulsa *\"Estoy de viaje\"*\n"
            "2️⃣ En la siguiente pantalla pulsa *\"Enviar email\"*\n"
            "3️⃣ Espera el correo en tu teléfono\n\n"
            "⏳ Cuando hayas pulsado *\"Enviar email\"*, dímelo aquí:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(kb),
        )

    elif screen_type == 'second_warning':
        _set_state(telegram_id, STATE_MIGRATION_CHOICE, session)
        kb = [
            [InlineKeyboardButton("🚀 Express (inmediata, pierde historial)", callback_data=f"hogar:b_express:{telegram_id}")],
            [InlineKeyboardButton("📦 Con historial (1-4 horas)", callback_data=f"hogar:b_history:{telegram_id}")],
        ]
        await update.message.reply_text(
            "⚠️ *Acceso temporal agotado — Migración necesaria*\n\n"
            "Tu perfil debe ser migrado a otra cuenta de Netflix.\n\n"
            "🚀 *MIGRACIÓN EXPRESS* — Inmediata\n"
            "• Credenciales nuevas en minutos\n"
            "• ⚠️ Perderás tu historial de Netflix\n\n"
            "📦 *CON HISTORIAL* — 1-4 horas\n"
            "• Tu historial queda conservado\n"
            "• Requiere procesamiento manual del administrador\n\n"
            "¿Cuál prefieres?",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(kb),
        )

    else:
        if retries < 2:
            r.setex(_RETRIES_KEY.format(tid=telegram_id), _TTL, retries + 1)
            await update.message.reply_text(
                "❓ *No pude identificar la pantalla*\n\n"
                "Asegúrate de:\n"
                "• Fotografiar la *segunda pantalla* (tras pulsar 'Esta es mi Cuenta')\n"
                "• Que el texto sea legible y la imagen sea clara\n\n"
                "Envíame una nueva foto.",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            r.delete(_RETRIES_KEY.format(tid=telegram_id))
            await _escalate_to_admin_from_update(update, context, session,
                                                  "Foto de pantalla no reconocida tras 3 intentos")
    return True


# ── Gmail: buscar y entregar link ───────────────────────────────

async def _search_and_deliver_gmail(edit_func, context, session: dict, telegram_id: int):
    """Busca el link de Netflix en Gmail maestro y lo entrega."""
    account_email = session.get('account_email', '')
    master_creds = os.environ.get("GMAIL_MASTER_CREDENTIALS_JSON", "")

    try:
        await edit_func("⏳ Buscando tu código en el correo...")
    except Exception:
        pass

    from services.gmail_service import get_netflix_access_code
    result = await get_netflix_access_code(account_email, master_creds)
    found = result.get('type') is not None

    if found:
        from database.hogar import create_incident, update_account_health
        incident = await create_incident(
            user_id=session['user_id'], account_id=session['account_id'],
            profile_id=session['profile_id'], subscription_id=session['subscription_id'],
            stage='first_warning', incident_type='code_sent',
        )
        if incident:
            _redis().setex(_INCIDENT_KEY.format(tid=telegram_id), _TTL, incident['id'])
        await update_account_health(session['account_id'])

        kb = [
            [InlineKeyboardButton("✅ Sí, ya funciona", callback_data=f"hogar:resolved:{telegram_id}")],
            [InlineKeyboardButton("❌ Sigo bloqueado", callback_data=f"hogar:not_resolved:{telegram_id}")],
        ]
        if result['type'] == 'code':
            msg = (
                "✅ *¡Tu código de acceso está listo!*\n\n"
                f"🔢 Tu código es: *`{result['value']}`*\n\n"
                "⏰ Tienes *15 minutos* para usarlo.\n\n"
                "Introdúcelo en tu TV y dime si pudiste acceder:"
            )
        else:
            msg = (
                "✅ *¡Tu código está listo!*\n\n"
                f"🔗 Haz clic aquí para obtener tu código de 4 dígitos:\n{result['value']}\n\n"
                "⏰ Tienes *15 minutos* para usarlo.\n\n"
                "Introdúcelo en tu TV y dime si pudiste acceder:"
            )
        try:
            await edit_func(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))
        except Exception:
            await context.bot.send_message(chat_id=telegram_id, text=msg,
                                            parse_mode=ParseMode.MARKDOWN,
                                            reply_markup=InlineKeyboardMarkup(kb))
    else:
        # No encontrado — dar opción de reintentar manualmente sin bloquear
        kb = [
            [InlineKeyboardButton("🔄 Buscar de nuevo", callback_data=f"hogar:retry_gmail:{telegram_id}")],
            [InlineKeyboardButton("👨‍💼 Contactar admin", callback_data=f"hogar:escalate:{telegram_id}")],
        ]
        msg = (
            "⏳ *El email aún no ha llegado*\n\n"
            "Netflix puede tardar 1-2 minutos en enviar el código.\n\n"
            "Espera un momento y pulsa *'Buscar de nuevo'*:"
        )
        try:
            await edit_func(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))
        except Exception:
            await context.bot.send_message(chat_id=telegram_id, text=msg,
                                            parse_mode=ParseMode.MARKDOWN,
                                            reply_markup=InlineKeyboardMarkup(kb))


# ── Escalación al admin ─────────────────────────────────────────

async def _escalate_to_admin_from_update(update, context, session: dict, reason: str):
    telegram_id = update.effective_user.id
    await update.message.reply_text(
        "👨‍💼 *Caso escalado al administrador*\n\n"
        "El administrador revisará tu caso y te atenderá a la brevedad.",
        parse_mode=ParseMode.MARKDOWN,
    )
    await _notify_admin_escalation(context, session, telegram_id, reason)
    _clear_state(telegram_id)


async def _notify_admin_escalation(context, session: dict, client_tid: int, reason: str):
    for admin_id in _get_admin_ids():
        try:
            kb = [[InlineKeyboardButton("🔍 Gestionar caso", callback_data=f"hogar:admin_manage:{client_tid}")]]
            await context.bot.send_message(
                chat_id=admin_id,
                text=(
                    f"🆘 *Caso escalado — Hogar Netflix*\n\n"
                    f"👤 Cliente TID: `{client_tid}`\n"
                    f"📧 Cuenta: `{session.get('account_email', '—')}`\n"
                    f"❓ Motivo: {reason}"
                ),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(kb),
            )
        except Exception as e:
            logger.error(f"[hogar] notify_admin_escalation admin={admin_id}: {e}")


# ── Core: ejecutar migración express ────────────────────────────

async def _execute_express_migration(context, session: dict, target_profile: dict,
                                      telegram_id: int, incident_stage: str = 'second_warning') -> bool:
    from database.profiles import assign_profile, release_profile
    from database.hogar import create_incident, update_incident, update_account_health
    from database import get_supabase

    old_profile_id = session.get('profile_id')
    new_profile_id = target_profile['id']
    subscription_id = session.get('subscription_id')

    try:
        await assign_profile(new_profile_id)
        await release_profile(old_profile_id)

        get_supabase().table('subscriptions').update(
            {'profile_id': new_profile_id}
        ).eq('id', str(subscription_id)).execute()

        incident = await create_incident(
            user_id=session['user_id'], account_id=session['account_id'],
            profile_id=old_profile_id, subscription_id=subscription_id,
            stage=incident_stage, incident_type='express',
        )
        if incident:
            await update_incident(incident['id'],
                new_profile_id=new_profile_id,
                new_account_id=target_profile.get('account_id'),
                resolved=True,
                resolved_at=datetime.now(timezone.utc).isoformat(),
            )
        await update_account_health(session['account_id'])

        new_email = target_profile.get('accounts', {}).get('email', '—')
        new_password = target_profile.get('accounts', {}).get('password', '—')
        new_name = target_profile.get('profile_name', '—')
        new_pin = target_profile.get('pin', '—')

        sub_result = get_supabase().table('subscriptions').select('end_date') \
            .eq('id', str(subscription_id)).execute()
        end_date = sub_result.data[0].get('end_date', '—') if sub_result.data else '—'

        wa_ticket = (
            f"Hola! Tu Netflix fue migrado exitosamente.\n\n"
            f"📧 Email: {new_email}\n"
            f"🔑 Clave: {new_password}\n"
            f"👤 Perfil: {new_name}\n"
            f"🔢 PIN: {new_pin}\n"
            f"📅 Vence: {end_date}\n\n"
            f"En tu TV: cierra sesión e inicia con estas credenciales."
        )

        kb = [
            [InlineKeyboardButton("✅ Ya funciona", callback_data=f"hogar:resolved:{telegram_id}")],
            [InlineKeyboardButton("❌ Sigo con problemas", callback_data=f"hogar:not_resolved:{telegram_id}")],
        ]
        try:
            await context.bot.send_message(
                chat_id=telegram_id,
                text=(
                    f"🎉 *¡Migración completada!*\n\n"
                    f"📧 Email: `{new_email}`\n"
                    f"🔑 Clave: `{new_password}`\n"
                    f"👤 Perfil: {new_name}\n"
                    f"🔢 PIN: `{new_pin}`\n"
                    f"📅 Vence: {end_date}\n\n"
                    f"En tu TV: cierra sesión e inicia con estas credenciales. ¿Pudiste acceder?"
                ),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(kb),
            )
        except Exception:
            pass

        for admin_id in _get_admin_ids():
            try:
                # Obtener datos completos del cliente
                _sb = get_supabase()
                user_result = _sb.table('users').select(
                    'name, phone, username'
                ).eq('id', str(session['user_id'])).execute()
                user_data = user_result.data[0] if user_result.data else {}

                # Obtener credenciales de la cuenta origen
                origin_result = _sb.table('accounts').select(
                    'email, password'
                ).eq('id', str(session['account_id'])).execute()
                origin_data = origin_result.data[0] if origin_result.data else {}

                client_name = user_data.get('name') or user_data.get('username') or 'Sin nombre'
                client_phone = user_data.get('phone') or '—'
                origin_email = origin_data.get('email', '—')
                origin_password = origin_data.get('password', '—')

                await context.bot.send_message(
                    chat_id=admin_id,
                    text=(
                        f"🔄 *Migración Express Ejecutada*\n\n"
                        f"👤 Cliente: *{client_name}*\n"
                        f"📱 Teléfono: {client_phone}\n"
                        f"🆔 TID: `{telegram_id}`\n\n"
                        f"📤 *Cuenta origen liberada:*\n"
                        f"  📧 Email: `{origin_email}`\n"
                        f"  🔑 Clave: `{origin_password}`\n\n"
                        f"📥 *Destino asignado:*\n"
                        f"  📧 Email: `{new_email}`\n"
                        f"  👤 Perfil: {new_name}\n"
                        f"  🔢 PIN: `{new_pin}`\n\n"
                        f"📋 *Ticket WhatsApp:*\n`{wa_ticket}`"
                    ),
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception as e:
                logger.error(f"[hogar] notify_admin_after_express: {e}")

        return True
    except Exception as e:
        logger.error(f"[hogar] _execute_express_migration: {e}")
        return False


# ════════════════════════════════════════════════════════════════
# DISPATCHER DE CALLBACKS
# ════════════════════════════════════════════════════════════════

async def handle_hogar_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dispatcher principal para callbacks hogar:*"""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    if len(parts) < 2:
        return

    action = parts[1]
    raw_client_id = parts[2] if len(parts) > 2 else str(query.from_user.id)
    if raw_client_id.startswith("uid_"):
        client_tid = raw_client_id  # mantener como string "uid_xxxx"
    else:
        client_tid = int(raw_client_id) if raw_client_id.lstrip('-').isdigit() else query.from_user.id
    caller_tid = query.from_user.id

    # ── Callbacks de cliente ─────────────────────────────────────
    if action == "travel_done":
        session = _get_session(client_tid)
        if not session:
            await query.edit_message_text("❌ Sesión expirada. Escribe 'soporte' para comenzar.")
            return
        await _search_and_deliver_gmail(
            lambda *a, **kw: query.edit_message_text(*a, **kw),
            context, session, client_tid
        )

    elif action == "no_travel":
        session = _get_session(client_tid)
        _set_state(client_tid, STATE_MIGRATION_CHOICE, session)
        kb = [
            [InlineKeyboardButton("🚀 Express (inmediata)", callback_data=f"hogar:b_express:{client_tid}")],
            [InlineKeyboardButton("📦 Con historial (1-4 horas)", callback_data=f"hogar:b_history:{client_tid}")],
        ]
        await query.edit_message_text(
            "⚠️ *Migración necesaria*\n\n"
            "🚀 *EXPRESS* — Inmediata · Pierde historial\n"
            "📦 *CON HISTORIAL* — 1-4 horas · Historial conservado\n\n¿Cuál prefieres?",
            parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb),
        )

    elif action == "retry_gmail":
        session = _get_session(client_tid)
        if not session:
            await query.edit_message_text("❌ Sesión expirada. Escribe 'soporte' para comenzar.")
            return
        await _search_and_deliver_gmail(
            lambda *a, **kw: query.edit_message_text(*a, **kw),
            context, session, client_tid
        )

    elif action == "resolved":
        incident_id = _redis().get(_INCIDENT_KEY.format(tid=client_tid))
        if incident_id:
            from database.hogar import update_incident
            await update_incident(incident_id, resolved=True,
                resolved_at=datetime.now(timezone.utc).isoformat())
        _clear_state(client_tid)
        await query.edit_message_text(
            "🎉 *¡Perfecto!* Nos alegra que hayas podido acceder.\n\n"
            "Si vuelves a tener problemas escribe 'soporte hogar' aquí.",
            parse_mode=ParseMode.MARKDOWN,
        )

    elif action == "not_resolved":
        # Notificar a todos los admins con contexto completo del cliente
        session = _get_session(client_tid)
        subscription_id = session.get("subscription_id") if session else None
        incident_id     = session.get("incident_id")     if session else None

        client_name  = "Desconocido"
        client_phone = "N/A"
        acc_email    = "N/A"

        try:
            db = get_supabase()
            u_res = db.table('users').select('name, phone').eq('telegram_id', str(client_tid)).limit(1).execute()
            if u_res.data:
                client_name  = u_res.data[0].get('name')  or client_name
                client_phone = u_res.data[0].get('phone') or client_phone

            if subscription_id:
                s_res = db.table('subscriptions').select(
                    'profiles!inner(accounts!inner(email))'
                ).eq('id', str(subscription_id)).limit(1).execute()
                if s_res.data:
                    acc_email = (s_res.data[0]
                                 .get('profiles', {})
                                 .get('accounts', {})
                                 .get('email', acc_email))
        except Exception as exc:
            logger.error(f"[hogar] not_resolved: error obteniendo datos para admin: {exc}")

        admin_msg = (
            f"⚠️ *Cliente sigue con problemas tras migración*\n\n"
            f"👤 *Cliente:* {client_name}\n"
            f"📞 *Teléfono:* {client_phone}\n"
            f"🆔 *Telegram ID:* `{client_tid}`\n"
            f"📧 *Cuenta origen:* `{acc_email}`\n"
            f"🎫 *Incidente ID:* `{incident_id or 'N/A'}`\n\n"
            f"El cliente indicó que el problema *no fue resuelto*. "
            f"Por favor revisa y contacta manualmente."
        )
        for admin_id in _get_admin_ids():
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=admin_msg,
                    parse_mode="Markdown"
                )
            except Exception as exc:
                logger.error(f"[hogar] not_resolved: no se pudo notificar admin {admin_id}: {exc}")

        kb = [
            [InlineKeyboardButton("🔄 Buscar código de nuevo", callback_data=f"hogar:retry_gmail:{client_tid}")],
            [InlineKeyboardButton("🔄 Migrar perfil",          callback_data=f"hogar:no_travel:{client_tid}")],
            [InlineKeyboardButton("👨‍💼 Hablar con admin",       callback_data=f"hogar:escalate:{client_tid}")],
        ]
        await query.edit_message_text(
            "😔 Entendido. Ya notifiqué a un administrador para que te contacte.\n"
            "¿Qué más deseas intentar?",
            reply_markup=InlineKeyboardMarkup(kb)
        )

    elif action == "escalate":
        session = _get_session(client_tid)
        await query.edit_message_text("👨‍💼 El administrador ha sido notificado y te atenderá pronto.")
        if session:
            await _notify_admin_escalation(context, session, client_tid, "Escalado manualmente por cliente")
        _clear_state(client_tid)

    elif action == "b_express":
        session = _get_session(client_tid)
        _set_state(client_tid, STATE_EXPRESS_CONFIRM, session)
        kb = [
            [InlineKeyboardButton("✅ Sí, confirmo", callback_data=f"hogar:confirm_express:{client_tid}")],
            [InlineKeyboardButton("⬅️ Volver", callback_data=f"hogar:no_travel:{client_tid}")],
        ]
        await query.edit_message_text(
            "⚠️ *Confirma la Migración Express*\n\n"
            "• Migración inmediata\n• Tu historial se *perderá*\n• Recibes credenciales al instante\n\n"
            "¿Confirmas?",
            parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb),
        )

    elif action == "confirm_express":
        session = _get_session(client_tid)
        if not session:
            await query.edit_message_text("❌ Sesión expirada. Escribe 'soporte' para comenzar.")
            return
        await query.edit_message_text("⚡ Procesando migración...")
        from database.hogar import get_available_profiles_for_migration
        available = await get_available_profiles_for_migration(
            session['user_id'], session.get('account_id')
        )
        if not available:
            kb = [[InlineKeyboardButton("📦 Solicitar con historial", callback_data=f"hogar:b_history:{client_tid}")]]
            await query.edit_message_text(
                "😔 *Sin perfiles disponibles en este momento.*\n\nPuedes solicitar migración con historial.",
                parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb),
            )
            await _notify_admin_escalation(context, session, client_tid, "Sin perfiles para migración express")
            return
        success = await _execute_express_migration(context, session, available[0], client_tid)
        if not success:
            await query.edit_message_text("❌ Error en la migración. El administrador fue notificado.")
            await _notify_admin_escalation(context, session, client_tid, "Error en migración express automática")
        _clear_state(client_tid)

    elif action == "b_history":
        session = _get_session(client_tid)
        from database.hogar import create_incident, update_account_health
        incident = await create_incident(
            user_id=session.get('user_id'), account_id=session.get('account_id'),
            profile_id=session.get('profile_id'), subscription_id=session.get('subscription_id'),
            stage='second_warning', incident_type='history',
        )
        await update_account_health(session.get('account_id', ''))
        incident_id = incident['id'] if incident else 'N/A'
        await query.edit_message_text(
            "📦 *Migración con Historial solicitada*\n\n"
            "El administrador procesará la migración conservando tu historial.\n"
            "⏳ Tiempo estimado: minutos a pocas horas.\n\n"
            "Te notificamos aquí cuando esté lista. ¡Gracias por tu paciencia!",
            parse_mode=ParseMode.MARKDOWN,
        )
        account_email = session.get('account_email', '—')
        for admin_id in _get_admin_ids():
            try:
                # Guardar incident_id en Redis para no superar 64 bytes en callback
                _redis().setex(f"hogar_incident_admin:{admin_id}", _TTL, str(incident_id))
                kb = [
                    [InlineKeyboardButton("⏳ En proceso", callback_data=f"hogar:history_inprogress:{client_tid}")],
                    [InlineKeyboardButton("✅ Completada", callback_data=f"hogar:complete_history:{client_tid}")],
                ]
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=(
                        f"📦 *Solicitud — Migración con Historial Netflix*\n\n"
                        f"👤 Cliente TID: `{client_tid}`\n"
                        f"📧 Cuenta actual: `{account_email}`\n"
                        f"🆔 Incidente: `{incident_id}`\n\n"
                        f"Procesa manualmente en Netflix y marca cuando termine."
                    ),
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup(kb),
                )
            except Exception as e:
                logger.error(f"[hogar] notify_admin_history: {e}")
        _clear_state(client_tid)

    elif action == "retry_photo":
        session = _get_session(client_tid)
        _set_state(client_tid, STATE_WAITING_PHOTO, session)
        await query.edit_message_text(
            "📸 Envíame una foto clara de la *segunda pantalla* de Netflix "
            "(la que aparece después de pulsar 'Esta es mi Cuenta').",
            parse_mode=ParseMode.MARKDOWN,
        )

    # ── Callbacks de admin ───────────────────────────────────────
    elif action == "select_sub":
        if not _is_admin(caller_tid):
            return
        sub_index_raw = parts[3] if len(parts) > 3 else None
        if sub_index_raw is None or not sub_index_raw.isdigit():
            await query.edit_message_text("❌ Error al seleccionar suscripción.")
            return
        sub_index = int(sub_index_raw)

        # Leer subs guardadas en Redis
        subs_raw = _redis().get(f"hogar_subs:{caller_tid}")
        if not subs_raw:
            await query.edit_message_text("❌ Sesión expirada. Usa /hogar de nuevo.")
            return
        subs_list = json.loads(subs_raw)
        if sub_index >= len(subs_list):
            await query.edit_message_text("❌ Índice de suscripción inválido.")
            return
        sub_data = subs_list[sub_index]

        # Obtener datos del usuario
        if str(client_tid).startswith("uid_"):
            uid = str(client_tid).replace("uid_", "")
            from database import get_supabase as _gc
            result = _gc().table('users').select('*').eq('id', uid).execute()
            user = result.data[0] if result.data else None
        else:
            from database.users import get_user_by_telegram_id
            user = await get_user_by_telegram_id(int(client_tid))

        if not user:
            await query.edit_message_text("❌ Cliente no encontrado.")
            return

        session = {
            'user_id': str(user['id']),
            'client_tid': str(client_tid),
            'subscription_id': sub_data['id'],
            'account_id': sub_data['account_id'],
            'profile_id': sub_data['profile_id'],
            'account_email': sub_data['account_email'],
        }
        _redis().setex(_ADMIN_SESSION_KEY.format(tid=caller_tid), _TTL, json.dumps(session))

        kb = [
            [InlineKeyboardButton("🔑 Buscar código Gmail", callback_data=f"hogar:search_gmail:{client_tid}")],
            [InlineKeyboardButton("🚀 Migrar Express", callback_data=f"hogar:do_express:{client_tid}"),
             InlineKeyboardButton("📦 Con Historial", callback_data=f"hogar:do_history:{client_tid}")],
            [InlineKeyboardButton("📋 Ver incidentes", callback_data=f"hogar:view_incidents:{client_tid}")],
        ]
        text = (
            f"📧 Cuenta: `{sub_data['account_email']}`\n"
            f"📅 Vence: {sub_data['end_date'][:10] if sub_data['end_date'] else '—'}"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN,
                                       reply_markup=InlineKeyboardMarkup(kb))

    elif action == "admin_manage":
        if not _is_admin(caller_tid):
            return
        # Soportar tanto telegram_id como uid_ prefijo
        if str(client_tid).startswith("uid_"):
            uid = str(client_tid).replace("uid_", "")
            from database import get_supabase as _gc
            result = _gc().table('users').select('*').eq('id', uid).execute()
            user = result.data[0] if result.data else None
        else:
            from database.users import get_user_by_telegram_id
            user = await get_user_by_telegram_id(int(client_tid))
        if not user:
            await query.edit_message_text(f"❌ Cliente TID {client_tid} no encontrado.")
            return
        await _show_admin_client_panel(query, context, user, caller_tid)

    elif action == "search_gmail":
        if not _is_admin(caller_tid):
            return
        await _admin_search_gmail(query, context, caller_tid, client_tid)

    elif action == "do_express":
        if not _is_admin(caller_tid):
            return
        await _admin_show_profiles_for_express(query, context, caller_tid, client_tid)

    elif action == "do_history":
        if not _is_admin(caller_tid):
            return
        await _admin_create_history_ticket(query, context, caller_tid, client_tid)

    elif action == "select_profile":
        if not _is_admin(caller_tid):
            return
        profile_id = parts[4] if len(parts) > 4 else None
        await _admin_execute_express(query, context, caller_tid, client_tid, profile_id)

    elif action == "history_inprogress":
        if not _is_admin(caller_tid):
            return
        incident_id = _redis().get(f"hogar_incident_admin:{caller_tid}") or None
        try:
            await context.bot.send_message(
                chat_id=client_tid,
                text="✅ Tu solicitud de migración está en proceso. Te notificamos pronto.",
            )
        except Exception:
            pass
        kb = [[InlineKeyboardButton("✅ Completada", callback_data=f"hogar:complete_history:{client_tid}")]]
        await query.edit_message_text(
            "⏳ Caso marcado *En Proceso*. El cliente fue notificado.\n"
            "Cuando termines la migración, pulsa 'Completada'.",
            parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb),
        )

    elif action == "complete_history":
        if not _is_admin(caller_tid):
            return
        # Leer incident_id desde Redis
        incident_id = _redis().get(f"hogar_incident_admin:{caller_tid}") or None
        await _admin_finalize_history(query, context, caller_tid, client_tid, incident_id)

    elif action == "finalize_history":
        if not _is_admin(caller_tid):
            return
        incident_id = _redis().get(f"hogar_incident_admin:{caller_tid}") or None
        profile_id = parts[3] if len(parts) > 3 else None
        await _admin_complete_history_with_profile(query, context, caller_tid,
                                                    client_tid, incident_id, profile_id)

    elif action == "view_incidents":
        if not _is_admin(caller_tid):
            return
        from database.users import get_user_by_telegram_id
        from database.hogar import get_incident_history
        user = await get_user_by_telegram_id(client_tid)
        if not user:
            await query.edit_message_text("❌ Cliente no encontrado.")
            return
        incidents = await get_incident_history(str(user['id']), limit=10)
        if not incidents:
            await query.edit_message_text("📋 Sin incidentes de hogar registrados.")
            return
        labels = {'code_sent': '🔑 Código', 'express': '🚀 Express',
                  'history': '📦 Historial', 'escalated': '🆘 Escalado'}
        text = f"📋 *Historial — {user.get('name', 'Cliente')}*\n\n"
        for i, inc in enumerate(incidents, 1):
            label = labels.get(inc.get('type', ''), '❓')
            date = (inc.get('created_at') or '—')[:10]
            text += f"{i}. {label} — {date}\n"
        kb = [[InlineKeyboardButton("⬅️ Volver", callback_data=f"hogar:admin_manage:{client_tid}")]]
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN,
                                       reply_markup=InlineKeyboardMarkup(kb))

    elif action == "list_page":
        page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        await _show_hogar_client_list(query, context, page=page)

    elif action == "search_mode":
        if not _is_admin(caller_tid):
            return
        _redis().setex(_ADMIN_SEARCH_KEY.format(tid=caller_tid), 300, "1")
        await query.edit_message_text(
            "🔍 Escribe el nombre, teléfono o Telegram ID del cliente:",
            parse_mode=ParseMode.MARKDOWN,
        )

    elif action == "cancel":
        _clear_state(client_tid)
        await query.edit_message_text("❌ Operación cancelada.")


# ════════════════════════════════════════════════════════════════
# COMANDO ADMIN /hogar
# ════════════════════════════════════════════════════════════════

async def cmd_hogar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /hogar — solo admin. Muestra lista de clientes Netflix activos."""
    telegram_id = update.effective_user.id
    if not _is_admin(telegram_id):
        return
    await _show_hogar_client_list(update.message, context, page=0)


async def _show_hogar_client_list(msg_or_query, context, page: int = 0):
    """Muestra lista paginada de clientes con suscripción Netflix activa."""
    from database import get_supabase
    PAGE_SIZE = 8
    try:
        result = get_supabase().table('subscriptions').select(
            'id, end_date,'
            'users!inner(id, name, telegram_id, phone),'
            'profiles!inner(id, profile_name, account_id,'
            '  accounts!inner(email, account_health)),'
            'platforms!inner(name)'
        ).eq('status', 'active').execute()

        subs = [s for s in (result.data or [])
                if 'netflix' in (s.get('platforms', {}).get('name', '') or '').lower()]

        # Deduplicar por telegram_id o user_id — un botón por cliente
        seen_users = set()
        unique_clients = []
        for s in subs:
            user = s.get('users', {})
            tid = user.get('telegram_id')
            uid = str(user.get('id', ''))
            # Usar telegram_id si existe, sino user_id como identificador único
            dedup_key = str(tid) if tid else uid
            if not dedup_key or dedup_key in seen_users:
                continue
            seen_users.add(dedup_key)
            unique_clients.append(s)

        total = len(unique_clients)
        page_clients = unique_clients[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

        kb = []
        for s in page_clients:
            user = s.get('users', {})
            name = user.get('full_name') or user.get('name') or user.get('phone') or 'Sin nombre'
            email = s.get('profiles', {}).get('accounts', {}).get('email', '—')
            tid = user.get('telegram_id', '')
            health = s.get('profiles', {}).get('accounts', {}).get('account_health', 'healthy')
            h_emoji = {'healthy': '🟢', 'warning': '🟡', 'restricted': '🔴'}.get(health, '⚪')
            # Para clientes sin telegram_id usar el user_id en el callback
            uid = str(user.get('id', ''))
            callback_id = str(tid) if tid else f"uid_{uid}"
            kb.append([InlineKeyboardButton(
                f"{h_emoji} {name[:20]} — {email[:22]}",
                callback_data=f"hogar:admin_manage:{callback_id}"
            )])

        # Paginación
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️ Anterior", callback_data=f"hogar:list_page:{page - 1}"))
        if (page + 1) * PAGE_SIZE < total:
            nav.append(InlineKeyboardButton("Siguiente ➡️", callback_data=f"hogar:list_page:{page + 1}"))
        if nav:
            kb.append(nav)

        kb.append([InlineKeyboardButton("🔍 Buscar por nombre", callback_data="hogar:search_mode")])

        total_pages = max(1, -(-total // PAGE_SIZE))
        text = (
            f"🏠 *Soporte Hogar Netflix — Admin*\n\n"
            f"Clientes Netflix activos: {total}\n"
            f"Página {page + 1}/{total_pages}\n\n"
            f"Selecciona un cliente:"
        )

        if hasattr(msg_or_query, 'edit_message_text'):
            await msg_or_query.edit_message_text(
                text, parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(kb)
            )
        else:
            await msg_or_query.reply_text(
                text, parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(kb)
            )
    except Exception as e:
        logger.error(f"[hogar] _show_hogar_client_list: {e}", exc_info=True)
        if hasattr(msg_or_query, 'reply_text'):
            await msg_or_query.reply_text("❌ Error cargando lista de clientes.")
        elif hasattr(msg_or_query, 'edit_message_text'):
            await msg_or_query.edit_message_text("❌ Error cargando lista de clientes.")


async def handle_hogar_admin_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Procesa la búsqueda de cliente del admin. Retorna True si lo manejó."""
    telegram_id = update.effective_user.id
    if not _is_admin(telegram_id):
        return False
    r = _redis()
    if not r.get(_ADMIN_SEARCH_KEY.format(tid=telegram_id)):
        return False
    r.delete(_ADMIN_SEARCH_KEY.format(tid=telegram_id))

    from database.users import search_users
    users = await search_users(update.message.text.strip())
    if not users:
        await update.message.reply_text("❌ No encontré clientes con ese criterio.")
        return True
    if len(users) == 1:
        await _show_admin_client_panel(update.message, context, users[0], telegram_id)
    else:
        kb = []
        for u in users[:10]:
            name = u.get('name') or u.get('username') or f"ID:{str(u['id'])[:8]}"
            tid = u.get('telegram_id', '')
            kb.append([InlineKeyboardButton(name, callback_data=f"hogar:admin_manage:{tid}")])
        await update.message.reply_text(
            f"Encontré {len(users)} clientes. ¿Cuál?",
            reply_markup=InlineKeyboardMarkup(kb),
        )
    return True


async def _show_admin_client_panel(msg_or_query, context, user: dict, admin_tid: int):
    logger.info(f"[hogar] _show_admin_client_panel user={user.get('id')} admin={admin_tid}")
    from database.hogar import get_netflix_subscription_for_user, get_incident_history
    try:
        client_tid = user.get('telegram_id', '')
        subs = await get_netflix_subscription_for_user(str(user['id']))
        incidents = await get_incident_history(str(user['id']))

        if not subs:
            text = f"👤 *{user.get('name', 'Cliente')}*\n\n❌ Sin suscripción Netflix activa."
        elif len(subs) == 1:
            sub = subs[0]
            account = sub['profiles']['accounts']
            h_emoji = {'healthy': '🟢', 'warning': '🟡', 'restricted': '🔴'}.get(
                account.get('account_health', 'healthy'), '⚪'
            )
            text = (
                f"👤 *{user.get('name', 'Cliente')}*\n"
                f"📧 Cuenta: `{account.get('email', '—')}`\n"
                f"🏥 Salud: {h_emoji} {account.get('account_health', '—').capitalize()} "
                f"({account.get('household_incidents', 0)} incidentes)\n"
                f"📅 Vence: {sub.get('end_date', '—')}\n"
                f"🔁 Incidentes totales: {len(incidents)}"
            )
            session = {
                'user_id': str(user['id']),
                'client_tid': client_tid,
                'subscription_id': str(sub['id']),
                'account_id': str(account['id']),
                'profile_id': str(sub['profiles']['id']),
                'account_email': account.get('email', ''),
            }
            _redis().setex(_ADMIN_SESSION_KEY.format(tid=admin_tid), _TTL, json.dumps(session))
        else:
            # Cliente con múltiples suscripciones Netflix — mostrar selector
            text = f"👤 *{user.get('name', 'Cliente')}*\n\n⚠️ Tiene {len(subs)} suscripciones Netflix activas.\nSelecciona cuál gestionar:"
            # Guardar lista de subs en Redis indexada para no superar 64 bytes en callback
            subs_index = [{'id': str(s['id']), 'account_id': str(s['profiles']['accounts']['id']),
                           'profile_id': str(s['profiles']['id']),
                           'account_email': s['profiles']['accounts'].get('email', ''),
                           'end_date': s.get('end_date', '—')} for s in subs]
            _redis().setex(f"hogar_subs:{admin_tid}", _TTL, json.dumps(subs_index))

            kb = []
            for i, sub in enumerate(subs):
                account = sub['profiles']['accounts']
                end = sub.get('end_date', '—')[:10]
                kb.append([InlineKeyboardButton(
                    f"📧 {account.get('email', '—')[:30]} · vence {end}",
                    callback_data=f"hogar:select_sub:{client_tid}:{i}"
                )])
            kb.append([InlineKeyboardButton("⬅️ Volver", callback_data=f"hogar:cancel:{client_tid}")])
            if hasattr(msg_or_query, 'edit_message_text'):
                await msg_or_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN,
                                                      reply_markup=InlineKeyboardMarkup(kb))
            else:
                await msg_or_query.reply_text(text, parse_mode=ParseMode.MARKDOWN,
                                               reply_markup=InlineKeyboardMarkup(kb))
            return

        kb = [
            [InlineKeyboardButton("🔑 Buscar código Gmail", callback_data=f"hogar:search_gmail:{client_tid}")],
            [InlineKeyboardButton("🚀 Migrar Express", callback_data=f"hogar:do_express:{client_tid}"),
             InlineKeyboardButton("📦 Con Historial", callback_data=f"hogar:do_history:{client_tid}")],
            [InlineKeyboardButton("📋 Ver incidentes", callback_data=f"hogar:view_incidents:{client_tid}")],
        ]
        if hasattr(msg_or_query, 'edit_message_text'):
            await msg_or_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN,
                                                  reply_markup=InlineKeyboardMarkup(kb))
        else:
            await msg_or_query.reply_text(text, parse_mode=ParseMode.MARKDOWN,
                                           reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        logger.error(f"[hogar] _show_admin_client_panel error: {e}", exc_info=True)


async def _admin_search_gmail(query, context, admin_tid: int, client_tid: int):
    r = _redis()
    session_raw = r.get(_ADMIN_SESSION_KEY.format(tid=admin_tid))
    if not session_raw:
        await query.edit_message_text("❌ Sesión expirada. Usa /hogar de nuevo.")
        return
    session = json.loads(session_raw)
    account_email = session.get('account_email', '')
    await query.edit_message_text("⏳ Buscando en Gmail maestro...")
    from services.gmail_service import get_netflix_access_code
    result = await get_netflix_access_code(account_email, os.environ.get("GMAIL_MASTER_CREDENTIALS_JSON", ""))
    if result.get('type'):
        if result['type'] == 'code':
            wa_msg = f"Hola! Tu código Netflix es: *{result['value']}*\nTienes 15 min para introducirlo en tu TV."
            await query.edit_message_text(
                f"✅ *Código encontrado*\n\n🔢 Código: `{result['value']}`\n\n📋 *Para WhatsApp:*\n`{wa_msg}`",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            wa_msg = (f"Hola! Aquí tu link Netflix:\n\n{result['value']}\n\n"
                      f"Tienes 15 min. Haz clic, copia el código e introdúcelo en tu TV.")
            kb = [[InlineKeyboardButton("🔗 Abrir link", url=result['value'])]]
            await query.edit_message_text(
                f"✅ *Link encontrado*\n\n🔗 {result['value']}\n\n📋 *Para WhatsApp:*\n`{wa_msg}`",
                parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb),
            )
    else:
        kb = [
            [InlineKeyboardButton("🔄 Buscar de nuevo", callback_data=f"hogar:search_gmail:{client_tid}")],
            [InlineKeyboardButton("🚀 Migrar Express", callback_data=f"hogar:do_express:{client_tid}")],
        ]
        await query.edit_message_text(
            "⚠️ No encontré email reciente de Netflix.\n"
            "El cliente debe primero pulsar 'Estoy de viaje' → 'Enviar email' en su TV.",
            reply_markup=InlineKeyboardMarkup(kb),
        )


async def _admin_show_profiles_for_express(query, context, admin_tid: int, client_tid: int):
    r = _redis()
    session_raw = r.get(_ADMIN_SESSION_KEY.format(tid=admin_tid))
    if not session_raw:
        await query.edit_message_text("❌ Sesión expirada. Usa /hogar de nuevo.")
        return
    session = json.loads(session_raw)
    from database.hogar import get_available_profiles_for_migration
    available = await get_available_profiles_for_migration(session['user_id'], session.get('account_id'))
    if not available:
        await query.edit_message_text("❌ No hay perfiles disponibles para migración en este momento.")
        return
    kb = []
    for p in available[:6]:
        email = p.get('accounts', {}).get('email', '—')
        health = p.get('accounts', {}).get('account_health', 'healthy')
        h_e = {'healthy': '🟢', 'warning': '🟡'}.get(health, '⚪')
        kb.append([InlineKeyboardButton(
            f"{h_e} {email[:28]} — {p.get('profile_name', '—')}",
            callback_data=f"hogar:select_profile:{client_tid}:auto:{p['id']}"
        )])
    kb.append([InlineKeyboardButton("❌ Cancelar", callback_data=f"hogar:cancel:{client_tid}")])
    await query.edit_message_text("🚀 *Selecciona perfil destino:*",
                                   parse_mode=ParseMode.MARKDOWN,
                                   reply_markup=InlineKeyboardMarkup(kb))


async def _admin_execute_express(query, context, admin_tid: int, client_tid: int, profile_id: str):
    r = _redis()
    session_raw = r.get(_ADMIN_SESSION_KEY.format(tid=admin_tid))
    if not session_raw or not profile_id:
        await query.edit_message_text("❌ Error al ejecutar migración.")
        return
    session = json.loads(session_raw)
    from database import get_supabase
    p_result = get_supabase().table('profiles').select(
        'id, profile_name, pin, account_id, accounts!inner(id, email, password, account_health)'
    ).eq('id', profile_id).execute()
    if not p_result.data:
        await query.edit_message_text("❌ Perfil no encontrado.")
        return
    await query.edit_message_text("⚡ Ejecutando migración express...")
    success = await _execute_express_migration(context, session, p_result.data[0], int(client_tid))
    if success:
        await query.edit_message_text("✅ Migración express completada. El cliente fue notificado.")
    else:
        await query.edit_message_text("❌ Error en la migración. Revisa los logs.")


async def _admin_create_history_ticket(query, context, admin_tid: int, client_tid: int):
    r = _redis()
    session_raw = r.get(_ADMIN_SESSION_KEY.format(tid=admin_tid))
    if not session_raw:
        await query.edit_message_text("❌ Sesión expirada.")
        return
    session = json.loads(session_raw)
    from database.hogar import create_incident, update_account_health
    incident = await create_incident(
        user_id=session['user_id'], account_id=session['account_id'],
        profile_id=session['profile_id'], subscription_id=session['subscription_id'],
        stage='second_warning', incident_type='history',
    )
    await update_account_health(session['account_id'])
    incident_id = incident['id'] if incident else 'N/A'
    try:
        await context.bot.send_message(
            chat_id=int(client_tid),
            text="⏳ Tu migración con historial está en proceso. Te notificamos cuando esté lista.",
        )
    except Exception:
        pass
    # Guardar incident_id en Redis para no superar 64 bytes en callback
    _redis().setex(f"hogar_incident_admin:{admin_tid}", _TTL, str(incident_id))
    kb = [
        [InlineKeyboardButton("⏳ En proceso", callback_data=f"hogar:history_inprogress:{client_tid}")],
        [InlineKeyboardButton("✅ Completada", callback_data=f"hogar:complete_history:{client_tid}")],
    ]
    await query.edit_message_text(
        f"📦 *Ticket — Migración con Historial*\n\n"
        f"👤 Cliente TID: `{client_tid}`\n"
        f"🆔 Incidente: `{incident_id}`\n\n"
        f"Cuando completes la migración manualmente en Netflix, pulsa 'Completada'.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def _admin_finalize_history(query, context, admin_tid: int, client_tid: int, incident_id: str):
    """Muestra perfiles disponibles para seleccionar el destino de la migración con historial."""
    r = _redis()
    session_raw = r.get(_ADMIN_SESSION_KEY.format(tid=admin_tid))
    if not session_raw:
        await query.edit_message_text("❌ Sesión expirada. Usa /hogar de nuevo.")
        return
    session = json.loads(session_raw)
    from database.hogar import get_available_profiles_for_migration
    available = await get_available_profiles_for_migration(
        session.get('user_id', ''), session.get('account_id')
    )
    kb = []
    for p in available[:6]:
        email = p.get('accounts', {}).get('email', '—')
        health = p.get('accounts', {}).get('account_health', 'healthy')
        h_e = {'healthy': '🟢', 'warning': '🟡'}.get(health, '⚪')
        kb.append([InlineKeyboardButton(
            f"{h_e} {email[:28]} — {p.get('profile_name', '—')}",
            callback_data=f"hogar:finalize_history:{client_tid}:{p['id']}"
        )])
    kb.append([InlineKeyboardButton(
        "✅ Sin cambio de perfil (notificar solo)",
        callback_data=f"hogar:finalize_history:{client_tid}:none"
    )])
    await query.edit_message_text(
        "📦 *Selecciona perfil destino para migración con historial:*\n\n"
        "_(El historial ya fue transferido manualmente en Netflix)_",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def _admin_complete_history_with_profile(query, context, admin_tid: int,
                                                client_tid: int, incident_id: str, profile_id: str):
    """Completa la migración con historial: asigna perfil en BD y notifica al cliente."""
    r = _redis()
    session_raw = r.get(_ADMIN_SESSION_KEY.format(tid=admin_tid))
    session = json.loads(session_raw) if session_raw else {}

    from database.hogar import update_incident, update_account_health
    from database import get_supabase

    if profile_id and profile_id != 'none':
        p_result = get_supabase().table('profiles').select(
            'id, profile_name, pin, account_id, accounts!inner(id, email, account_health)'
        ).eq('id', profile_id).execute()
        if not p_result.data:
            await query.edit_message_text("❌ Perfil no encontrado.")
            return
        target = p_result.data[0]

        from database.profiles import assign_profile, release_profile
        await assign_profile(profile_id)
        if session.get('profile_id'):
            await release_profile(session['profile_id'])

        subscription_id = session.get('subscription_id')
        if subscription_id:
            get_supabase().table('subscriptions').update(
                {'profile_id': profile_id}
            ).eq('id', str(subscription_id)).execute()

        new_email = target.get('accounts', {}).get('email', '—')
        new_name = target.get('profile_name', '—')
        new_pin = target.get('pin', '—')

        if incident_id and incident_id != 'N/A':
            await update_incident(incident_id,
                new_profile_id=profile_id,
                new_account_id=target.get('account_id'),
                resolved=True,
                resolved_at=datetime.now(timezone.utc).isoformat(),
            )
        if session.get('account_id'):
            await update_account_health(session['account_id'])

        client_msg = (
            f"🎉 *¡Tu migración con historial está lista!*\n\n"
            f"📧 Email: `{new_email}`\n👤 Perfil: {new_name}\n🔢 PIN: `{new_pin}`\n\n"
            f"Cierra sesión en tu TV e inicia con estas credenciales."
        )
    else:
        if incident_id and incident_id != 'N/A':
            await update_incident(incident_id, resolved=True,
                resolved_at=datetime.now(timezone.utc).isoformat())
        client_msg = "✅ Tu migración con historial fue completada. Usa tus mismas credenciales."

    kb = [
        [InlineKeyboardButton("✅ Ya funciona", callback_data=f"hogar:resolved:{client_tid}")],
        [InlineKeyboardButton("❌ Sigo con problemas", callback_data=f"hogar:not_resolved:{client_tid}")],
    ]
    try:
        await context.bot.send_message(
            chat_id=int(client_tid),
            text=client_msg,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(kb),
        )
    except Exception:
        pass

    await query.edit_message_text(
        f"✅ *Migración con historial completada*\n\nCliente TID `{client_tid}` notificado.",
        parse_mode=ParseMode.MARKDOWN,
    )
