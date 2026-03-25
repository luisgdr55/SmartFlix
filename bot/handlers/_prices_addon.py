"""
Gestión de precios — addon para admin.py
Importar desde admin.py e incluir en main.py.
"""
from __future__ import annotations

import logging

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from bot.keyboards import (
    prices_menu_keyboard,
    platform_price_edit_keyboard,
    confirm_price_keyboard,
)
from bot.middleware import set_user_state, get_user_data, clear_user_state
from config import settings
from database.platforms import get_active_platforms, get_platform_by_id, update_platform_prices
from database.users import log_admin_action
from services.exchange_service import update_rate, get_current_rate, fetch_binance_p2p_rate
from utils.validators import is_admin

logger = logging.getLogger(__name__)


def _check_admin(telegram_id: int) -> bool:
    return is_admin(telegram_id, settings.ADMIN_TELEGRAM_IDS)


async def cmd_precios(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Muestra el menú de gestión de precios de todas las plataformas."""
    if not update.message or not update.effective_user:
        return
    telegram_id = update.effective_user.id

    if not _check_admin(telegram_id):
        await update.message.reply_text("Sin permisos.")
        return

    platforms = await get_active_platforms()
    rate = await get_current_rate()
    rate_val = (rate or {}).get("usd_binance", "N/A")

    text = (
        "💰 <b>Gestión de Precios</b>\n\n"
        f"💱 Tasa Binance actual: <b>Bs {rate_val}/USD</b>\n\n"
        "Selecciona una plataforma para editar sus precios:\n"
        "<i>M = Mensual  ·  E = Express</i>"
    )
    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=prices_menu_keyboard(platforms),
    )


async def handle_prices_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Router para todos los callbacks prices:*

      prices:menu                          → lista de plataformas
      prices:platform:<id>                 → precios actuales + botones
      prices:edit:<id>:<type>              → solicita nuevo valor (estado en Redis)
      prices:confirm:<id>:<type>:<value>   → guarda en BD
      prices:tasa                          → solicita nueva tasa manual
      prices:autotasa                      → fetch automático Binance P2P
      prices:autotasa_save:<value>         → confirma y guarda la tasa auto
    """
    query = update.callback_query
    if not query or not update.effective_user:
        return

    telegram_id = update.effective_user.id
    if not _check_admin(telegram_id):
        await query.answer("Sin permisos de admin", show_alert=True)
        return

    await query.answer()
    data = query.data
    parts = data.split(":")

    # ── prices:menu ──────────────────────────────────────────────
    if data == "prices:menu":
        platforms = await get_active_platforms()
        rate = await get_current_rate()
        rate_val = (rate or {}).get("usd_binance", "N/A")
        text = (
            "💰 <b>Gestión de Precios</b>\n\n"
            f"💱 Tasa Binance: <b>Bs {rate_val}/USD</b>\n\n"
            "Selecciona una plataforma para editar:\n"
            "<i>M = Mensual  ·  E = Express</i>"
        )
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=prices_menu_keyboard(platforms),
        )

    # ── prices:platform:<id> ──────────────────────────────────────
    elif data.startswith("prices:platform:") and len(parts) == 3:
        platform_id = parts[2]
        platform = await get_platform_by_id(platform_id)
        if not platform:
            await query.edit_message_text("Plataforma no encontrada.")
            return

        icon = platform.get("icon_emoji", "📺")
        name = platform.get("name", "")
        monthly = platform.get("monthly_price_usd") or "—"
        express = platform.get("express_price_usd") or "—"

        rate = await get_current_rate()
        rate_val = float((rate or {}).get("usd_binance", 0) or 0)

        def bs_ref(usd):
            if isinstance(usd, (int, float)) and rate_val:
                return f"  ≈ Bs {usd * rate_val:,.0f}"
            return ""

        text = (
            f"{icon} <b>{name}</b> — Precios actuales\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"📅 Mensual:  <b>${monthly}</b>{bs_ref(monthly)}\n"
            f"⚡ Express:  <b>${express}</b>{bs_ref(express)}\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Elige qué precio modificar:"
        )
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=platform_price_edit_keyboard(platform_id, platform),
        )

    # ── prices:edit:<id>:<type> ───────────────────────────────────
    elif data.startswith("prices:edit:") and len(parts) == 4:
        platform_id = parts[2]
        price_type = parts[3]  # monthly | express | all

        platform = await get_platform_by_id(platform_id)
        if not platform:
            await query.edit_message_text("Plataforma no encontrada.")
            return

        icon = platform.get("icon_emoji", "📺")
        name = platform.get("name", "")

        set_user_state(telegram_id, f"admin:precios:{platform_id}:{price_type}")

        if price_type == "all":
            hint = (
                "Envía los <b>2 precios en USD</b> separados por espacio:\n"
                "<code>mensual express</code>\n\n"
                "Ejemplo: <code>5.00 1.00</code>"
            )
        else:
            current = platform.get(f"{price_type}_price_usd") or "0"
            labels = {"monthly": "Mensual", "express": "Express 24h"}
            hint = (
                f"Editando: <b>{labels.get(price_type, price_type)}</b>\n"
                f"Precio actual: <b>${current}</b>\n\n"
                "Envía el nuevo precio en USD:\n"
                "Ejemplo: <code>5.00</code>"
            )

        await query.edit_message_text(
            f"{icon} <b>{name}</b>\n\n{hint}",
            parse_mode="HTML",
        )

    # ── prices:confirm:<id>:<type>:<value> ────────────────────────
    elif data.startswith("prices:confirm:") and len(parts) >= 5:
        platform_id = parts[2]
        price_type = parts[3]
        value_str = parts[4]

        try:
            new_price = float(value_str)
        except ValueError:
            await query.edit_message_text("Valor inválido.")
            return

        platform = await get_platform_by_id(platform_id)
        if not platform:
            await query.edit_message_text("Plataforma no encontrada.")
            return

        monthly = float(platform.get("monthly_price_usd") or 0)
        express = float(platform.get("express_price_usd") or 0)

        if price_type == "monthly":
            monthly = new_price
        elif price_type == "express":
            express = new_price

        success = await update_platform_prices(platform_id, monthly, express)
        if not success:
            await query.edit_message_text("Error al guardar precio.")
            return

        await log_admin_action(
            telegram_id, "update_price",
            {"platform_id": platform_id, "type": price_type, "new_price": new_price},
        )
        clear_user_state(telegram_id)

        icon = platform.get("icon_emoji", "📺")
        name = platform.get("name", "")
        labels = {"monthly": "Mensual", "express": "Express"}
        await query.edit_message_text(
            f"✅ <b>{icon} {name}</b>\n"
            f"Precio {labels.get(price_type, price_type)} actualizado:\n\n"
            f"<b>${new_price:.2f} USD</b>\n\n"
            "Usa /precios para más cambios.",
            parse_mode="HTML",
        )

    # ── prices:tasa ───────────────────────────────────────────────
    elif data == "prices:tasa":
        rate = await get_current_rate()
        current = (rate or {}).get("usd_binance", "N/A")
        set_user_state(telegram_id, "admin:tasa_manual")
        await query.edit_message_text(
            f"💱 <b>Actualizar Tasa Binance</b>\n\n"
            f"Tasa actual: <b>Bs {current}/USD</b>\n\n"
            "Envía el nuevo valor en Bs por USD:\n"
            "Ejemplo: <code>58.30</code>",
            parse_mode="HTML",
        )

    # ── prices:autotasa ───────────────────────────────────────────
    elif data == "prices:autotasa":
        await query.edit_message_text("⏳ Consultando Binance P2P USDT/VES...")
        p2p_rate = await fetch_binance_p2p_rate()
        if p2p_rate is None:
            await query.edit_message_text(
                "No se pudo obtener la tasa de Binance P2P.\n\n"
                "Verifica la conexión o actualiza manualmente con /tasa"
            )
            return

        confirm_kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    f"✅ Guardar Bs {p2p_rate:.2f}/USD",
                    callback_data=f"prices:autotasa_save:{p2p_rate}",
                ),
                InlineKeyboardButton("❌ Cancelar", callback_data="prices:menu"),
            ]
        ])
        await query.edit_message_text(
            f"📡 <b>Tasa obtenida de Binance P2P</b>\n\n"
            f"USDT/VES — promedio de los 3 mejores vendedores:\n"
            f"<b>Bs {p2p_rate:.2f} / USD</b>\n\n"
            "¿Guardar esta tasa?",
            parse_mode="HTML",
            reply_markup=confirm_kb,
        )

    # ── prices:autotasa_save:<value> ──────────────────────────────
    elif data.startswith("prices:autotasa_save:") and len(parts) == 3:
        try:
            new_rate = float(parts[2])
        except ValueError:
            await query.edit_message_text("Valor inválido.")
            return

        success = await update_rate(new_rate, telegram_id)
        if success:
            await log_admin_action(
                telegram_id, "autofetch_rate_binance", {"rate": new_rate}
            )
            await query.edit_message_text(
                f"✅ <b>Tasa Binance actualizada</b>\n\n"
                f"Nueva tasa: <b>Bs {new_rate:.2f}/USD</b>\n"
                f"Origen: Binance P2P automático",
                parse_mode="HTML",
            )
        else:
            await query.edit_message_text("Error al guardar la tasa.")


