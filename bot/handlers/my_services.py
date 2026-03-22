from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.keyboards import my_services_keyboard, renewal_keyboard, main_menu_keyboard
from bot.messages import MY_SERVICES_ACTIVE, MY_SERVICES_EMPTY, SERVICE_DETAIL
from database.users import get_user_by_telegram_id
from database.subscriptions import get_user_active_subscriptions
from utils.helpers import format_datetime_vzla, days_remaining, short_id

logger = logging.getLogger(__name__)


async def show_my_services(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show all active subscriptions for the user."""
    query = update.callback_query
    message = update.message
    effective_user = update.effective_user

    if not effective_user:
        return

    telegram_id = effective_user.id

    try:
        user = await get_user_by_telegram_id(telegram_id)
        if not user:
            text = "Por favor usa /start para registrarte."
            if query:
                await query.answer()
                await query.edit_message_text(text)
            elif message:
                await message.reply_text(text)
            return

        subscriptions = await get_user_active_subscriptions(str(user["id"]))

        if not subscriptions:
            if query:
                await query.answer()
                await query.edit_message_text(
                    MY_SERVICES_EMPTY,
                    parse_mode="HTML",
                    reply_markup=main_menu_keyboard(),
                )
            elif message:
                await message.reply_text(
                    MY_SERVICES_EMPTY,
                    parse_mode="HTML",
                    reply_markup=main_menu_keyboard(),
                )
            return

        # Build services list text
        services_text = ""
        for sub in subscriptions:
            platform = sub.get("platforms") or {}
            profile = sub.get("profiles") or {}
            icon = platform.get("icon_emoji", "📺")
            platform_name = platform.get("name", "?")
            plan_type = sub.get("plan_type", "monthly")
            plan_labels = {"monthly": "Mensual", "express": "Express 24h", "week": "Semanal"}
            plan_label = plan_labels.get(plan_type, plan_type)
            profile_name = profile.get("profile_name", "N/A")

            from datetime import datetime
            end_date_str = sub.get("end_date")
            end_dt = None
            if end_date_str:
                try:
                    end_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
                except Exception:
                    pass

            days_left = days_remaining(end_dt) if end_dt else 0
            end_date_formatted = format_datetime_vzla(end_dt) if end_dt else "N/A"

            services_text += SERVICE_DETAIL.format(
                icon=icon,
                platform=platform_name,
                plan_type=plan_label,
                profile_name=profile_name,
                end_date=end_date_formatted,
                days_left=days_left,
                sub_id=short_id(str(sub.get("id", ""))),
            ) + "\n"

        full_text = MY_SERVICES_ACTIVE.format(count=len(subscriptions), services_list=services_text)

        if query:
            await query.answer()
            await query.edit_message_text(
                full_text,
                parse_mode="HTML",
                reply_markup=my_services_keyboard(subscriptions),
            )
        elif message:
            await message.reply_text(
                full_text,
                parse_mode="HTML",
                reply_markup=my_services_keyboard(subscriptions),
            )
    except Exception as e:
        logger.error(f"Error in show_my_services: {e}")
        if query:
            await query.answer("Error al cargar servicios")


async def handle_service_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show detail view of a specific subscription."""
    query = update.callback_query
    if not query:
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

        platform = sub.get("platforms") or {}
        profile = sub.get("profiles") or {}
        from datetime import datetime

        end_date_str = sub.get("end_date")
        end_dt = None
        if end_date_str:
            try:
                end_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            except Exception:
                pass

        days_left = days_remaining(end_dt) if end_dt else 0
        end_date_formatted = format_datetime_vzla(end_dt) if end_dt else "N/A"

        plan_labels = {"monthly": "Mensual", "express": "Express 24h", "week": "Semanal"}
        plan_label = plan_labels.get(sub.get("plan_type", "monthly"), "Mensual")

        detail_text = (
            f"{platform.get('icon_emoji','📺')} <b>{platform.get('name','')}</b>\n\n"
            f"📅 Plan: <b>{plan_label}</b>\n"
            f"👤 Perfil: <b>{profile.get('profile_name', 'N/A')}</b>\n"
            f"⏰ Vence: <b>{end_date_formatted}</b>\n"
            f"⏳ Días restantes: <b>{days_left}</b>\n"
            f"🔖 ID: <code>#{short_id(sub_id)}</code>\n\n"
            f"Estado: {'✅ Activo' if sub.get('status') == 'active' else '⏳ ' + sub.get('status', '?')}"
        )

        keyboard = renewal_keyboard(str(sub.get("platform_id", "")), sub.get("plan_type", "monthly"))

        await query.edit_message_text(
            detail_text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    except Exception as e:
        logger.error(f"Error in handle_service_detail: {e}")
        await query.edit_message_text("Error al cargar detalles.")


async def handle_renewal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle renewal callback - redirect to platform selection."""
    query = update.callback_query
    if not query or not update.effective_user:
        return
    await query.answer()

    parts = query.data.split(":")
    if len(parts) < 3:
        return
    plan_type = parts[1]
    platform_id = parts[2]

    from bot.middleware import set_user_data, set_user_state
    set_user_data(update.effective_user.id, "selected_platform_id", platform_id)
    set_user_data(update.effective_user.id, "selected_plan_type", plan_type)

    # Redirect to confirmation
    await handle_service_confirm_renewal(update, context, platform_id, plan_type)


async def handle_service_confirm_renewal(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    platform_id: str,
    plan_type: str,
) -> None:
    """Show renewal confirmation."""
    query = update.callback_query
    if not query:
        return

    try:
        from database.platforms import get_platform_by_id
        from services.exchange_service import calculate_price_bs, get_current_rate
        from bot.keyboards import confirm_order_keyboard

        platform = await get_platform_by_id(platform_id)
        if not platform:
            await query.edit_message_text("Plataforma no encontrada.")
            return

        price_field = {"monthly": "monthly_price_usd", "express": "express_price_usd", "week": "week_price_usd"}.get(plan_type, "monthly_price_usd")
        price_usd = float(platform.get(price_field) or 4.50)
        price_bs = await calculate_price_bs(price_usd)
        rate = await get_current_rate()
        rate_value = float((rate or {}).get("usd_binance") or 36.0)

        telegram_id = update.effective_user.id  # type: ignore
        from bot.middleware import set_user_data
        set_user_data(telegram_id, "price_usd", str(price_usd))
        set_user_data(telegram_id, "price_bs", str(price_bs))
        set_user_data(telegram_id, "rate_used", str(rate_value))

        plan_labels = {"monthly": "Mensual (30 días)", "express": "Express (24h)", "week": "Semanal (7 días)"}
        plan_label = plan_labels.get(plan_type, plan_type)

        confirm_text = (
            f"🔄 <b>Confirmar Renovación</b>\n\n"
            f"📺 Plataforma: <b>{platform.get('icon_emoji','')} {platform.get('name','')}</b>\n"
            f"📅 Plan: <b>{plan_label}</b>\n"
            f"💵 Precio: <b>${price_usd:.2f} USD</b> = <b>Bs {price_bs:,.2f}</b>\n"
            f"📊 Tasa: Bs {rate_value:.2f}/USD\n\n"
            f"¿Confirmamos la renovación?"
        )

        await query.edit_message_text(
            confirm_text,
            parse_mode="HTML",
            reply_markup=confirm_order_keyboard(platform_id, plan_type),
        )
    except Exception as e:
        logger.error(f"Error in handle_service_confirm_renewal: {e}")
        await query.edit_message_text("Error al procesar renovación.")
