from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove


def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Main menu with primary actions."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📺 Suscripción Mensual", callback_data="menu:subscribe"),
            InlineKeyboardButton("⚡ Express 24h", callback_data="menu:express"),
        ],
        [
            InlineKeyboardButton("📅 Pack Semanal", callback_data="menu:week"),
            InlineKeyboardButton("📋 Mis Servicios", callback_data="menu:my_services"),
        ],
        [
            InlineKeyboardButton("🆘 Soporte", callback_data="menu:support"),
        ],
    ])


def platforms_keyboard(platforms_with_stock: list[dict], plan_type: str) -> InlineKeyboardMarkup:
    """Platform selection keyboard with availability counts."""
    buttons = []
    for p in platforms_with_stock:
        icon = p.get("icon_emoji", "📺")
        name = p.get("name", "")
        count = p.get(f"{plan_type}_available", 0)
        if plan_type == "monthly":
            count = p.get("monthly_available", 0)
        elif plan_type == "express":
            count = p.get("express_available", 0)
        elif plan_type == "week":
            count = p.get("week_available", 0)
        stock_label = f"({count} disp.)" if count > 0 else "(Sin stock)"
        buttons.append([
            InlineKeyboardButton(
                f"{icon} {name} {stock_label}",
                callback_data=f"platform:{plan_type}:{p['platform_id']}",
            )
        ])
    buttons.append([InlineKeyboardButton("🔙 Volver", callback_data="menu:main")])
    return InlineKeyboardMarkup(buttons)


def confirm_order_keyboard(platform_id: str, plan_type: str) -> InlineKeyboardMarkup:
    """Confirm or cancel an order."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirmar pedido", callback_data=f"confirm:{plan_type}:{platform_id}"),
            InlineKeyboardButton("❌ Cambiar", callback_data=f"menu:{plan_type if plan_type != 'monthly' else 'subscribe'}"),
        ],
    ])


def payment_received_keyboard() -> InlineKeyboardMarkup:
    """Keyboard shown after user sends payment comprobante."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Reenviar comprobante", callback_data="payment:resend")],
        [InlineKeyboardButton("🆘 Contactar soporte", callback_data="support:contact_admin")],
    ])


def my_services_keyboard(subscriptions: list[dict]) -> InlineKeyboardMarkup:
    """Services list keyboard with renewal options."""
    buttons = []
    for sub in subscriptions[:5]:
        platform = sub.get("platforms") or {}
        icon = platform.get("icon_emoji", "📺")
        name = platform.get("name", "?")
        plan = sub.get("plan_type", "")
        sub_id = str(sub.get("id", ""))[:8]
        buttons.append([
            InlineKeyboardButton(
                f"{icon} {name} ({plan}) - #{sub_id}",
                callback_data=f"service:detail:{sub['id']}",
            )
        ])
    buttons.append([InlineKeyboardButton("🔙 Menú principal", callback_data="menu:main")])
    return InlineKeyboardMarkup(buttons)


def support_keyboard() -> InlineKeyboardMarkup:
    """Support options keyboard."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📧 Ver mis credenciales", callback_data="support:credentials")],
        [InlineKeyboardButton("🔐 Código de verificación", callback_data="support:verification_code")],
        [InlineKeyboardButton("🔧 Guía de problemas", callback_data="support:troubleshooting")],
        [InlineKeyboardButton("📊 Estado de mi perfil", callback_data="support:profile_status")],
        [InlineKeyboardButton("👨‍💼 Hablar con soporte", callback_data="support:contact_admin")],
        [InlineKeyboardButton("🔙 Menú principal", callback_data="menu:main")],
    ])


def admin_dashboard_keyboard() -> InlineKeyboardMarkup:
    """Admin panel action keyboard."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⏳ Pendientes", callback_data="admin:pending"),
            InlineKeyboardButton("👥 Clientes", callback_data="admin:clients"),
        ],
        [
            InlineKeyboardButton("💵 Ingresos", callback_data="admin:income"),
            InlineKeyboardButton("📦 Stock", callback_data="admin:stock"),
        ],
        [
            InlineKeyboardButton("💰 Precios", callback_data="prices:menu"),
            InlineKeyboardButton("💱 Tasa Binance", callback_data="prices:tasa"),
        ],
        [
            InlineKeyboardButton("🔄 Auto-fetch tasa", callback_data="prices:autotasa"),
            InlineKeyboardButton("⚙️ Config", callback_data="admin:config"),
        ],
    ])


