"""Módulo de afiliación manual para admin.

Permite al admin registrar clientes que se concretaron por otra vía
(WhatsApp, llamada, etc.) y no tienen Telegram o no pudieron usar el bot.

El pago ya fue confirmado externamente. Este módulo:
  1. Solicita datos del cliente (nombre, teléfono/contacto)
  2. Permite seleccionar plan y plataforma
  3. Asigna credenciales disponibles
  4. Crea el registro completo (usuario + suscripción activa) en la BD
  5. Muestra las credenciales al admin para que las entregue al cliente

Para notificaciones de vencimiento de estos clientes externos, el bot
notifica al admin (en lugar del cliente) con todos los datos para que el
admin le avise por el medio alternativo que corresponda.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from bot.middleware import (
    set_user_state, clear_user_state,
    get_user_data, set_user_data,
)
from bot.messages import ACCESS_INSTRUCTIONS, PIN_LINE
from config import settings
from database.accounts import get_account_by_id
from database.platforms import get_active_platforms, get_platform_by_id
from database.profiles import get_available_profiles, assign_profile
from database.analytics import get_platform_availability
from services.exchange_service import calculate_price_bs, get_current_rate
from utils.helpers import venezuela_now, format_datetime_vzla, short_id
from utils.validators import is_admin

logger = logging.getLogger(__name__)


def _check_admin(telegram_id: int) -> bool:
    return is_admin(telegram_id, settings.ADMIN_TELEGRAM_IDS)


def _plan_select_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 Mensual (30 días)", callback_data="afiliar:plan:monthly")],
        [InlineKeyboardButton("⚡ Express (24h)", callback_data="afiliar:plan:express")],
        [InlineKeyboardButton("❌ Cancelar", callback_data="afiliar:cancel")],
    ])


def _platforms_keyboard(platforms: list[dict], plan_type: str) -> InlineKeyboardMarkup:
    buttons = []
    for p in platforms:
        icon = p.get("icon_emoji", "📺")
        name = p.get("name", "")
        pid = str(p["id"])
        buttons.append([InlineKeyboardButton(f"{icon} {name}", callback_data=f"afiliar:platform:{pid}")])
    buttons.append([InlineKeyboardButton("❌ Cancelar", callback_data="afiliar:cancel")])
    return InlineKeyboardMarkup(buttons)


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirmar y afiliar", callback_data="afiliar:confirm"),
            InlineKeyboardButton("❌ Cancelar", callback_data="afiliar:cancel"),
        ]
    ])


# ─────────────────────────────────────────────────────────────────────────────
# COMMAND ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_afiliar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Iniciar flujo de afiliación manual. Comando: /afiliar"""
    if not update.message or not update.effective_user:
        return
    telegram_id = update.effective_user.id

    if not _check_admin(telegram_id):
        await update.message.reply_text("❌ Sin permisos de administrador.")
        return

    set_user_state(telegram_id, "admin:afiliar:nombre")
    await update.message.reply_text(
        "👤 <b>Afiliación Manual de Cliente</b>\n\n"
        "Registra un cliente que se concretó por otra vía (WhatsApp, llamada, etc.).\n"
        "El pago ya fue confirmado externamente — solo registramos y asignamos credenciales.\n\n"
        "Las notificaciones de vencimiento de este cliente llegarán a ti (admin) para "
        "que se las notifiques por el medio que corresponda.\n\n"
        "─────────────────────\n"
        "📝 Paso 1/3 — ¿Cuál es el <b>nombre completo</b> del cliente?",
        parse_mode="HTML",
    )


# ─────────────────────────────────────────────────────────────────────────────
# TEXT INPUT HANDLER (called from _text_message_router in main.py)
# ─────────────────────────────────────────────────────────────────────────────

