from __future__ import annotations

import logging
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from bot.keyboards import (
    admin_dashboard_keyboard, pending_payment_keyboard, paginator_keyboard,
    platforms_keyboard, flyer_preview_keyboard, main_menu_keyboard,
    prices_menu_keyboard, platform_price_edit_keyboard, confirm_price_keyboard,
    client_detail_keyboard,
    clients_list_keyboard,
)
from bot.messages import ADMIN_DASHBOARD
from bot.middleware import set_user_state, set_user_data, get_user_data, clear_user_state
from config import settings
from database.analytics import get_dashboard_stats, get_income_report, get_clients_list, get_client_detail, get_platform_availability
from database.subscriptions import get_pending_subscriptions, confirm_subscription, cancel_subscription
from database.profiles import get_available_profiles, assign_profile, update_profile_pin
from database.platforms import get_active_platforms, get_platform_by_id, update_platform_prices
from database.users import block_user, unblock_user, log_admin_action, get_user_by_telegram_id, update_user_name, update_user_phone
from services.exchange_service import update_rate, get_current_rate, fetch_binance_p2p_rate, auto_update_rate
from utils.helpers import format_datetime_vzla, short_id
from utils.validators import is_admin

logger = logging.getLogger(__name__)


def _check_admin(telegram_id: int) -> bool:
    return is_admin(telegram_id, settings.ADMIN_TELEGRAM_IDS)


async def admin_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show admin dashboard with stats."""
    if not update.effective_user:
        return
    telegram_id = update.effective_user.id

    if not _check_admin(telegram_id):
        if update.message:
            await update.message.reply_text("❌ No tienes permisos de administrador.")
        return

    try:
        stats = await get_dashboard_stats()
        availability = stats.get("platform_availability", [])

        avail_text = ""
        for p in availability:
            icon = p.get("icon_emoji", "📺")
            name = p.get("name", "")
            monthly = p.get("monthly_available", 0)
            express = p.get("express_available", 0)
            avail_text += f"{icon} {name}: {monthly}M | {express}E\n"

        dashboard_text = ADMIN_DASHBOARD.format(
            total_users=stats.get("total_users", 0),
            new_users_today=stats.get("new_users_today", 0),
            active_subscriptions=stats.get("active_subscriptions", 0),
            pending_payments=stats.get("pending_payments", 0),
            expiring_soon=stats.get("expiring_soon", 0),
            monthly_revenue_usd=stats.get("monthly_revenue_usd", 0.0),
            availability=avail_text or "Sin datos",
        )

        msg = update.message or (update.callback_query and update.callback_query.message)
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(
                dashboard_text,
                parse_mode="HTML",
                reply_markup=admin_dashboard_keyboard(),
            )
        elif update.message:
            await update.message.reply_text(
                dashboard_text,
                parse_mode="HTML",
                reply_markup=admin_dashboard_keyboard(),
            )
    except Exception as e:
        logger.error(f"Error in admin_dashboard: {e}")
        if update.message:
            await update.message.reply_text(f"Error al cargar dashboard: {e}")


async def cmd_tasa(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Update Binance exchange rate. Usage: /tasa 36.50"""
    if not update.message or not update.effective_user:
        return
    telegram_id = update.effective_user.id

    if not _check_admin(telegram_id):
        await update.message.reply_text("❌ Sin permisos.")
        return

    args = context.args
    if not args:
        rate = await get_current_rate()
        current = (rate or {}).get("usd_binance", "N/A")
        await update.message.reply_text(
            f"💱 <b>Tasa actual Binance:</b> Bs {current}/USD\n\n"
            f"Para actualizar: <code>/tasa 36.50</code>",
            parse_mode="HTML",
        )
        return

    try:
        new_rate = float(args[0])
        await update_rate(new_rate, telegram_id)
        await log_admin_action(telegram_id, "update_rate_binance", {"rate": new_rate})
        await update.message.reply_text(
            f"✅ Tasa Binance actualizada: <b>Bs {new_rate:.2f}/USD</b>",
            parse_mode="HTML",
        )
    except ValueError:
        await update.message.reply_text("❌ Valor inválido. Ejemplo: /tasa 36.50")


async def cmd_tasabcv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Update BCV rates. Usage: /tasabcv 35.80 38.20"""
    if not update.message or not update.effective_user:
        return
    telegram_id = update.effective_user.id

    if not _check_admin(telegram_id):
        await update.message.reply_text("❌ Sin permisos.")
        return

    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Uso: /tasabcv <usd_bcv> <eur_bcv>\nEjemplo: /tasabcv 35.80 38.20")
        return

    try:
        usd_bcv = float(args[0])
        eur_bcv = float(args[1])
        await update_rate(usd_bcv, telegram_id, usd_bcv=usd_bcv, eur_bcv=eur_bcv)
        await log_admin_action(telegram_id, "update_rate_bcv", {"usd_bcv": usd_bcv, "eur_bcv": eur_bcv})
        await update.message.reply_text(
            f"✅ Tasas BCV actualizadas:\n💵 USD: Bs {usd_bcv:.2f}\n💶 EUR: Bs {eur_bcv:.2f}",
            parse_mode="HTML",
        )
    except ValueError:
        await update.message.reply_text("❌ Valores inválidos.")


async def cmd_addcuenta(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start guided multi-step account creation."""
    if not update.message or not update.effective_user:
        return
    telegram_id = update.effective_user.id

    if not _check_admin(telegram_id):
        await update.message.reply_text("❌ Sin permisos.")
        return

    platforms = await get_active_platforms()
    platform_list = "\n".join([f"{i+1}. {p.get('icon_emoji','')} {p['name']} ({p['slug']})" for i, p in enumerate(platforms)])

    set_user_state(telegram_id, "admin:addcuenta:select_platform")
    await update.message.reply_text(
        f"🔧 <b>Agregar nueva cuenta</b>\n\n"
        f"Selecciona la plataforma:\n{platform_list}\n\n"
        f"Responde con el número o slug de la plataforma:",
        parse_mode="HTML",
    )