def flyer_preview_keyboard(campaign_id: str, recipient_count: int) -> InlineKeyboardMarkup:
    """Flyer campaign preview actions."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                f"📤 Enviar ahora ({recipient_count} usuarios)",
                callback_data=f"campaign:send:{campaign_id}",
            )
        ],
        [
            InlineKeyboardButton("✏️ Editar mensaje", callback_data=f"campaign:edit:{campaign_id}"),
            InlineKeyboardButton("🕐 Programar", callback_data=f"campaign:schedule:{campaign_id}"),
        ],
        [InlineKeyboardButton("❌ Cancelar", callback_data=f"campaign:cancel:{campaign_id}")],
    ])


def renewal_keyboard(platform_id: str, plan_type: str) -> InlineKeyboardMarkup:
    """Renewal options keyboard."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Renovar ahora", callback_data=f"renew:{plan_type}:{platform_id}")],
        [InlineKeyboardButton("📋 Ver planes", callback_data="menu:subscribe")],
        [InlineKeyboardButton("🏠 Menú principal", callback_data="menu:main")],
    ])


def express_no_stock_keyboard(platform_id: str) -> InlineKeyboardMarkup:
    """Keyboard when no express stock is available."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔔 Unirme a lista de espera", callback_data=f"queue:join:{platform_id}")],
        [InlineKeyboardButton("📺 Ver otras plataformas", callback_data="menu:express")],
        [InlineKeyboardButton("📅 Plan mensual", callback_data="menu:subscribe")],
        [InlineKeyboardButton("🔙 Menú principal", callback_data="menu:main")],
    ])


def platform_select_for_support(subscriptions: list[dict]) -> InlineKeyboardMarkup:
    """Select platform for support flow."""
    buttons = []
    seen = set()
    for sub in subscriptions:
        platform = sub.get("platforms") or {}
        p_id = sub.get("platform_id")
        if p_id in seen:
            continue
        seen.add(p_id)
        icon = platform.get("icon_emoji", "📺")
        name = platform.get("name", "?")
        buttons.append([
            InlineKeyboardButton(f"{icon} {name}", callback_data=f"support:platform:{p_id}:{sub['id']}")
        ])
    buttons.append([InlineKeyboardButton("🔙 Soporte", callback_data="menu:support")])
    return InlineKeyboardMarkup(buttons)


def pending_payment_keyboard(sub_id: str) -> InlineKeyboardMarkup:
    """Admin actions on a pending payment."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Aprobar", callback_data=f"admin:approve:{sub_id}"),
            InlineKeyboardButton("❌ Rechazar", callback_data=f"admin:reject:{sub_id}"),
        ],
    ])


def paginator_keyboard(current_page: int, total_pages: int, base_callback: str) -> InlineKeyboardMarkup:
    """Pagination keyboard."""
    buttons = []
    row = []
    if current_page > 1:
        row.append(InlineKeyboardButton("⬅️ Anterior", callback_data=f"{base_callback}:{current_page - 1}"))
    if current_page < total_pages:
        row.append(InlineKeyboardButton("Siguiente ➡️", callback_data=f"{base_callback}:{current_page + 1}"))
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("🔙 Atrás", callback_data="admin:back")])
    return InlineKeyboardMarkup(buttons)


# ─────────────────────────────────────────────────────────────────
# GESTIÓN DE PRECIOS (admin)
# ─────────────────────────────────────────────────────────────────

def prices_menu_keyboard(platforms: list[dict]) -> InlineKeyboardMarkup:
    """List all platforms for price editing."""
    buttons = []
    for p in platforms:
        icon = p.get("icon_emoji", "📺")
        name = p.get("name", "")
        pid = str(p.get("id", ""))
        monthly = p.get("monthly_price_usd") or 0
        express = p.get("express_price_usd") or 0
        week = p.get("week_price_usd") or 0
        label = f"{icon} {name}  |  M:${monthly}  E:${express}  S:${week}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"prices:platform:{pid}")])
    buttons.append([
        InlineKeyboardButton("💱 Tasa Binance", callback_data="prices:tasa"),
        InlineKeyboardButton("🔄 Auto-fetch tasa", callback_data="prices:autotasa"),
    ])
    buttons.append([InlineKeyboardButton("🔙 Panel admin", callback_data="admin:back")])
    return InlineKeyboardMarkup(buttons)