async def handle_afiliar_text(update: Update, context: ContextTypes.DEFAULT_TYPE, state: str) -> None:
    """Procesar texto en los pasos de afiliación que requieren texto."""
    if not update.message or not update.effective_user:
        return
    telegram_id = update.effective_user.id
    text = update.message.text.strip()

    if state == "admin:afiliar:nombre":
        if len(text) < 2:
            await update.message.reply_text("❌ Nombre muy corto. Ingresa el nombre completo:")
            return

        set_user_data(telegram_id, "afiliar_nombre", text)
        set_user_state(telegram_id, "admin:afiliar:telefono")
        await update.message.reply_text(
            f"✅ Nombre: <b>{text}</b>\n\n"
            f"📞 Paso 2/3 — ¿Cuál es el <b>teléfono o contacto</b> del cliente?\n"
            f"<i>(Ej: 0414-1234567, correo, Instagram, etc.)</i>",
            parse_mode="HTML",
        )

    elif state == "admin:afiliar:telefono":
        if len(text) < 4:
            await update.message.reply_text("❌ Contacto muy corto. Ingresa el teléfono u otro dato de contacto:")
            return

        set_user_data(telegram_id, "afiliar_telefono", text)
        set_user_state(telegram_id, "admin:afiliar:plan")

        await update.message.reply_text(
            f"✅ Contacto: <b>{text}</b>\n\n"
            f"📋 Paso 3/3 — Selecciona el <b>tipo de plan</b>:",
            parse_mode="HTML",
            reply_markup=_plan_select_keyboard(),
        )

    elif state in ("admin:afiliar:plan", "admin:afiliar:plataforma", "admin:afiliar:confirmar"):
        # Admin is in a button-click step — remind them to use the buttons
        nombre = get_user_data(telegram_id, "afiliar_nombre") or ""
        step_msgs = {
            "admin:afiliar:plan": ("Selecciona el tipo de plan usando los botones de arriba.", _plan_select_keyboard()),
            "admin:afiliar:plataforma": ("Selecciona la plataforma usando los botones de arriba.", None),
            "admin:afiliar:confirmar": ("Usa los botones para confirmar o cancelar la afiliación.", _confirm_keyboard()),
        }
        hint, kb = step_msgs.get(state, ("Usa los botones para continuar.", None))
        await update.message.reply_text(
            f"👆 <i>{hint}</i>\n\nSi deseas cancelar y empezar de nuevo: /afiliar",
            parse_mode="HTML",
            reply_markup=kb,
        )


# ─────────────────────────────────────────────────────────────────────────────
# CALLBACK QUERY HANDLER (called from main.py for pattern ^afiliar:)
# ─────────────────────────────────────────────────────────────────────────────