async def cmd_addexpress(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Add express slot. Usage: /addexpress <platform_slug> <profile_name> <account_id> [pin]"""
    if not update.message or not update.effective_user:
        return
    telegram_id = update.effective_user.id

    if not _check_admin(telegram_id):
        await update.message.reply_text("❌ Sin permisos.")
        return

    args = context.args
    if len(args) < 3:
        await update.message.reply_text(
            "Uso: /addexpress <platform_slug> <profile_name> <account_id> [pin]\n"
            "Ejemplo: /addexpress netflix MiPerfil abc123 1234"
        )
        return

    platform_slug = args[0]
    profile_name = args[1]
    account_id = args[2]
    pin = args[3] if len(args) > 3 else None

    from database.platforms import get_platform_by_slug
    from database.profiles import create_profile

    platform = await get_platform_by_slug(platform_slug)
    if not platform:
        await update.message.reply_text(f"❌ Plataforma '{platform_slug}' no encontrada.")
        return

    profile = await create_profile(account_id, str(platform["id"]), profile_name, pin, "express")
    if profile:
        await log_admin_action(telegram_id, "add_express_profile", {"platform": platform_slug, "profile_name": profile_name})
        await update.message.reply_text(f"✅ Slot express agregado: <b>{profile_name}</b> para {platform_slug}", parse_mode="HTML")
    else:
        await update.message.reply_text("❌ Error al crear el perfil express.")


async def cmd_editpin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Update profile PIN. Usage: /editpin <profile_id> <new_pin>"""
    if not update.message or not update.effective_user:
        return
    telegram_id = update.effective_user.id

    if not _check_admin(telegram_id):
        await update.message.reply_text("❌ Sin permisos.")
        return

    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Uso: /editpin <profile_id> <new_pin>")
        return

    profile_id, new_pin = args[0], args[1]
    success = await update_profile_pin(profile_id, new_pin)

    if success:
        await log_admin_action(telegram_id, "edit_pin", {"profile_id": profile_id})
        await update.message.reply_text(f"✅ PIN actualizado para perfil {short_id(profile_id)}")
    else:
        await update.message.reply_text("❌ Error al actualizar PIN.")


async def cmd_clientes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show paginated client list. Usage: /clientes [page]"""
    if not update.message or not update.effective_user:
        return
    telegram_id = update.effective_user.id

    if not _check_admin(telegram_id):
        await update.message.reply_text("❌ Sin permisos.")
        return

    args = context.args
    page = int(args[0]) if args else 1

    data = await get_clients_list(page=page, per_page=10)
    clients = data.get("clients", [])
    total = data.get("total", 0)
    total_pages = data.get("total_pages", 1)

    if not clients:
        await update.message.reply_text("No hay clientes registrados.")
        return

    text = f"👥 <b>Clientes</b> - Página {page}/{total_pages} (Total: {total})\n\n"
    for c in clients:
        name = c.get("name") or c.get("username") or "Sin nombre"
        tid = c.get("telegram_id")
        purchases = c.get("total_purchases", 0)
        status = "✅" if c.get("status") == "active" else "🚫"
        text += f"{status} {name} | ID: <code>{tid}</code> | Compras: {purchases}\n"

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=paginator_keyboard(page, total_pages, "admin:clients_page"),
    )


async def cmd_cliente(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show individual client detail. Usage: /cliente <telegram_id>"""
    if not update.message or not update.effective_user:
        return
    telegram_id = update.effective_user.id

    if not _check_admin(telegram_id):
        await update.message.reply_text("❌ Sin permisos.")
        return

    args = context.args
    if not args:
        await update.message.reply_text("Uso: /cliente <telegram_id>")
        return

    try:
        target_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ ID inválido.")
        return

    data = await get_client_detail(target_id)
    if not data:
        await update.message.reply_text(f"❌ Cliente con ID {target_id} no encontrado.")
        return

    user = data["user"]
    subs = data.get("subscriptions", [])

    is_blocked = user.get("status") == "blocked"
    status_icon = "🚫 Bloqueado" if is_blocked else "✅ Activo"

    text = (
        f"👤 <b>Detalle del Cliente</b>\n\n"
        f"📝 Nombre: {user.get('name') or 'N/A'}\n"
        f"👤 Username: @{user.get('username') or 'N/A'}\n"
        f"🆔 Telegram ID: <code>{user.get('telegram_id')}</code>\n"
        f"📱 Teléfono: {user.get('phone') or 'N/A'}\n"
        f"🛒 Compras: {user.get('total_purchases', 0)}\n"
        f"📊 Estado: {status_icon}\n\n"
        f"📋 <b>Últimas suscripciones ({len(subs)}):</b>\n"
    )

    for sub in subs[:5]:
        platform = (sub.get("platforms") or {}).get("name", "?")
        plan = sub.get("plan_type", "?")
        sub_status = sub.get("status", "?")
        text += f"  • {platform} ({plan}) - {sub_status}\n"

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=client_detail_keyboard(target_id, is_blocked),
    )


