"""Módulo de afiliación manual para admin.

Permite al admin registrar clientes que se concretaron por otra vía
(WhatsApp, llamada, etc.) y no tienen Telegram o no pudieron usar el bot.

El pago ya fue confirmado externamente. Este módulo:
  PATH A (cliente nuevo):  nombre → teléfono → plan → plataforma → perfil → confirmar
  PATH B (cliente existente): lista paginada → seleccionar → plan → plataforma → perfil → confirmar

NOTA: Todo el estado del flujo se guarda en context.user_data (en memoria,
no depende de Redis) para evitar fallos por conexión a Redis.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from bot.messages import ACCESS_INSTRUCTIONS
from config import settings
from database.accounts import get_account_by_id
from database.platforms import get_platform_by_id
from database.subscriptions import create_active_subscription
from database.profiles import get_available_profiles, assign_profile, get_profile_by_id
from database.analytics import get_platform_availability
from services.exchange_service import calculate_price_bs, get_current_rate
from utils.helpers import venezuela_now, format_datetime_vzla, short_id
from utils.validators import is_admin

logger = logging.getLogger(__name__)

_KEY = "afiliar_session"
_PAGE_SIZE = 10


def _check_admin(telegram_id: int) -> bool:
    return is_admin(telegram_id, settings.ADMIN_TELEGRAM_IDS)


def _session(context: ContextTypes.DEFAULT_TYPE) -> dict:
    """Return the active affiliation session dict (creates it if missing)."""
    if _KEY not in context.user_data:
        context.user_data[_KEY] = {}
    return context.user_data[_KEY]


def _clear_session(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(_KEY, None)


def is_in_afiliar_flow(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """True when the admin has an active affiliation session."""
    return bool(context.user_data.get(_KEY, {}).get("step"))


# ─────────────────────────────────────────────────────────────────────────────
# KEYBOARDS
# ─────────────────────────────────────────────────────────────────────────────

def _tipo_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 Cliente nuevo", callback_data="afiliar:tipo:nuevo")],
        [InlineKeyboardButton("🔄 Cliente existente", callback_data="afiliar:tipo:existente")],
        [InlineKeyboardButton("❌ Cancelar", callback_data="afiliar:cancel")],
    ])


def _plan_select_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 Mensual (30 días)", callback_data="afiliar:plan:monthly")],
        [InlineKeyboardButton("⚡ Express (24h)", callback_data="afiliar:plan:express")],
        [InlineKeyboardButton("❌ Cancelar", callback_data="afiliar:cancel")],
    ])


def _platforms_keyboard(platforms: list[dict]) -> InlineKeyboardMarkup:
    buttons = []
    for p in platforms:
        icon = p.get("icon_emoji", "📺")
        name = p.get("name", "")
        pid = str(p.get("platform_id") or p.get("id", ""))
        buttons.append([InlineKeyboardButton(f"{icon} {name}", callback_data=f"afiliar:platform:{pid}")])
    buttons.append([InlineKeyboardButton("❌ Cancelar", callback_data="afiliar:cancel")])
    return InlineKeyboardMarkup(buttons)


def _profiles_keyboard(profiles: list[dict]) -> InlineKeyboardMarkup:
    buttons = []
    for p in profiles:
        name = p.get("profile_name", "Sin nombre")
        pin = p.get("pin", "—")
        buttons.append([InlineKeyboardButton(
            f"👤 {name} — PIN: {pin}",
            callback_data=f"afiliar:perfil:{p['id']}",
        )])
    buttons.append([InlineKeyboardButton("❌ Cancelar", callback_data="afiliar:cancel")])
    return InlineKeyboardMarkup(buttons)


def _clients_keyboard(clients: list[dict], page: int, total: int) -> InlineKeyboardMarkup:
    buttons = []
    for c in clients:
        display = c.get("name") or c.get("username") or f"ID:{str(c['id'])[:8]}"
        contact = c.get("phone") or ""
        label = f"👤 {display}" + (f" — {contact}" if contact else "")
        buttons.append([InlineKeyboardButton(label, callback_data=f"afiliar:cliente:{c['id']}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Anterior", callback_data=f"afiliar:page:{page - 1}"))
    if (page + 1) * _PAGE_SIZE < total:
        nav.append(InlineKeyboardButton("Siguiente ➡️", callback_data=f"afiliar:page:{page + 1}"))
    if nav:
        buttons.append(nav)
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
    if not _check_admin(update.effective_user.id):
        await update.message.reply_text("❌ Sin permisos de administrador.")
        return
    _clear_session(context)
    await update.message.reply_text(
        "👤 <b>Afiliación Manual de Cliente</b>\n\n"
        "¿Estás afiliando a un cliente nuevo o a uno ya registrado en el sistema?",
        parse_mode="HTML",
        reply_markup=_tipo_keyboard(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# TEXT INPUT HANDLER  (called from _text_message_router in main.py)
# ─────────────────────────────────────────────────────────────────────────────

async def handle_afiliar_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Procesar texto en los pasos del flujo que requieren entrada de texto."""
    if not update.message or not update.effective_user:
        return
    text = update.message.text.strip()
    sess = _session(context)
    step = sess.get("step", "")

    if step == "nombre":
        if len(text) < 2:
            await update.message.reply_text("❌ Nombre muy corto. Ingresa el nombre completo del cliente:")
            return
        sess["nombre"] = text
        sess["step"] = "telefono"
        await update.message.reply_text(
            f"✅ Nombre: <b>{text}</b>\n\n"
            f"📞 Paso 2/3 — ¿Cuál es el <b>teléfono o contacto</b> del cliente?\n"
            f"<i>(Ej: 0414-1234567, correo, Instagram, etc.)</i>",
            parse_mode="HTML",
        )

    elif step == "telefono":
        if len(text) < 4:
            await update.message.reply_text("❌ Contacto muy corto. Ingresa el teléfono u otro dato de contacto:")
            return
        sess["telefono"] = text
        sess["step"] = "plan"
        await update.message.reply_text(
            f"✅ Contacto: <b>{text}</b>\n\n"
            f"📋 Paso 3/3 — Selecciona el <b>tipo de plan</b>:",
            parse_mode="HTML",
            reply_markup=_plan_select_keyboard(),
        )

    elif step in ("tipo", "cliente_list", "plan", "plataforma", "perfil", "confirmar"):
        hints = {
            "tipo": ("Selecciona el tipo de cliente con los botones.", _tipo_keyboard()),
            "cliente_list": ("Selecciona el cliente usando los botones de arriba.", None),
            "plan": ("Selecciona el tipo de plan usando los botones de arriba.", _plan_select_keyboard()),
            "plataforma": ("Selecciona la plataforma usando los botones de arriba.", None),
            "perfil": ("Selecciona el perfil usando los botones de arriba.", None),
            "confirmar": ("Usa los botones para confirmar o cancelar la afiliación.", _confirm_keyboard()),
        }
        hint, kb = hints.get(step, ("Usa los botones para continuar.", None))
        await update.message.reply_text(
            f"👆 <i>{hint}</i>\n\nSi deseas cancelar y empezar de nuevo: /afiliar",
            parse_mode="HTML",
            reply_markup=kb,
        )


