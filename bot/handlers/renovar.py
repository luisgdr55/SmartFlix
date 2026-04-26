"""Módulo de renovación manual para admin (/renovar).

Flujo:
  PASO 0: /renovar → lista paginada de todos los clientes
  PASO 1: Admin selecciona cliente → lista sus subs activas/expiradas
          Si 1 → directo al confirm
          Si 2+ → botones para elegir cuál renovar
  PASO 2: Confirmar → resumen con cliente, plataforma, fecha actual y nueva
  PASO 3: Ejecutar → confirm_renewal_subscription + ticket + notif cliente
"""
from __future__ import annotations

import logging
from datetime import timedelta, datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from bot.messages import ACCESS_INSTRUCTIONS
from config import settings
from database.accounts import get_account_by_id
from database.platforms import get_platform_by_id
from database.profiles import get_profile_by_id
from database.subscriptions import confirm_renewal_subscription
from utils.helpers import venezuela_now, format_datetime_vzla, short_id
from utils.validators import is_admin

logger = logging.getLogger(__name__)

_KEY = "renovar_session"
_PAGE_SIZE = 10


def _check_admin(telegram_id: int) -> bool:
    return is_admin(telegram_id, settings.ADMIN_TELEGRAM_IDS)


def _session(context: ContextTypes.DEFAULT_TYPE) -> dict:
    if _KEY not in context.user_data:
        context.user_data[_KEY] = {}
    return context.user_data[_KEY]