async def cmd_pendientes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show and review pending payments."""
    if not update.message or not update.effective_user:
        return
    telegram_id = update.effective_user.id

    if not _check_admin(telegram_id):
        await update.message.reply_text("❌ Sin permisos.")
        return

    subs = await get_pending_subscriptions()

    if not subs:
        await update.message.reply_text("✅ No hay pagos pendientes.")
        return

    for sub in subs[:5]:
        user = sub.get("users") or {}
        platform = sub.get("platforms") or {}
        sub_id = str(sub["id"])

        text = (
            f"⏳ <b>Pago Pendiente</b>\n\n"
            f"👤 Usuario: {user.get('name') or user.get('username', 'N/A')}\n"
            f"🆔 ID: {user.get('telegram_id')}\n"
            f"📺 Plataforma: {platform.get('icon_emoji','')} {platform.get('name','')}\n"
            f"📅 Plan: {sub.get('plan_type')}\n"
            f"💵 Monto: ${sub.get('price_usd', 0):.2f} USD\n"
            f"💰 Bs: {sub.get('price_bs', 0):.2f}\n"
            f"🔖 Sub ID: #{short_id(sub_id)}\n"
            f"📸 Comprobante: {sub.get('payment_image_url') or 'No enviado'}"
        )

        await update.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=pending_payment_keyboard(sub_id),
        )


async def cmd_ingresos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show income report. Usage: /ingresos [today|week|month]"""
    if not update.message or not update.effective_user:
        return
    telegram_id = update.effective_user.id

    if not _check_admin(telegram_id):
        await update.message.reply_text("❌ Sin permisos.")
        return

    args = context.args
    period = args[0] if args else "month"
    if period not in ("today", "week", "month"):
        period = "month"

    report = await get_income_report(period)
    period_label = {"today": "Hoy", "week": "Esta semana", "month": "Este mes"}.get(period, period)

    by_platform_text = "\n".join([f"  📺 {k}: ${v:.2f}" for k, v in (report.get("by_platform") or {}).items()])
    by_plan_text = "\n".join([f"  📅 {k}: ${v:.2f}" for k, v in (report.get("by_plan") or {}).items()])

    text = (
        f"💰 <b>Reporte de Ingresos - {period_label}</b>\n\n"
        f"💵 Total USD: <b>${report.get('total_usd', 0):.2f}</b>\n"
        f"💴 Total Bs: <b>Bs {report.get('total_bs', 0):,.2f}</b>\n"
        f"🔢 Transacciones: <b>{report.get('transaction_count', 0)}</b>\n\n"
        f"📊 Por plataforma:\n{by_platform_text or '  Sin datos'}\n\n"
        f"📋 Por plan:\n{by_plan_text or '  Sin datos'}"
    )

    await update.message.reply_text(text, parse_mode="HTML")


async def cmd_bloquear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Block a user. Usage: /bloquear <telegram_id>"""
    if not update.message or not update.effective_user:
        return
    telegram_id = update.effective_user.id

    if not _check_admin(telegram_id):
        await update.message.reply_text("❌ Sin permisos.")
        return

    args = context.args
    if not args:
        await update.message.reply_text("Uso: /bloquear <telegram_id>")
        return

    try:
        target_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ ID inválido.")
        return

    await block_user(target_id)
    await log_admin_action(telegram_id, "block_user", {"target_telegram_id": target_id})
    await update.message.reply_text(f"✅ Usuario {target_id} bloqueado.")


async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start broadcast flow. Usage: /broadcast <message>"""
    if not update.message or not update.effective_user:
        return
    telegram_id = update.effective_user.id

    if not _check_admin(telegram_id):
        await update.message.reply_text("❌ Sin permisos.")
        return

    if not context.args:
        await update.message.reply_text(
            "Uso: /broadcast <mensaje>\n\n"
            "Ejemplo: /broadcast ¡Nueva promoción disponible!"
        )
        return

    message_text = " ".join(context.args)
    set_user_data(telegram_id, "broadcast_message", message_text)
    set_user_state(telegram_id, "admin:broadcast_confirm")

    from database.users import get_all_active_users
    users = await get_all_active_users()
    count = len(users)

    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    confirm_kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"📤 Enviar a {count} usuarios", callback_data="admin:broadcast_do"),
            InlineKeyboardButton("❌ Cancelar", callback_data="admin:broadcast_cancel"),
        ]
    ])

    await update.message.reply_text(
        f"📢 <b>Confirmar Broadcast</b>\n\n"
        f"Mensaje:\n<i>{message_text}</i>\n\n"
        f"Se enviará a <b>{count}</b> usuarios activos.",
        parse_mode="HTML",
        reply_markup=confirm_kb,
    )