# ─────────────────────────────────────────────────────────────────────────────
# CLIENT LIST HELPER
# ─────────────────────────────────────────────────────────────────────────────

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
            f"🔄 <b>Selecciona el cliente existente</b>\n"
            f"Página {page + 1}/{total_pages} — {total} clientes en total",
            parse_mode="HTML",
            reply_markup=_clients_keyboard(clients, page, total),
        )
    except Exception as e:
        logger.error(f"Error showing client list: {e}")
        await query.edit_message_text("❌ Error al cargar clientes. Intenta de nuevo.")
        _clear_session(context)


# ─────────────────────────────────────────────────────────────────────────────
# CALLBACK QUERY HANDLER  (called from main.py for pattern ^afiliar:)
# ─────────────────────────────────────────────────────────────────────────────

async def handle_afiliar_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manejar todos los callbacks del flujo de afiliación."""
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
    if data == "afiliar:cancel":
        _clear_session(context)
        await query.edit_message_text("❌ Afiliación cancelada.")
        return

    # ── TIPO: nuevo / existente ──────────────────────────────────────────────
    if data.startswith("afiliar:tipo:"):
        tipo = data.split(":")[-1]
        if tipo == "nuevo":
            sess["path"] = "nuevo"
            sess["step"] = "nombre"
            await query.edit_message_text(
                "👤 <b>Cliente Nuevo</b>\n\n"
                "El pago ya fue confirmado externamente.\n\n"
                "─────────────────────\n"
                "📝 Paso 1/3 — ¿Cuál es el <b>nombre completo</b> del cliente?",
                parse_mode="HTML",
            )
        else:
            sess["path"] = "existente"
            sess["step"] = "cliente_list"
            await _show_client_list(query, context, page=0)
        return

    # ── CLIENT LIST PAGINATION ───────────────────────────────────────────────
    if data.startswith("afiliar:page:"):
        page = int(data.split(":")[-1])
        await _show_client_list(query, context, page=page)
        return

    # ── CLIENT SELECTION ────────────────────────────────────────────────────
    if data.startswith("afiliar:cliente:"):
        user_id = data.split(":", 2)[2]
        try:
            from database.users import get_user_by_id
            user = await get_user_by_id(user_id)
            if not user:
                await query.answer("Cliente no encontrado.", show_alert=True)
                return
            sess["user_id"] = user_id
            sess["nombre"] = user.get("name") or user.get("username") or f"ID:{user_id[:8]}"
            sess["telefono"] = user.get("phone") or "Sin contacto"
            sess["step"] = "plan"
            await query.edit_message_text(
                f"✅ <b>Cliente:</b> {sess['nombre']}\n"
                f"📞 <b>Contacto:</b> {sess['telefono']}\n\n"
                f"📋 Selecciona el <b>tipo de plan</b>:",
                parse_mode="HTML",
                reply_markup=_plan_select_keyboard(),
            )
        except Exception as e:
            logger.error(f"Error selecting client: {e}")
            await query.edit_message_text("❌ Error al cargar el cliente.")
            _clear_session(context)
        return

    # ── PLAN SELECTION ───────────────────────────────────────────────────────
    if data.startswith("afiliar:plan:"):
        plan_type = data.split(":")[-1]
        sess["plan"] = plan_type

        try:
            availability = await get_platform_availability()
            field = "monthly_available" if plan_type == "monthly" else "express_available"
            available = [p for p in availability if p.get(field, 0) > 0]

            if not available:
                await query.edit_message_text(
                    "❌ No hay plataformas con disponibilidad para ese plan en este momento.\n"
                    "Agrega perfiles primero con /addexpress o /addcuenta."
                )
                _clear_session(context)
                return

            plan_label = "Mensual (30 días)" if plan_type == "monthly" else "Express (24h)"
            text_lines = ""
            for p in availability:
                icon = p.get("icon_emoji", "📺")
                name = p.get("name", "")
                count = p.get(field, 0)
                avail_icon = "✅" if count > 0 else "❌"
                text_lines += f"{avail_icon} {icon} {name} — {count} disponible{'s' if count != 1 else ''}\n"

            sess["step"] = "plataforma"
            nombre = sess.get("nombre", "")
            contacto = sess.get("telefono", "")

            await query.edit_message_text(
                f"📋 <b>Afiliación Manual</b>\n"
                f"👤 Cliente: <b>{nombre}</b>\n"
                f"📞 Contacto: <b>{contacto}</b>\n"
                f"📅 Plan: <b>{plan_label}</b>\n\n"
                f"Disponibilidad:\n{text_lines}\n"
                f"Selecciona la <b>plataforma</b>:",
                parse_mode="HTML",
                reply_markup=_platforms_keyboard(available),
            )

        except Exception as e:
            logger.error(f"Error loading platforms in afiliar: {e}", exc_info=True)
            await query.edit_message_text("❌ Error al cargar plataformas. Intenta de nuevo.")
            _clear_session(context)
        return

    # ── PLATFORM SELECTION → muestra perfiles disponibles ───────────────────
    if data.startswith("afiliar:platform:"):
        platform_id = data.split(":")[-1]
        plan_type = sess.get("plan", "monthly")

        try:
            platform = await get_platform_by_id(platform_id)
            if not platform:
                await query.answer("Plataforma no encontrada.", show_alert=True)
                return

            profiles = await get_available_profiles(platform_id, plan_type)
            if not profiles:
                await query.answer("Sin perfiles disponibles para esa plataforma/plan.", show_alert=True)
                return

            price_field = "monthly_price_usd" if plan_type == "monthly" else "express_price_usd"
            price_usd = float(platform.get(price_field) or 4.50)
            price_bs = await calculate_price_bs(price_usd)
            rate_obj = await get_current_rate()
            rate_value = float((rate_obj or {}).get("usd_binance") or 36.0)

            sess["platform_id"] = platform_id
            sess["price_usd"] = price_usd
            sess["price_bs"] = price_bs
            sess["rate"] = rate_value
            sess["step"] = "perfil"

            plan_label = "Mensual (30 días)" if plan_type == "monthly" else "Express (24h)"
            await query.edit_message_text(
                f"📋 <b>Selección de Perfil</b>\n\n"
                f"👤 <b>Cliente:</b> {sess.get('nombre', '')}\n"
                f"📺 <b>Plataforma:</b> {platform.get('icon_emoji','')} {platform.get('name','')}\n"
                f"📅 <b>Plan:</b> {plan_label}\n\n"
                f"Elige el <b>perfil</b> a asignar:",
                parse_mode="HTML",
                reply_markup=_profiles_keyboard(profiles),
            )

        except Exception as e:
            logger.error(f"Error in afiliar platform selection: {e}", exc_info=True)
            await query.edit_message_text("❌ Error al cargar perfiles. Intenta de nuevo.")
            _clear_session(context)
        return

    # ── PROFILE SELECTION ────────────────────────────────────────────────────
    if data.startswith("afiliar:perfil:"):
        profile_id = data.split(":", 2)[2]
        try:
            profile = await get_profile_by_id(profile_id)
            if not profile:
                await query.answer("Perfil no encontrado.", show_alert=True)
                return

            platform = await get_platform_by_id(sess.get("platform_id", ""))
            plan_type = sess.get("plan", "monthly")
            price_usd = sess.get("price_usd", 0)
            price_bs = sess.get("price_bs", 0)
            days = 30 if plan_type == "monthly" else 1
            end_date = venezuela_now() + timedelta(days=days)
            plan_label = "Mensual (30 días)" if plan_type == "monthly" else "Express (24h)"

            sess["profile_id"] = profile_id
            sess["step"] = "confirmar"

            await query.edit_message_text(
                f"📋 <b>Resumen de Afiliación</b>\n\n"
                f"👤 <b>Cliente:</b> {sess.get('nombre', '')}\n"
                f"📞 <b>Contacto:</b> {sess.get('telefono', '')}\n"
                f"📺 <b>Plataforma:</b> {(platform or {}).get('icon_emoji','')} {(platform or {}).get('name','')}\n"
                f"📅 <b>Plan:</b> {plan_label}\n"
                f"👤 <b>Perfil:</b> {profile.get('profile_name','N/A')} — PIN: {profile.get('pin','—')}\n"
                f"💵 <b>Precio:</b> ${price_usd:.2f} = Bs {price_bs:,.2f}\n"
                f"📆 <b>Vence:</b> {format_datetime_vzla(end_date)}\n\n"
                f"¿Confirmas la afiliación?",
                parse_mode="HTML",
                reply_markup=_confirm_keyboard(),
            )
        except Exception as e:
            logger.error(f"Error in afiliar profile selection: {e}")
            await query.edit_message_text("❌ Error al procesar el perfil.")
            _clear_session(context)
        return

    # ── CONFIRM ──────────────────────────────────────────────────────────────
    if data == "afiliar:confirm":
        await _execute_affiliation(query, context, update.effective_user.id)
        return


# ─────────────────────────────────────────────────────────────────────────────
# CORE AFFILIATION EXECUTION
# ─────────────────────────────────────────────────────────────────────────────

async def _execute_affiliation(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    admin_telegram_id: int,
) -> None:
    """Crear/obtener usuario, crear suscripción activa, asignar perfil elegido y mostrar ticket."""
    from database.users import create_external_user, log_admin_action, delete_user

    sess = _session(context)
    nombre = sess.get("nombre", "")
    contacto = sess.get("telefono", "")
    platform_id = sess.get("platform_id")
    plan_type = sess.get("plan", "monthly")
    price_usd = sess.get("price_usd")
    price_bs = sess.get("price_bs")
    rate_used = sess.get("rate")
    profile_id = sess.get("profile_id")
    existing_user_id = sess.get("user_id")  # PATH B: cliente existente

    if not all([nombre, contacto, platform_id, price_usd, price_bs, rate_used, profile_id]):
        await query.edit_message_text("❌ Sesión expirada o incompleta. Inicia de nuevo con /afiliar")
        _clear_session(context)
        return

    await query.edit_message_text("⏳ Procesando afiliación...")

    try:
        # 1. Obtener o crear usuario
        user_created = False
        if existing_user_id:
            from database.users import get_user_by_id
            user = await get_user_by_id(existing_user_id)
            if not user:
                await query.edit_message_text("❌ No se pudo cargar el cliente. Intenta de nuevo.")
                _clear_session(context)
                return
        else:
            user = await create_external_user(
                name=nombre,
                phone=contacto,
                notes=f"Afiliado manualmente por admin. Contacto: {contacto}",
            )
            if not user:
                await query.edit_message_text("❌ Error al crear el usuario. Intenta de nuevo.")
                _clear_session(context)
                return
            user_created = True

        user_id = str(user["id"])

        # 2. Calcular fechas
        now = venezuela_now()
        days = 30 if plan_type == "monthly" else 1
        end_date = now + timedelta(days=days)

        # 3. Crear suscripción activa directamente
        sub = await create_active_subscription(
            user_id=user_id,
            platform_id=platform_id,
            plan_type=plan_type,
            price_usd=float(price_usd),
            price_bs=float(price_bs),
            rate_used=float(rate_used),
            end_date=end_date,
            profile_id=profile_id,
            payment_reference=f"MANUAL-{short_id(user_id)}",
        )
        if not sub:
            if user_created:
                await delete_user(user_id)
            await query.edit_message_text(
                "❌ Error al crear la suscripción."
                + (" El usuario fue eliminado para evitar registros huérfanos." if user_created else "")
            )
            _clear_session(context)
            return

        # 4. Marcar perfil como ocupado
        await assign_profile(profile_id)

        # 5. Obtener credenciales para el ticket
        profile = await get_profile_by_id(profile_id) or {}
        account_id = str(profile.get("account_id", ""))
        account = await get_account_by_id(account_id) if account_id else None
        platform = await get_platform_by_id(platform_id)
        platform_label = f"{(platform or {}).get('icon_emoji','')} {(platform or {}).get('name','')}".strip()
        platform_slug = (platform or {}).get("slug", "")
        instructions = ACCESS_INSTRUCTIONS.get(platform_slug, "Ingresa con el email y contraseña proporcionados.")
        plan_label = "Mensual (30 días)" if plan_type == "monthly" else "Express (24h)"

        # 6. Registrar en admin_log
        await log_admin_action(admin_telegram_id, "afiliar_manual", {
            "cliente": nombre,
            "contacto": contacto,
            "plataforma": platform_slug,
            "plan": plan_type,
            "user_id": user_id,
            "sub_id": str(sub["id"]),
        })

        _clear_session(context)

        # 7. Generar ticket
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
            f"📆 <b>Vence:</b> {format_datetime_vzla(end_date)}\n\n"
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
        await query.message.reply_text(
            "📋 <i>Copia el ticket de arriba para enviárselo al cliente.</i>\n\n"
            f"🆔 <i>ID interno: {short_id(user_id)}</i>",
            parse_mode="HTML",
        )

    except Exception as e:
        logger.error(f"Error in _execute_affiliation: {e}")
        await query.edit_message_text(f"❌ Error inesperado: {e}\n\nIntenta de nuevo con /afiliar")
        _clear_session(context)