def _clear_session(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(_KEY, None)


# ─────────────────────────────────────────────────────────────────────────────
# KEYBOARDS
# ─────────────────────────────────────────────────────────────────────────────

def _clients_keyboard(clients: list[dict], page: int, total: int) -> InlineKeyboardMarkup:
    buttons = []
    for c in clients:
        display = c.get("name") or c.get("username") or f"ID:{str(c['id'])[:8]}"
        contact = c.get("phone") or ""
        label = f"👤 {display}" + (f" — {contact}" if contact else "")
        buttons.append([InlineKeyboardButton(label, callback_data=f"renovar:cliente:{c['id']}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Anterior", callback_data=f"renovar:page:{page - 1}"))
    if (page + 1) * _PAGE_SIZE < total:
        nav.append(InlineKeyboardButton("Siguiente ➡️", callback_data=f"renovar:page:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("❌ Cancelar", callback_data="renovar:cancel")])
    return InlineKeyboardMarkup(buttons)


def _subs_keyboard(subs: list[dict]) -> InlineKeyboardMarkup:
    buttons = []
    for s in subs:
        platform = s.get("platforms") or {}
        icon = platform.get("icon_emoji", "📺")
        name = platform.get("name", "")
        end = (s.get("end_date") or "")[:10]
        status_icon = "✅" if s.get("status") == "active" else "❌"
        buttons.append([InlineKeyboardButton(
            f"{status_icon} {icon} {name} — vence {end}",
            callback_data=f"renovar:sub:{s['id']}",
        )])
    buttons.append([InlineKeyboardButton("❌ Cancelar", callback_data="renovar:cancel")])
    return InlineKeyboardMarkup(buttons)


def _confirm_keyboard(sub_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirmar renovación", callback_data=f"renovar:confirm:{sub_id}"),
        InlineKeyboardButton("❌ Cancelar", callback_data="renovar:cancel"),
    ]])


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _calc_new_end_date(end_date_str: str, plan_type: str) -> datetime:
    """Replica la lógica de admin.py approve_payment:
    extiende desde la end_date original a las 23:59:59, no desde hoy."""
    now = venezuela_now()
    days = 30 if plan_type == "monthly" else 1
    try:
        y, m, d = map(int, end_date_str[:10].split("-"))
        base = now.replace(year=y, month=m, day=d, hour=23, minute=59, second=59, microsecond=0)
    except Exception:
        base = now
    return base + timedelta(days=days)


def _store_sub_in_session(sess: dict, sub: dict) -> None:
    """Cache los campos necesarios del sub en la sesión para el paso de ejecución."""
    profile = sub.get("profiles") or {}
    sess["sub_id"] = str(sub["id"])
    sess["platform_id"] = str(sub.get("platform_id", ""))
    sess["plan_type"] = sub.get("plan_type", "monthly")
    sess["profile_id"] = str(sub.get("profile_id") or profile.get("id") or "")
    sess["end_date"] = sub.get("end_date", "")
    sess["step"] = "confirmar"


def _build_confirm_text(sub: dict, sess: dict) -> str:
    platform = sub.get("platforms") or {}
    profile = sub.get("profiles") or {}
    plan_type = sub.get("plan_type", "monthly")
    end_date_str = sub.get("end_date", "")
    new_end = _calc_new_end_date(end_date_str, plan_type)
    plan_label = "Mensual (30 días)" if plan_type == "monthly" else "Express (24h)"
    platform_label = f"{platform.get('icon_emoji','')} {platform.get('name','')}".strip()
    return (
        f"📋 <b>Confirmar Renovación</b>\n\n"
        f"👤 <b>Cliente:</b> {sess.get('nombre', '')}\n"
        f"📞 <b>Contacto:</b> {sess.get('telefono', '')}\n"
        f"📺 <b>Plataforma:</b> {platform_label}\n"
        f"📅 <b>Plan:</b> {plan_label}\n"
        f"👤 <b>Perfil:</b> {profile.get('profile_name','N/A')} — PIN: {profile.get('pin','—')}\n"
        f"📆 <b>Vence actualmente:</b> {end_date_str[:10]}\n"
        f"✅ <b>Nueva fecha:</b> {format_datetime_vzla(new_end)}\n\n"
        f"¿Confirmas la renovación?"
    )


async def _show_client_list(query, context: ContextTypes.DEFAULT_TYPE, page: int) -> None:
    from database.users import get_all_clients_for_admin
    try:
        clients, total = await get_all_clients_for_admin(page=page, page_size=_PAGE_SIZE)
        if not clients and page == 0:
            await query.edit_message_text("❌ No hay clientes registrados en el sistema.")
            _clear_session(context)
            return
        total_pages = max((total + _PAGE_SIZE - 1) // _PAGE_SIZE, 1)
        _session(context)["client_page"] = page
        await query.edit_message_text(
            f"🔄 <b>Renovación Manual — Selecciona el cliente</b>\n"
            f"Página {page + 1}/{total_pages} — {total} clientes en total",
            parse_mode="HTML",
            reply_markup=_clients_keyboard(clients, page, total),
        )
    except Exception as e:
        logger.error(f"Error showing client list in renovar: {e}")
        await query.edit_message_text("❌ Error al cargar clientes. Intenta de nuevo.")
        _clear_session(context)


# ─────────────────────────────────────────────────────────────────────────────
# COMMAND ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_renovar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Iniciar flujo de renovación manual. Comando: /renovar"""
    if not update.message or not update.effective_user:
        return
    if not _check_admin(update.effective_user.id):
        await update.message.reply_text("❌ Sin permisos de administrador.")
        return
    _clear_session(context)
    _session(context)["step"] = "cliente_list"

    from database.users import get_all_clients_for_admin
    try:
        clients, total = await get_all_clients_for_admin(page=0, page_size=_PAGE_SIZE)
        if not clients:
            await update.message.reply_text("❌ No hay clientes registrados en el sistema.")
            return
        total_pages = max((total + _PAGE_SIZE - 1) // _PAGE_SIZE, 1)
        await update.message.reply_text(
            f"🔄 <b>Renovación Manual — Selecciona el cliente</b>\n"
            f"Página 1/{total_pages} — {total} clientes en total",
            parse_mode="HTML",
            reply_markup=_clients_keyboard(clients, 0, total),
        )
    except Exception as e:
        logger.error(f"Error in cmd_renovar: {e}")
        await update.message.reply_text("❌ Error al cargar clientes.")


# ─────────────────────────────────────────────────────────────────────────────
# CALLBACK QUERY HANDLER
# ─────────────────────────────────────────────────────────────────────────────

async def handle_renovar_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not update.effective_user:
        return
    await query.answer()

    if not _check_admin(update.effective_user.id):
        await query.answer("Sin permisos.", show_alert=True)
        return

    data = query.data or ""
    sess = _session(context)

    # ── CANCEL ──────────────────────────────────────────────────────────────
    if data == "renovar:cancel":
        _clear_session(context)
        await query.edit_message_text("❌ Renovación cancelada.")
        return

    # ── PAGINATION ───────────────────────────────────────────────────────────
    if data.startswith("renovar:page:"):
        page = int(data.split(":")[-1])
        await _show_client_list(query, context, page=page)
        return

    # ── CLIENT SELECTION ─────────────────────────────────────────────────────
    if data.startswith("renovar:cliente:"):
        user_id = data.split(":", 2)[2]
        try:
            from database.users import get_user_by_id
            from database.subscriptions import get_renewable_subscriptions_by_user

            user = await get_user_by_id(user_id)
            if not user:
                await query.answer("Cliente no encontrado.", show_alert=True)
                return

            subs = await get_renewable_subscriptions_by_user(user_id)
            if not subs:
                nombre = user.get("name") or user.get("username") or "Sin nombre"
                await query.edit_message_text(
                    f"❌ <b>{nombre}</b> no tiene suscripciones activas ni expiradas para renovar.",
                    parse_mode="HTML",
                )
                _clear_session(context)
                return

            sess["user_id"] = user_id
            sess["nombre"] = user.get("name") or user.get("username") or f"ID:{user_id[:8]}"
            sess["telefono"] = user.get("phone") or "Sin contacto"
            sess["telegram_id"] = user.get("telegram_id")

            if len(subs) == 1:
                sub = subs[0]
                _store_sub_in_session(sess, sub)
                text = _build_confirm_text(sub, sess)
                await query.edit_message_text(text, parse_mode="HTML",
                                              reply_markup=_confirm_keyboard(str(sub["id"])))
            else:
                sess["step"] = "sub_select"
                await query.edit_message_text(
                    f"👤 <b>Cliente:</b> {sess['nombre']}\n\n"
                    f"Tiene {len(subs)} suscripción(es). ¿Cuál deseas renovar?",
                    parse_mode="HTML",
                    reply_markup=_subs_keyboard(subs),
                )
        except Exception as e:
            logger.error(f"Error in renovar cliente selection: {e}")
            await query.edit_message_text("❌ Error al cargar suscripciones.")
            _clear_session(context)
        return

    # ── SUB SELECTION ────────────────────────────────────────────────────────
    if data.startswith("renovar:sub:"):
        sub_id = data.split(":", 2)[2]
        try:
            from database.subscriptions import get_subscription_by_id
            sub = await get_subscription_by_id(sub_id)
            if not sub:
                await query.answer("Suscripción no encontrada.", show_alert=True)
                return
            _store_sub_in_session(sess, sub)
            text = _build_confirm_text(sub, sess)
            await query.edit_message_text(text, parse_mode="HTML",
                                          reply_markup=_confirm_keyboard(sub_id))
        except Exception as e:
            logger.error(f"Error in renovar sub selection: {e}")
            await query.edit_message_text("❌ Error al cargar la suscripción.")
            _clear_session(context)
        return

    # ── CONFIRM ──────────────────────────────────────────────────────────────
    if data.startswith("renovar:confirm:"):
        sub_id = data.split(":", 2)[2]
        await _execute_renewal(query, context, update.effective_user.id, sub_id)
        return


# ─────────────────────────────────────────────────────────────────────────────
# CORE RENEWAL EXECUTION
# ─────────────────────────────────────────────────────────────────────────────

async def _execute_renewal(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    admin_telegram_id: int,
    sub_id: str,
) -> None:
    """Ejecutar renovación: actualizar BD, generar ticket, notificar cliente si tiene Telegram."""
    from database.users import log_admin_action

    sess = _session(context)
    nombre = sess.get("nombre", "")
    contacto = sess.get("telefono", "")
    client_telegram_id = sess.get("telegram_id")
    profile_id = sess.get("profile_id", "")
    platform_id = sess.get("platform_id", "")
    plan_type = sess.get("plan_type", "monthly")
    end_date_str = sess.get("end_date", "")

    if not all([sub_id, profile_id, platform_id, end_date_str]):
        await query.edit_message_text("❌ Sesión expirada o incompleta. Inicia de nuevo con /renovar")
        _clear_session(context)
        return

    await query.edit_message_text("⏳ Procesando renovación...")

    try:
        new_end_date = _calc_new_end_date(end_date_str, plan_type)

        ok = await confirm_renewal_subscription(
            sub_id=sub_id,
            profile_id=profile_id,
            payment_reference=f"MANUAL-RENEW-{short_id(sub_id)}",
            new_end_date=new_end_date,
        )
        if not ok:
            await query.edit_message_text("❌ Error al renovar la suscripción. Intenta de nuevo.")
            _clear_session(context)
            return

        # Obtener credenciales para el ticket
        profile = await get_profile_by_id(profile_id) or {}
        account_id = str(profile.get("account_id", ""))
        account = await get_account_by_id(account_id) if account_id else None
        platform = await get_platform_by_id(platform_id)
        platform_label = f"{(platform or {}).get('icon_emoji','')} {(platform or {}).get('name','')}".strip()
        platform_slug = (platform or {}).get("slug", "")
        instructions = ACCESS_INSTRUCTIONS.get(platform_slug, "Ingresa con el email y contraseña proporcionados.")
        plan_label = "Mensual (30 días)" if plan_type == "monthly" else "Express (24h)"

        await log_admin_action(admin_telegram_id, "renovar_manual", {
            "cliente": nombre,
            "contacto": contacto,
            "plataforma": platform_slug,
            "plan": plan_type,
            "sub_id": sub_id,
            "new_end_date": new_end_date.isoformat(),
        })

        _clear_session(context)

        email_line = account.get("email", "N/A") if account else "⚠️ Ver manualmente"
        pass_line = account.get("password", "N/A") if account else "⚠️ Ver manualmente"
        pin_val = profile.get("pin", "—")
        profile_name = profile.get("profile_name", "N/A")

        ticket = (
            f"═══════════════════════════\n"
            f"🎬 <b>ACCESO STREAMVIP</b>\n"
            f"═══════════════════════════\n"
            f"👤 <b>Cliente:</b> {nombre}\n"
            f"📱 <b>Contacto:</b> {contacto}\n"
            f"🎭 <b>Plataforma:</b> {platform_label}\n"
            f"📅 <b>Plan:</b> {plan_label}\n"
            f"📆 <b>Vence:</b> {format_datetime_vzla(new_end_date)}\n\n"
            f"🔐 <b>DATOS DE ACCESO:</b>\n"
            f"📧 <b>Email:</b> <code>{email_line}</code>\n"
            f"🔑 <b>Contraseña:</b> <code>{pass_line}</code>\n"
            f"👤 <b>Perfil:</b> {profile_name}\n"
            f"🔢 <b>PIN:</b> <code>{pin_val}</code>\n\n"
            f"📋 <b>Instrucciones:</b>\n{instructions}\n"
            f"═══════════════════════════\n"
            f"✅ <i>Generado por SMARTFLIXVE_BOT</i>"
        )

        await query.edit_message_text(ticket, parse_mode="HTML")

        if client_telegram_id:
            follow_up = (
                "📋 <i>Copia el ticket de arriba para enviárselo al cliente.</i>\n"
                "✅ <i>El cliente también fue notificado automáticamente por Telegram.</i>"
            )
        else:
            follow_up = (
                "📋 <i>Copia el ticket de arriba para enviárselo al cliente.</i>\n"
                "⚠️ <i>Este cliente no tiene Telegram — envíalo manualmente.</i>"
            )
        await query.message.reply_text(follow_up, parse_mode="HTML")

        # Notificar al cliente directamente si tiene Telegram
        if client_telegram_id:
            try:
                await context.bot.send_message(
                    chat_id=client_telegram_id,
                    text=ticket,
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.warning(f"Could not send renewal ticket to client {client_telegram_id}: {e}")

    except Exception as e:
        logger.error(f"Error in _execute_renewal: {e}")
        await query.edit_message_text(f"❌ Error inesperado: {e}\n\nIntenta de nuevo con /renovar")
        _clear_session(context)