def platform_price_edit_keyboard(platform_id: str, platform: dict) -> InlineKeyboardMarkup:
    """Buttons to edit each price type for a platform."""
    monthly = platform.get("monthly_price_usd") or "—"
    express = platform.get("express_price_usd") or "—"
    week = platform.get("week_price_usd") or "—"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"📅 Mensual: ${monthly}",
            callback_data=f"prices:edit:{platform_id}:monthly",
        )],
        [InlineKeyboardButton(
            f"⚡ Express: ${express}",
            callback_data=f"prices:edit:{platform_id}:express",
        )],
        [InlineKeyboardButton(
            f"🗓 Semanal: ${week}",
            callback_data=f"prices:edit:{platform_id}:week",
        )],
        [InlineKeyboardButton(
            "💾 Guardar los 3 precios a la vez",
            callback_data=f"prices:edit:{platform_id}:all",
        )],
        [InlineKeyboardButton("🔙 Ver todas las plataformas", callback_data="prices:menu")],
    ])


def confirm_price_keyboard(platform_id: str, price_type: str, new_price: float) -> InlineKeyboardMarkup:
    """Confirm new price before saving."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                f"✅ Guardar ${new_price:.2f}",
                callback_data=f"prices:confirm:{platform_id}:{price_type}:{new_price}",
            ),
            InlineKeyboardButton("❌ Cancelar", callback_data=f"prices:platform:{platform_id}"),
        ],
    ])


def clients_list_keyboard(clients: list[dict], current_page: int, total_pages: int) -> InlineKeyboardMarkup:
    """Client list with per-client buttons and pagination."""
    buttons = []
    for c in clients:
        name = c.get("name") or c.get("username") or "Sin nombre"
        tid = c.get("telegram_id")
        status_icon = "✅" if c.get("status") == "active" else "🚫"
        purchases = c.get("total_purchases", 0)
        buttons.append([InlineKeyboardButton(
            f"{status_icon} {name}  ({purchases} compras)",
            callback_data=f"admin:client_detail:{tid}",
        )])
    nav = []
    if current_page > 1:
        nav.append(InlineKeyboardButton("⬅️ Anterior", callback_data=f"admin:clients_page:{current_page - 1}"))
    if current_page < total_pages:
        nav.append(InlineKeyboardButton("Siguiente ➡️", callback_data=f"admin:clients_page:{current_page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("🔙 Panel admin", callback_data="admin:back")])
    return InlineKeyboardMarkup(buttons)


def client_detail_keyboard(telegram_id: int, is_blocked: bool) -> InlineKeyboardMarkup:
    """Admin actions on a specific client."""
    block_btn = (
        InlineKeyboardButton("✅ Desbloquear", callback_data=f"admin:unblock:{telegram_id}")
        if is_blocked
        else InlineKeyboardButton("🚫 Bloquear", callback_data=f"admin:block:{telegram_id}")
    )
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✏️ Editar nombre", callback_data=f"admin:edit_name:{telegram_id}"),
            InlineKeyboardButton("📱 Editar teléfono", callback_data=f"admin:edit_phone:{telegram_id}"),
        ],
        [block_btn],
        [InlineKeyboardButton("🔙 Lista clientes", callback_data="admin:clients")],
    ])


def share_contact_keyboard() -> ReplyKeyboardMarkup:
    """Keyboard asking user to share their phone number."""
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📱 Compartir mi número", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def remove_keyboard() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()


def cart_keyboard() -> InlineKeyboardMarkup:
    """Cart summary keyboard."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirmar pedido", callback_data="cart:confirm")],
        [InlineKeyboardButton("➕ Agregar otro servicio", callback_data="menu:subscribe")],
        [InlineKeyboardButton("🗑️ Vaciar carrito", callback_data="cart:clear")],
    ])