async def handle_broadcast_do(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Execute broadcast after confirmation."""
    query = update.callback_query
    if not query or not update.effective_user:
        return
    await query.answer()
    telegram_id = update.effective_user.id

    if not _check_admin(telegram_id):
        return

    message_text = get_user_data(telegram_id, "broadcast_message")
    if not message_text:
        await query.edit_message_text("Error: mensaje no encontrado.")
        return

    from database.users import get_all_active_users
    from services.notification_service import broadcast_campaign
    users = await get_all_active_users()
    user_ids = [u["telegram_id"] for u in users if u.get("telegram_id")]

    await query.edit_message_text(f"📤 Enviando mensaje a {len(user_ids)} usuarios...")

    result = await broadcast_campaign(
        campaign_id="manual_broadcast",
        user_ids=user_ids,
        message_template=message_text,
    )

    await log_admin_action(telegram_id, "broadcast", {"sent": result["sent"], "failed": result["failed"]})
    await query.edit_message_text(
        f"✅ <b>Broadcast completado</b>\n\n"
        f"✅ Enviados: {result['sent']}\n"
        f"❌ Fallidos: {result['failed']}",
        parse_mode="HTML",
    )


async def cmd_flyer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start campaign/flyer creation. Usage: /flyer <platform_slug> <title>"""
    if not update.message or not update.effective_user:
        return
    telegram_id = update.effective_user.id

    if not _check_admin(telegram_id):
        await update.message.reply_text("❌ Sin permisos.")
        return

    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "Uso: /flyer <platform_slug> <título del contenido>\n"
            "Ejemplo: /flyer netflix 'Stranger Things 5'"
        )
        return

    platform_slug = args[0]
    title = " ".join(args[1:])

    from database.platforms import get_platform_by_slug
    platform = await get_platform_by_slug(platform_slug)
    if not platform:
        await update.message.reply_text(f"❌ Plataforma '{platform_slug}' no encontrada.")
        return

    await update.message.reply_text(f"⏳ Generando flyer para <b>{title}</b> en {platform.get('name', '')}...", parse_mode="HTML")

    try:
        from services.flyer_service import create_flyer_campaign
        from database.users import get_all_active_users

        campaign_data = await create_flyer_campaign(
            platform=platform,
            title=title,
            audience="all",
            yo_la_vi=False,
            admin_telegram_id=telegram_id,
        )

        flyer_bytes = campaign_data.get("flyer_bytes")
        campaign = campaign_data.get("campaign", {})
        campaign_id = str(campaign.get("id", "preview"))

        users = await get_all_active_users()
        recipient_count = len(users)

        msg_template = campaign_data.get("message_template", "")
        preview_text = (
            f"📱 <b>Preview del Flyer</b>\n\n"
            f"📺 Plataforma: {platform.get('name','')}\n"
            f"🎬 Contenido: {title}\n"
            f"👥 Destinatarios: {recipient_count}\n\n"
            f"📝 <b>Mensaje template:</b>\n{msg_template[:300]}..."
        )

        await update.message.reply_photo(
            photo=flyer_bytes,
            caption=preview_text,
            parse_mode="HTML",
            reply_markup=flyer_preview_keyboard(campaign_id, recipient_count),
        )
    except Exception as e:
        logger.error(f"Error in cmd_flyer: {e}")
        await update.message.reply_text(f"Error al crear flyer: {e}")


async def cmd_promo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Announce verified content. Usage: /promo <platform_slug> <title>"""
    if not update.message or not update.effective_user:
        return
    telegram_id = update.effective_user.id

    if not _check_admin(telegram_id):
        await update.message.reply_text("❌ Sin permisos.")
        return

    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Uso: /promo <platform_slug> <título>")
        return

    platform_slug = args[0]
    title = " ".join(args[1:])

    from database.platforms import get_platform_by_slug
    platform = await get_platform_by_slug(platform_slug)
    if not platform:
        await update.message.reply_text(f"❌ Plataforma '{platform_slug}' no encontrada.")
        return

    await update.message.reply_text(f"⏳ Verificando disponibilidad de '{title}'...")

    from services.gemini_service import verify_content_venezuela
    availability = await verify_content_venezuela(title, platform.get("name", ""))

    if availability.get("disponible"):
        from services.gemini_service import generate_synopsis_vzla
        synopsis = await generate_synopsis_vzla(title, "movie", 2024, False)
        await update.message.reply_text(
            f"✅ <b>'{title}'</b> disponible en {platform.get('name','')} Venezuela\n\n"
            f"📝 Sinopsis:\n{synopsis}\n\n"
            f"💡 Confianza: {availability.get('confianza', 'media')}",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            f"⚠️ <b>'{title}'</b> posiblemente NO disponible en Venezuela\n"
            f"Nota: {availability.get('nota', '')}",
            parse_mode="HTML",
        )


async def cmd_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show configuration menu."""
    if not update.effective_message or not update.effective_user:
        return
    telegram_id = update.effective_user.id

    if not _check_admin(telegram_id):
        await update.effective_message.reply_text("❌ Sin permisos.")
        return

    from services.exchange_service import get_current_rate, check_rate_staleness
    rate = await get_current_rate()
    stale_warning = await check_rate_staleness()

    from services.payment_service import get_payment_config
    payment_cfg = await get_payment_config()

    config_text = (
        "⚙️ <b>Configuración del Sistema</b>\n\n"
        f"💱 <b>Tasa de cambio:</b>\n"
        f"  Binance: Bs {(rate or {}).get('usd_binance', 'N/A')}/USD\n"
        f"  BCV USD: Bs {(rate or {}).get('usd_bcv', 'N/A')}/USD\n"
        f"  BCV EUR: Bs {(rate or {}).get('eur_bcv', 'N/A')}/EUR\n\n"
        f"💳 <b>Datos de pago:</b>\n"
        f"  Banco: {(payment_cfg or {}).get('banco', 'N/A')}\n"
        f"  Tel: {(payment_cfg or {}).get('telefono', 'N/A')}\n"
        f"  Cédula: {(payment_cfg or {}).get('cedula', 'N/A')}\n\n"
    )

    if stale_warning:
        config_text += f"⚠️ {stale_warning}\n\n"

    config_text += (
        "Comandos disponibles:\n"
        "/tasa <valor> - Actualizar tasa Binance\n"
        "/tasabcv <usd> <eur> - Actualizar tasas BCV\n"
        "/addcuenta - Agregar cuenta de streaming\n"
        "/addexpress <slug> <nombre> <account_id> - Agregar slot express\n"
        "/editpin <profile_id> <pin> - Editar PIN\n"
    )

    await update.effective_message.reply_text(config_text, parse_mode="HTML")