async def handle_price_text_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    state: str,
) -> None:
    """
    Procesa texto libre cuando el admin edita precios o la tasa.
    Llamado desde el router de texto de main.py con el state ya conocido.

    States manejados:
      admin:tasa_manual
      admin:precios:<platform_id>:<price_type>
    """
    if not update.message or not update.effective_user:
        return
    telegram_id = update.effective_user.id
    text = update.message.text.strip()

    # ── Tasa manual ──────────────────────────────────────────────
    if state == "admin:tasa_manual":
        try:
            new_rate = float(text.replace(",", "."))
            if new_rate <= 0:
                raise ValueError("negative")
        except ValueError:
            await update.message.reply_text(
                "Valor inválido. Ingresa un número positivo, ej: <code>58.30</code>",
                parse_mode="HTML",
            )
            return

        success = await update_rate(new_rate, telegram_id)
        if success:
            await log_admin_action(
                telegram_id, "update_rate_binance_manual", {"rate": new_rate}
            )
            clear_user_state(telegram_id)
            await update.message.reply_text(
                f"✅ <b>Tasa Binance actualizada:</b> Bs {new_rate:.2f}/USD",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text("Error al guardar la tasa.")
        return

    # ── Precio de plataforma ─────────────────────────────────────
    parts = state.split(":")
    if len(parts) < 4 or parts[1] != "precios":
        return

    platform_id = parts[2]
    price_type = parts[3]

    platform = await get_platform_by_id(platform_id)
    if not platform:
        await update.message.reply_text("Plataforma no encontrada. Usa /precios.")
        clear_user_state(telegram_id)
        return

    icon = platform.get("icon_emoji", "📺")
    name = platform.get("name", "")

    # ── Guardar los 2 precios de una vez ─────────────────────────
    if price_type == "all":
        values = text.replace(",", ".").split()
        if len(values) != 2:
            await update.message.reply_text(
                "Necesito exactamente 2 valores separados por espacio.\n"
                "Ejemplo: <code>5.00 1.00</code>",
                parse_mode="HTML",
            )
            return
        try:
            monthly, express = [float(v) for v in values]
            if any(p < 0 for p in [monthly, express]):
                raise ValueError("negative")
        except ValueError:
            await update.message.reply_text("Valores inválidos. Usa números positivos.")
            return

        success = await update_platform_prices(platform_id, monthly, express)
        if not success:
            await update.message.reply_text("Error al guardar precios.")
            return

        await log_admin_action(
            telegram_id, "update_all_prices",
            {"platform_id": platform_id, "monthly": monthly, "express": express},
        )
        clear_user_state(telegram_id)
        await update.message.reply_text(
            f"✅ <b>{icon} {name}</b> — Precios actualizados:\n\n"
            f"📅 Mensual:  <b>${monthly:.2f}</b>\n"
            f"⚡ Express:  <b>${express:.2f}</b>\n\n"
            "Usa /precios para más cambios.",
            parse_mode="HTML",
        )
        return

    # ── Precio individual → pedir confirmación ────────────────────
    try:
        new_price = float(text.replace(",", "."))
        if new_price < 0:
            raise ValueError("negative")
    except ValueError:
        await update.message.reply_text(
            "Valor inválido. Ingresa un número positivo en USD.\n"
            "Ejemplo: <code>5.00</code>",
            parse_mode="HTML",
        )
        return

    labels = {"monthly": "Mensual", "express": "Express 24h"}
    await update.message.reply_text(
        f"{icon} <b>{name}</b> — Confirmar cambio\n\n"
        f"Tipo: <b>{labels.get(price_type, price_type)}</b>\n"
        f"Nuevo precio: <b>${new_price:.2f} USD</b>",
        parse_mode="HTML",
        reply_markup=confirm_price_keyboard(platform_id, price_type, new_price),
    )