async def handle_afiliar_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manejar todos los callbacks del flujo de afiliación."""
    query = update.callback_query
    if not query or not update.effective_user:
        return
    await query.answer()

    telegram_id = update.effective_user.id

    if not _check_admin(telegram_id):
        await query.answer("Sin permisos.", show_alert=True)
        return

    data = query.data or ""

    # ── CANCEL ──────────────────────────────────────────────────────────────
    if data == "afiliar:cancel":
        _clear_afiliar_state(telegram_id)
        await query.edit_message_text("❌ Afiliación cancelada.")
        return

    # ── PLAN SELECTION ───────────────────────────────────────────────────────
    if data.startswith("afiliar:plan:"):
        plan_type = data.split(":")[-1]  # monthly | express
        set_user_data(telegram_id, "afiliar_plan", plan_type)

        # Load available platforms
        try:
            availability = await get_platform_availability()
            field = "monthly_available" if plan_type == "monthly" else "express_available"
            available = [p for p in availability if p.get(field, 0) > 0]

            if not available:
                await query.edit_message_text(
                    "❌ No hay plataformas con disponibilidad para ese plan en este momento.\n"
                    "Agrega perfiles primero con /addexpress o /addcuenta."
                )
                _clear_afiliar_state(telegram_id)
                return

            plan_label = "Mensual (30 días)" if plan_type == "monthly" else "Express (24h)"
            text_lines = ""
            for p in availability:
                icon = p.get("icon_emoji", "📺")
                name = p.get("name", "")
                count = p.get(field, 0)
                avail_icon = "✅" if count > 0 else "❌"
                text_lines += f"{avail_icon} {icon} {name} — {count} disponible{'s' if count != 1 else ''}\n"

            set_user_state(telegram_id, "admin:afiliar:plataforma")
            nombre = get_user_data(telegram_id, "afiliar_nombre") or ""
            contacto = get_user_data(telegram_id, "afiliar_telefono") or ""

            await query.edit_message_text(
                f"📋 <b>Afiliación Manual</b>\n"
                f"👤 Cliente: <b>{nombre}</b>\n"
                f"📞 Contacto: <b>{contacto}</b>\n"
                f"📅 Plan: <b>{plan_label}</b>\n\n"
                f"Disponibilidad:\n{text_lines}\n"
                f"Selecciona la <b>plataforma</b>:",
                parse_mode="HTML",
                reply_markup=_platforms_keyboard(available, plan_type),
            )

        except Exception as e:
            logger.error(f"Error loading platforms in afiliar: {e}")
            await query.edit_message_text("❌ Error al cargar plataformas. Intenta de nuevo.")
            _clear_afiliar_state(telegram_id)
        return

    # ── PLATFORM SELECTION ───────────────────────────────────────────────────
    if data.startswith("afiliar:platform:"):
        platform_id = data.split(":")[-1]
        plan_type = get_user_data(telegram_id, "afiliar_plan") or "monthly"

        try:
            platform = await get_platform_by_id(platform_id)
            if not platform:
                await query.answer("Plataforma no encontrada.", show_alert=True)
                return

            # Check availability
            profile_type = plan_type  # monthly | express
            profiles = await get_available_profiles(platform_id, profile_type)
            if not profiles:
                await query.answer("Sin perfiles disponibles para esa plataforma/plan.", show_alert=True)
                return

            # Calculate price
            price_field = "monthly_price_usd" if plan_type == "monthly" else "express_price_usd"
            price_usd = float(platform.get(price_field) or 4.50)
            price_bs = await calculate_price_bs(price_usd)
            rate_obj = await get_current_rate()
            rate_value = float((rate_obj or {}).get("usd_binance") or 36.0)

            # Store selection
            set_user_data(telegram_id, "afiliar_platform_id", platform_id)
            set_user_data(telegram_id, "afiliar_platform_name", f"{platform.get('icon_emoji','')} {platform.get('name','')}")
            set_user_data(telegram_id, "afiliar_price_usd", str(price_usd))
            set_user_data(telegram_id, "afiliar_price_bs", str(price_bs))
            set_user_data(telegram_id, "afiliar_rate", str(rate_value))
            set_user_state(telegram_id, "admin:afiliar:confirmar")

            nombre = get_user_data(telegram_id, "afiliar_nombre") or ""
            contacto = get_user_data(telegram_id, "afiliar_telefono") or ""
            plan_label = "Mensual (30 días)" if plan_type == "monthly" else "Express (24h)"
            days = 30 if plan_type == "monthly" else 1
            now = venezuela_now()
            end_date = now + timedelta(days=days)

            await query.edit_message_text(
                f"📋 <b>Resumen de Afiliación</b>\n\n"
                f"👤 <b>Cliente:</b> {nombre}\n"
                f"📞 <b>Contacto:</b> {contacto}\n"
                f"📺 <b>Plataforma:</b> {platform.get('icon_emoji','')} {platform.get('name','')}\n"
                f"📅 <b>Plan:</b> {plan_label}\n"
                f"💵 <b>Precio:</b> ${price_usd:.2f} = Bs {price_bs:,.2f}\n"
                f"📊 <b>Tasa:</b> {rate_value:.2f} Bs/USD\n"
                f"📆 <b>Vence:</b> {format_datetime_vzla(end_date)}\n\n"
                f"⚠️ <i>Se asignará el primer perfil disponible de esta plataforma.</i>\n\n"
                f"¿Confirmas la afiliación?",
                parse_mode="HTML",
                reply_markup=_confirm_keyboard(),
            )

        except Exception as e:
            logger.error(f"Error in afiliar platform selection: {e}")
            await query.edit_message_text("❌ Error al procesar la selección.")
            _clear_afiliar_state(telegram_id)
        return

    # ── CONFIRM ──────────────────────────────────────────────────────────────
    if data == "afiliar:confirm":
        await _execute_affiliation(query, telegram_id)
        return


# ─────────────────────────────────────────────────────────────────────────────
# CORE AFFILIATION EXECUTION
# ─────────────────────────────────────────────────────────────────────────────

async def _execute_affiliation(query, admin_telegram_id: int) -> None:
    """Crear usuario externo, suscripción activa y asignar perfil."""
    from database.users import create_external_user, log_admin_action
    from database.subscriptions import create_active_subscription

    nombre = get_user_data(admin_telegram_id, "afiliar_nombre") or ""
    contacto = get_user_data(admin_telegram_id, "afiliar_telefono") or ""
    platform_id = get_user_data(admin_telegram_id, "afiliar_platform_id")
    plan_type = get_user_data(admin_telegram_id, "afiliar_plan") or "monthly"
    price_usd_str = get_user_data(admin_telegram_id, "afiliar_price_usd")
    price_bs_str = get_user_data(admin_telegram_id, "afiliar_price_bs")
    rate_str = get_user_data(admin_telegram_id, "afiliar_rate")

    if not all([nombre, contacto, platform_id, price_usd_str, price_bs_str, rate_str]):
        await query.edit_message_text("❌ Sesión expirada. Inicia de nuevo con /afiliar")
        _clear_afiliar_state(admin_telegram_id)
        return

    try:
        price_usd = float(price_usd_str)
        price_bs = float(price_bs_str)
        rate_used = float(rate_str)
    except ValueError:
        await query.edit_message_text("❌ Error en los datos del precio. Inicia de nuevo con /afiliar")
        _clear_afiliar_state(admin_telegram_id)
        return

    await query.edit_message_text("⏳ Procesando afiliación...")

    try:
        # 1. Crear usuario externo (sin telegram_id)
        user = await create_external_user(
            name=nombre,
            phone=contacto,
            notes=f"Afiliado manualmente por admin. Contacto: {contacto}",
        )
        if not user:
            await query.edit_message_text("❌ Error al crear el usuario. Intenta de nuevo.")
            _clear_afiliar_state(admin_telegram_id)
            return

        user_id = str(user["id"])

        # 2. Obtener perfil disponible
        profiles = await get_available_profiles(platform_id, plan_type)
        if not profiles:
            await query.edit_message_text(
                "❌ No hay perfiles disponibles en este momento.\n"
                "El usuario fue creado pero sin suscripción. Agrega perfiles y reintenta."
            )
            _clear_afiliar_state(admin_telegram_id)
            return

        profile = profiles[0]
        profile_id = str(profile["id"])

        # 3. Calcular fechas
        now = venezuela_now()
        days = 30 if plan_type == "monthly" else 1
        end_date = now + timedelta(days=days)

        # 4. Crear suscripción activa directamente
        sub = await create_active_subscription(
            user_id=user_id,
            platform_id=platform_id,
            plan_type=plan_type,
            price_usd=price_usd,
            price_bs=price_bs,
            rate_used=rate_used,
            end_date=end_date,
            profile_id=profile_id,
            payment_reference=f"MANUAL-{short_id(user_id)}",
        )
        if not sub:
            await query.edit_message_text("❌ Error al crear la suscripción. Intenta de nuevo.")
            _clear_afiliar_state(admin_telegram_id)
            return

        # 5. Marcar perfil como ocupado
        await assign_profile(profile_id)

        # 6. Obtener credenciales de la cuenta
        account_id = str(profile.get("account_id", ""))
        account = await get_account_by_id(account_id) if account_id else None

        platform = await get_platform_by_id(platform_id)
        platform_label = f"{(platform or {}).get('icon_emoji','')} {(platform or {}).get('name','')}"
        platform_slug = (platform or {}).get("slug", "")

        pin_text = PIN_LINE.format(pin=profile["pin"]) if profile.get("pin") else ""
        instructions = ACCESS_INSTRUCTIONS.get(platform_slug, "Ingresa con el email y contraseña proporcionados.")
        plan_label = "Mensual (30 días)" if plan_type == "monthly" else "Express (24h)"

        # 7. Registrar la acción en admin_log
        await log_admin_action(admin_telegram_id, "afiliar_manual", {
            "cliente": nombre,
            "contacto": contacto,
            "plataforma": platform_slug,
            "plan": plan_type,
            "user_id": user_id,
            "sub_id": str(sub["id"]),
        })

        _clear_afiliar_state(admin_telegram_id)

        # 8. Mostrar resumen con credenciales al admin
        credential_lines = ""
        if account:
            credential_lines = (
                f"📧 <b>Email:</b> <code>{account.get('email', 'N/A')}</code>\n"
                f"🔑 <b>Contraseña:</b> <code>{account.get('password', 'N/A')}</code>\n"
            )
        else:
            credential_lines = "⚠️ <i>No se pudo obtener la cuenta. Verifica manualmente.</i>\n"

        await query.edit_message_text(
            f"✅ <b>¡Afiliación completada!</b>\n\n"
            f"👤 <b>Cliente:</b> {nombre}\n"
            f"📞 <b>Contacto:</b> {contacto}\n"
            f"📺 <b>Plataforma:</b> {platform_label}\n"
            f"📅 <b>Plan:</b> {plan_label}\n"
            f"📆 <b>Vence:</b> {format_datetime_vzla(end_date)}\n\n"
            f"🔐 <b>Credenciales a entregar:</b>\n"
            f"👤 <b>Perfil:</b> {profile.get('profile_name', 'N/A')}\n"
            f"{credential_lines}"
            f"{pin_text}\n"
            f"📋 <b>Instrucciones:</b>\n{instructions}\n\n"
            f"─────────────────────\n"
            f"ℹ️ <i>Cuando este cliente esté por vencer, recibirás una notificación "
            f"aquí con todos sus datos para que lo contactes por {contacto}.</i>\n\n"
            f"🆔 <i>ID interno: {short_id(user_id)}</i>",
            parse_mode="HTML",
        )

    except Exception as e:
        logger.error(f"Error in _execute_affiliation: {e}")
        await query.edit_message_text(f"❌ Error inesperado: {e}\n\nIntenta de nuevo con /afiliar")
        _clear_afiliar_state(admin_telegram_id)


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _clear_afiliar_state(telegram_id: int) -> None:
    """Limpiar todo el estado de afiliación del admin."""
    clear_user_state(telegram_id)
    for key in ["afiliar_nombre", "afiliar_telefono", "afiliar_plan",
                "afiliar_platform_id", "afiliar_platform_name",
                "afiliar_price_usd", "afiliar_price_bs", "afiliar_rate"]:
        try:
            from bot.middleware import _get_redis
            r = _get_redis()
            r.delete(f"data:{telegram_id}:{key}")
        except Exception:
            pass