async def cmd_testllm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Quick test to verify OpenRouter LLM is responding."""
    if not update.message or not update.effective_user:
        return
    if not _check_admin(update.effective_user.id):
        await update.message.reply_text("❌ Sin permisos.")
        return

    await update.message.reply_text("⏳ Probando conexión con el LLM...")
    try:
        from services.gemini_service import _call
        response = await _call(
            messages=[{"role": "user", "content": "Responde solo: OK"}],
            temperature=0.1,
            max_tokens=10,
        )
        await update.message.reply_text(f"✅ LLM respondió: <code>{response}</code>", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Error LLM: <code>{e}</code>", parse_mode="HTML")


async def cmd_testnotif(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a test notification to all admin IDs to verify the notification pipeline."""
    if not update.message or not update.effective_user:
        return
    if not _check_admin(update.effective_user.id):
        await update.message.reply_text("❌ Sin permisos.")
        return

    await update.message.reply_text("⏳ Enviando notificación de prueba...")
    try:
        from services.notification_service import send_to_admin
        await send_to_admin(
            "🧪 <b>Test de notificación</b>\n\nSi ves este mensaje, el sistema de notificaciones funciona correctamente. ✅"
        )
        await update.message.reply_text("✅ Notificación enviada. Verifica que la recibiste.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error al enviar notificación: <code>{e}</code>", parse_mode="HTML")


async def cmd_testverif(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Test IMAP connectivity and inbox scan directly. Usage: /testverif [platform_slug]"""
    if not update.message or not update.effective_user:
        return
    if not _check_admin(update.effective_user.id):
        await update.message.reply_text("❌ Sin permisos.")
        return

    args = context.args or []
    platform_slug = args[0].lower() if args else "netflix"

    await update.message.reply_text(
        f"⏳ Probando IMAP para plataforma: <b>{platform_slug}</b>\n"
        f"Buscando en los últimos 15 minutos...",
        parse_mode="HTML",
    )

    try:
        import time
        from services.imap_reader import _imap_search_once

        imap_email = getattr(settings, "IMAP_EMAIL", "")
        imap_password = getattr(settings, "IMAP_PASSWORD", "")
        imap_host = getattr(settings, "IMAP_HOST", "imap.gmail.com")
        imap_port = int(getattr(settings, "IMAP_PORT", 993))

        if not imap_email:
            await update.message.reply_text("❌ IMAP_EMAIL no configurado en Railway.")
            return

        since_ts = time.time()  # busca en los últimos 15 min (lookback window)

        import asyncio
        code = await asyncio.to_thread(
            _imap_search_once,
            platform_slug,
            since_ts,
            imap_email,
            imap_password,
            imap_host,
            imap_port,
        )

        if code:
            await update.message.reply_text(
                f"✅ <b>IMAP funciona y encontró código</b>\n\n"
                f"📺 Plataforma: {platform_slug}\n"
                f"🔑 Código: <code>{code}</code>",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(
                f"⚠️ <b>IMAP conectó correctamente pero no encontró código</b>\n\n"
                f"📺 Plataforma: {platform_slug}\n\n"
                f"Posibles causas:\n"
                f"• No hay emails de {platform_slug} en los últimos 15 min\n"
                f"• El reenvío aún no llegó al inbox central\n"
                f"• El dominio del remitente no coincide\n\n"
                f"📧 Inbox: <code>{imap_email}</code>",
                parse_mode="HTML",
            )
    except Exception as e:
        await update.message.reply_text(
            f"❌ <b>Error IMAP</b>\n\n<code>{e}</code>\n\n"
            f"Verifica IMAP_EMAIL, IMAP_PASSWORD, IMAP_HOST en Railway.",
            parse_mode="HTML",
        )


async def _show_clients_list_callback(query, page: int = 1) -> None:
    """Edit-message version of client list for callbacks."""
    data = await get_clients_list(page=page, per_page=10)
    clients = data.get("clients", [])
    total = data.get("total", 0)
    total_pages = data.get("total_pages", 1)

    if not clients:
        await query.edit_message_text("No hay clientes registrados.")
        return

    text = f"👥 <b>Clientes</b> — Página {page}/{total_pages} (Total: {total})\n\nToca un cliente para ver su perfil y editarlo:"
    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=clients_list_keyboard(clients, page, total_pages),
    )


async def _show_client_detail_callback(query, target_id: int) -> None:
    """Edit-message version of client detail for callbacks."""
    data = await get_client_detail(target_id)
    if not data:
        await query.edit_message_text(f"❌ Cliente {target_id} no encontrado.")
        return

    user = data["user"]
    subs = data.get("subscriptions", [])
    is_blocked = user.get("status") == "blocked"
    status_icon = "🚫 Bloqueado" if is_blocked else "✅ Activo"

    text = (
        f"👤 <b>Detalle del Cliente</b>\n\n"
        f"📝 Nombre: {user.get('name') or 'N/A'}\n"
        f"👤 Username: @{user.get('username') or 'N/A'}\n"
        f"🆔 Telegram ID: <code>{user.get('telegram_id')}</code>\n"
        f"📱 Teléfono: {user.get('phone') or 'N/A'}\n"
        f"🛒 Compras: {user.get('total_purchases', 0)}\n"
        f"📊 Estado: {status_icon}\n\n"
        f"📋 <b>Últimas suscripciones ({len(subs)}):</b>\n"
    )
    for sub in subs[:5]:
        platform = (sub.get("platforms") or {}).get("name", "?")
        plan = sub.get("plan_type", "?")
        sub_status = sub.get("status", "?")
        text += f"  • {platform} ({plan}) - {sub_status}\n"

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=client_detail_keyboard(target_id, is_blocked),
    )


async def handle_admin_approve_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin approves a pending payment manually."""
    query = update.callback_query
    if not query or not update.effective_user:
        return

    telegram_id = update.effective_user.id
    if not _check_admin(telegram_id):
        await query.answer("Sin permisos", show_alert=True)
        return

    await query.answer()
    parts = query.data.split(":")
    if len(parts) < 3:
        return
    sub_id = parts[2]

    try:
        from database.subscriptions import (
            get_subscription_by_id, get_user_platform_active_subscription,
            confirm_renewal_subscription,
        )
        from database.users import increment_user_purchases
        from database.platforms import get_platform_by_id
        from services.notification_service import send_to_user
        from utils.helpers import format_datetime_vzla, venezuela_now
        from datetime import datetime, timedelta

        sub = await get_subscription_by_id(sub_id)
        if not sub:
            await query.edit_message_text("Suscripción no encontrada.")
            return

        platform_id = str(sub.get("platform_id", ""))
        plan_type = sub.get("plan_type", "monthly")
        user = sub.get("users") or {}
        user_tid = user.get("telegram_id")
        user_id = str(sub.get("user_id", ""))
        durations = {"monthly": 30, "express": 1}
        duration_days = durations.get(plan_type, 30)

        platform = await get_platform_by_id(platform_id)
        platform_label = f"{(platform or {}).get('icon_emoji','')} {(platform or {}).get('name','')}"

        # ── RENEWAL CHECK ──────────────────────────────────────────────
        existing_sub = await get_user_platform_active_subscription(user_id, platform_id)

        if existing_sub and str(existing_sub["id"]) != sub_id:
            # Perfil puede haber sido liberado por el scheduler antes de la aprobación
            profile_id = existing_sub.get("profile_id")
            if not profile_id:
                available = await get_available_profiles(platform_id, plan_type)
                if not available:
                    await query.edit_message_text("❌ No hay perfiles disponibles para asignar.")
                    return
                profile_id = str(available[0]["id"])
                await assign_profile(profile_id)
                profile = available[0]
            else:
                profile_id = str(profile_id)
                profile = existing_sub.get("profiles") or {}

            # New end_date: extend from current end_date (if future) or from now
            now = venezuela_now()
            existing_end_str = (existing_sub.get("end_date") or "")[:10]
            today_str = now.strftime("%Y-%m-%d")
            if existing_end_str > today_str:
                try:
                    y, m, d = map(int, existing_end_str.split("-"))
                    base = now.replace(year=y, month=m, day=d, hour=23, minute=59, second=59, microsecond=0)
                except Exception:
                    base = now
            else:
                base = now
            new_end_date = base + timedelta(days=duration_days)

            await confirm_renewal_subscription(str(existing_sub["id"]), profile_id, "MANUAL-ADMIN", new_end_date)
            # Delete the pending duplicate sub (not cancel — so it disappears from the UI cleanly)
            from database.subscriptions import delete_subscription
            await delete_subscription(sub_id)
            await log_admin_action(telegram_id, "approve_renewal", {"sub_id": sub_id})

            if user_tid:
                await increment_user_purchases(user_tid)
                renewal_msg = (
                    f"✅ <b>¡Renovación confirmada!</b>\n\n"
                    f"Tu suscripción de <b>{platform_label}</b> ha sido renovada.\n\n"
                    f"👤 Perfil: <b>{profile.get('profile_name', '')}</b>\n"
                    f"📅 Nueva fecha de corte: <b>{format_datetime_vzla(new_end_date)}</b>\n\n"
                    f"¡Gracias por tu preferencia! 🙌 Disfruta el streaming. 🎬"
                )
                await send_to_user(user_tid, renewal_msg)

            await query.edit_message_text(
                f"✅ Renovación #{short_id(sub_id)} aprobada.\n"
                f"Nueva fecha de corte: {format_datetime_vzla(new_end_date)}"
            )
            return

        # ── NEW SUBSCRIPTION PATH ──────────────────────────────────────
        profiles = await get_available_profiles(platform_id, plan_type)
        if not profiles:
            await query.edit_message_text(
                f"⚠️ No hay perfiles disponibles para {plan_type} en esta plataforma.\n"
                f"Agrega un perfil y aprueba manualmente."
            )
            return

        profile = profiles[0]
        profile_id = str(profile["id"])

        await confirm_subscription(sub_id, profile_id, "MANUAL-ADMIN", "manual_approval")
        await assign_profile(profile_id)
        await log_admin_action(telegram_id, "approve_payment", {"sub_id": sub_id})

        if user_tid:
            await increment_user_purchases(user_tid)

        if user_tid:
            from database.accounts import get_account_by_id
            from bot.messages import ACCESS_DELIVERED, ACCESS_INSTRUCTIONS, PIN_LINE, PAYMENT_CONFIRMED

            account = await get_account_by_id(str(profile.get("account_id", "")))
            now = venezuela_now()
            end_date = now + timedelta(days=duration_days)

            pin_line = PIN_LINE.format(pin=profile.get("pin")) if profile.get("pin") else ""
            platform_slug = (platform or {}).get("slug", "netflix")
            instructions_tpl = ACCESS_INSTRUCTIONS.get(platform_slug, "")
            instructions = instructions_tpl.format(profile_name=profile.get("profile_name", ""))

            access_text = ACCESS_DELIVERED.format(
                platform=platform_label,
                profile_name=profile.get("profile_name", ""),
                email=(account or {}).get("email", ""),
                password=(account or {}).get("password", ""),
                pin_line=pin_line,
                instructions=instructions,
            )
            confirmed_text = PAYMENT_CONFIRMED.format(
                platform=platform_label,
                start_date=format_datetime_vzla(now),
                end_date=format_datetime_vzla(end_date),
                reference=sub.get("payment_reference") or "N/A",
            )
            await send_to_user(user_tid, confirmed_text)
            await send_to_user(user_tid, access_text)

        await query.edit_message_text(f"✅ Pago #{short_id(sub_id)} aprobado y acceso enviado.")
    except Exception as e:
        logger.error(f"Error in handle_admin_approve_payment: {e}")
        await query.edit_message_text(f"Error al aprobar: {e}")


async def handle_admin_reject_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin rejects a pending payment."""
    query = update.callback_query
    if not query or not update.effective_user:
        return

    telegram_id = update.effective_user.id
    if not _check_admin(telegram_id):
        await query.answer("Sin permisos", show_alert=True)
        return

    await query.answer()
    parts = query.data.split(":")
    if len(parts) < 3:
        return
    sub_id = parts[2]

    try:
        from database.subscriptions import get_subscription_by_id
        sub = await get_subscription_by_id(sub_id)
        if not sub:
            await query.edit_message_text("Suscripción no encontrada.")
            return

        from database.subscriptions import delete_subscription
        await delete_subscription(sub_id)
        await log_admin_action(telegram_id, "reject_payment", {"sub_id": sub_id})

        # Notify user — invite to restart
        sub_user = sub.get("users") or {}
        user = sub_user
        user_tid = user.get("telegram_id")
        platform = sub.get("platforms") or {}
        platform_name = f"{platform.get('icon_emoji','')} {platform.get('name','')}".strip()
        if user_tid:
            from services.notification_service import send_to_user
            from telegram import InlineKeyboardMarkup, InlineKeyboardButton
            restart_keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Iniciar nuevo pedido", callback_data="menu:subscribe")
            ]])
            await send_to_user(
                user_tid,
                f"❌ <b>Comprobante no aprobado</b>\n\n"
                f"Hola, tu comprobante de pago para <b>{platform_name}</b> "
                f"no pudo ser verificado por nuestro equipo.\n\n"
                f"<b>Posibles motivos:</b>\n"
                f"• El monto no coincide con el precio exacto\n"
                f"• La imagen no es legible o está recortada\n"
                f"• La referencia ya fue registrada anteriormente\n\n"
                f"Por favor inicia un nuevo pedido y envía el comprobante correcto. "
                f"Si crees que es un error, contáctanos. 📞",
                keyboard=restart_keyboard,
            )

        await query.edit_message_text(f"❌ Pago #{short_id(sub_id)} rechazado — cliente notificado con opción de reiniciar.")
    except Exception as e:
        logger.error(f"Error in handle_admin_reject_payment: {e}")
        await query.edit_message_text(f"Error al rechazar: {e}")


async def handle_campaign_send(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a campaign to users."""
    query = update.callback_query
    if not query or not update.effective_user:
        return

    telegram_id = update.effective_user.id
    if not _check_admin(telegram_id):
        await query.answer("Sin permisos", show_alert=True)
        return

    await query.answer()
    parts = query.data.split(":")
    campaign_id = parts[2] if len(parts) > 2 else ""

    try:
        from database import get_supabase
        from database.users import get_all_active_users
        from services.notification_service import broadcast_campaign

        sb = get_supabase()
        campaign_result = sb.table("campaigns").select("*").eq("id", campaign_id).limit(1).execute()
        campaign = campaign_result.data if campaign_result else None

        if not campaign:
            await query.edit_message_text("Campaña no encontrada.")
            return

        users = await get_all_active_users()
        user_ids = [u["telegram_id"] for u in users if u.get("telegram_id")]

        await query.edit_message_text(f"📤 Enviando campaña a {len(user_ids)} usuarios...")

        msg_template = campaign.get("synopsis_vzla", "") or ""
        flyer_url = campaign.get("flyer_image_url")

        result = await broadcast_campaign(campaign_id, user_ids, msg_template, flyer_url)
        await log_admin_action(telegram_id, "send_campaign", {"campaign_id": campaign_id, "sent": result["sent"]})

        await query.edit_message_text(
            f"✅ <b>Campaña enviada</b>\n\n"
            f"✅ Enviados: {result['sent']}\n"
            f"❌ Fallidos: {result['failed']}",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Error sending campaign: {e}")
        await query.edit_message_text(f"Error al enviar campaña: {e}")


async def cmd_stock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stock overview — shows available profiles per platform."""
    availability = await get_platform_availability()
    lines = ["📦 <b>Stock disponible:</b>\n"]
    for p in availability:
        icon = p.get("icon_emoji", "📺")
        name = p.get("name", "")
        monthly = p.get("monthly_available", 0)
        express = p.get("express_available", 0)
        lines.append(f"{icon} <b>{name}</b> — Mensual: {monthly}  Express: {express}")
    msg = "\n".join(lines) or "Sin datos de stock."
    await update.effective_message.reply_text(msg, parse_mode="HTML")


async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Router for admin callback queries."""
    query = update.callback_query
    if not query or not update.effective_user:
        return

    if not _check_admin(update.effective_user.id):
        await query.answer("Sin permisos de admin", show_alert=True)
        return

    data = query.data
    admin_telegram_id = update.effective_user.id
    if data == "admin:pending":
        await query.answer()
        await cmd_pendientes(update, context)
    elif data == "admin:clients":
        await query.answer()
        await _show_clients_list_callback(query, page=1)
    elif data == "admin:income":
        await query.answer()
        await cmd_ingresos(update, context)
    elif data.startswith("admin:approve:"):
        await handle_admin_approve_payment(update, context)
    elif data.startswith("admin:reject:"):
        await handle_admin_reject_payment(update, context)
    elif data.startswith("campaign:send:"):
        await handle_campaign_send(update, context)
    elif data == "admin:broadcast_do":
        await handle_broadcast_do(update, context)
    elif data == "admin:broadcast_cancel":
        await query.answer()
        clear_user_state(update.effective_user.id)
        await query.edit_message_text("Broadcast cancelado.")
    elif data == "admin:update_rate":
        await query.answer()
        rate = await get_current_rate()
        current = (rate or {}).get("usd_binance", "N/A")
        await query.edit_message_text(
            f"💱 <b>Tasa actual Binance:</b> Bs {current}/USD\n\n"
            f"Para actualizar manualmente: <code>/tasa 36.50</code>\n"
            f"O usa el botón 🔄 Auto-fetch para obtenerla de Binance P2P.",
            parse_mode="HTML",
        )
    elif data.startswith("admin:edit_name:"):
        await query.answer()
        target_id = int(data.split(":")[2])
        set_user_state(admin_telegram_id, f"admin:edit_client:name:{target_id}")
        await query.edit_message_text(
            f"✏️ Ingresa el <b>nuevo nombre</b> para el cliente <code>{target_id}</code>:\n\n"
            f"(Escribe el nombre o /cancelar para abortar)",
            parse_mode="HTML",
        )

    elif data.startswith("admin:edit_phone:"):
        await query.answer()
        target_id = int(data.split(":")[2])
        set_user_state(admin_telegram_id, f"admin:edit_client:phone:{target_id}")
        await query.edit_message_text(
            f"📱 Ingresa el <b>nuevo teléfono</b> para el cliente <code>{target_id}</code>:\n\n"
            f"(Ej: 04141234567 o /cancelar para abortar)",
            parse_mode="HTML",
        )

    elif data.startswith("admin:block:"):
        await query.answer()
        target_id = int(data.split(":")[2])
        await block_user(target_id)
        await log_admin_action(admin_telegram_id, "block_user", {"target_telegram_id": target_id})
        await _show_client_detail_callback(query, target_id)

    elif data.startswith("admin:unblock:"):
        await query.answer()
        target_id = int(data.split(":")[2])
        await unblock_user(target_id)
        await log_admin_action(admin_telegram_id, "unblock_user", {"target_telegram_id": target_id})
        await _show_client_detail_callback(query, target_id)

    elif data.startswith("admin:clients_page:"):
        await query.answer()
        page = int(data.split(":")[2])
        await _show_clients_list_callback(query, page=page)

    elif data.startswith("admin:client_detail:"):
        await query.answer()
        target_id = int(data.split(":")[2])
        await _show_client_detail_callback(query, target_id)

    elif data == "admin:stock":
        await query.answer()
        await cmd_stock(update, context)

    elif data == "admin:config":
        await query.answer()
        await cmd_config(update, context)

    elif data == "admin:back":
        await query.answer()
        await admin_dashboard(update, context)
