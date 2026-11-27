"""Cliente en el bot con órdenes de pago (O02 Lote 4). Solo actúa con `CHECKOUT_V2_ENABLED=true`.

Con el flag apagado (por defecto) ninguna función de aquí se ejecuta en un flujo real: los puntos de
entrada existentes (`subscription.py`, `express.py`, `my_services.py`, `main.py`) comprueban el flag y,
si está apagado, siguen con el código de siempre.

Lo que hace con el flag encendido:
- Los carritos de compra y de renovación, la compra individual y la express crean UNA orden
  (`checkout_orders`) con un solo monto y un solo comprobante; no crean suscripciones.
- Solo los ítems `nueva` reservan perfil (30 min); las renovaciones no.
- El comprobante (foto, imagen como archivo o PDF) se asocia a la orden pendiente del cliente; se
  atiende antes que el flujo de Hogar. El OCR agrega señales que solo marcan; aprueba el admin.
- `payment:resend` y `order:cancel:<id>`; «Mis servicios» muestra también las vencidas con Renovar.

PRERREQUISITOS para encender el flag: migración del Lote 2 ejecutada y aprobación por orden en el
admin (Lote 5): los botones `admin:oapprove:` / `admin:oreject:` del ticket aún no tienen handler.
"""
from __future__ import annotations

import hashlib
import html
import logging
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.keyboards import main_menu_keyboard
from bot.middleware import clear_user_data, clear_user_state, set_user_data, set_user_state
from services import checkout_flow as flow
from services import ocr_signals
from services.checkout_service import CheckoutError, RateUnavailable, build_manifest, normalize_rate
from utils.helpers import short_id

logger = logging.getLogger(__name__)

STATE_AWAITING = "awaiting_order_payment"
MAX_RECEIPT_BYTES = 10 * 1024 * 1024
ADMIN_APPROVE_CB = "admin:oapprove:{}"   # sin handler hasta el Lote 5
ADMIN_REJECT_CB = "admin:oreject:{}"     # sin handler hasta el Lote 5


def v2_enabled() -> bool:
    from config import settings
    return bool(settings.CHECKOUT_V2_ENABLED)


# ── Teclados (todo aviso ofrece una acción) ─────────────────────────────────

_MENU = [InlineKeyboardButton("🏠 Menú principal", callback_data="menu:main")]


def payment_keyboard(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancelar pedido", callback_data=f"order:cancel:{order_id}")], _MENU])


def receipt_sent_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Reenviar comprobante", callback_data="payment:resend")],
        [InlineKeyboardButton("📋 Mis servicios", callback_data="menu:my_services")],
        [InlineKeyboardButton("🆘 Contactar soporte", callback_data="support:contact_admin")], _MENU])


def retry_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Intentar de nuevo", callback_data="menu:subscribe")], _MENU])


def order_admin_keyboard(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Aprobar", callback_data=ADMIN_APPROVE_CB.format(order_id)),
        InlineKeyboardButton("❌ Rechazar", callback_data=ADMIN_REJECT_CB.format(order_id))]])


# ── Utilidades ──────────────────────────────────────────────────────────────

async def _user_for(update: Update, create: bool = True) -> Optional[dict]:
    from database.users import get_or_create_user, get_user_by_telegram_id
    tg = update.effective_user
    user = await get_user_by_telegram_id(tg.id)
    if not user and create:
        user = await get_or_create_user(tg.id, tg.username, tg.full_name)
    return user


async def _platforms_for(platform_ids: set[str]) -> tuple[dict[str, dict], dict[str, str]]:
    """({id: fila}, {id: «🎬 Netflix»}) de las plataformas pedidas."""
    from database.platforms import get_platform_by_id
    rows: dict[str, dict] = {}
    for pid in platform_ids:
        row = await get_platform_by_id(pid)
        if row:
            rows[pid] = row
    names = {pid: f"{r.get('icon_emoji') or '📺'} {r.get('name', '?')}" for pid, r in rows.items()}
    return rows, names


async def _reserve_profiles(items: list[dict], user_id: str) -> None:
    """Reserva (30 min) un perfil por cada ítem `nueva`; las renovaciones no reservan. Mejor esfuerzo."""
    from database.profiles import get_available_profiles, reserve_profile
    for platform_id, profile_type in flow.reservation_targets(items):
        try:
            free = await get_available_profiles(platform_id, profile_type)
            if free:
                await reserve_profile(str(free[0]["id"]), user_id, flow.PROFILE_RESERVATION_MINUTES)
        except Exception as e:
            logger.warning(f"checkout_v2: no se pudo reservar perfil en {platform_id}: {e}")


async def _fail(query, text: str, keyboard: Optional[InlineKeyboardMarkup] = None) -> None:
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard or retry_keyboard())


async def _place_order(query, update: Update, items: list[dict], platforms: dict, names: dict, user: dict, after=None) -> None:
    """Común a los cuatro puntos de entrada: política de orden pendiente, tasa, manifiesto, orden,
    reserva, estado y mensaje de pago."""
    from database import orders
    from services.exchange_service import get_current_rate
    from services.payment_service import get_payment_config

    telegram_id = update.effective_user.id
    user_id = str(user["id"])

    pending = await orders.get_pending_order_for_user(user_id)
    policy = flow.pending_order_policy(pending)
    if policy == "waiting_review":
        await _fail(
            query,
            f"⏳ Ya tienes un pedido en revisión (#{short_id(pending['id'])}). "
            "Espera la respuesta del equipo antes de crear otro. Si enviaste un comprobante equivocado, reenvíalo.",
            receipt_sent_keyboard(),
        )
        return

    cfg = await get_payment_config()
    if not cfg:
        await _fail(query, "No pudimos cargar los datos de pago. Tu pedido no se creó; inténtalo en unos minutos o contacta a soporte.")
        return
    try:
        rate = normalize_rate(await get_current_rate())
    except RateUnavailable:
        await _fail(query, "No pudimos obtener la tasa de cambio ahora mismo. Tu pedido no se creó; inténtalo en unos minutos.")
        return

    manifest = build_manifest(items, rate)

    if policy == "supersede":
        try:
            await orders.decide_order(str(pending["id"]), "cancelled", f"cliente:{telegram_id}")
        except CheckoutError as e:
            logger.warning(f"checkout_v2: no se pudo reemplazar la orden {pending.get('id')}: {e}")

    order = await orders.create_order(user_id, manifest)
    if not order:
        await _fail(query, "No pudimos crear tu pedido. Inténtalo de nuevo en unos minutos.")
        return

    await _reserve_profiles(items, user_id)
    set_user_state(telegram_id, STATE_AWAITING)
    set_user_data(telegram_id, "order_id", str(order["id"]))
    if after:
        after()
    await query.edit_message_text(
        flow.payment_instructions_text(order, names, cfg), parse_mode="HTML",
        reply_markup=payment_keyboard(str(order["id"])),
    )


async def _entry(update: Update, builder) -> None:
    """Envoltorio de los puntos de entrada: responde el callback, resuelve el cliente y traduce
    cualquier `CheckoutError` a un mensaje con salida."""
    query = update.callback_query
    if not query or not update.effective_user:
        return
    await query.answer()
    try:
        user = await _user_for(update)
        if not user:
            await _fail(query, "No encontramos tu cuenta. Usa /start.", InlineKeyboardMarkup([_MENU]))
            return
        await builder(query, user)
    except CheckoutError as e:
        await _fail(query, html.escape(str(e)))
    except Exception as e:
        logger.error(f"checkout_v2 error: {e}", exc_info=True)
        await _fail(query, "Error al procesar tu pedido. Inténtalo de nuevo.")


# ── Puntos de entrada de la creación de la orden ────────────────────────────

async def confirm_purchase_cart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Carrito de compra → una orden de ítems `nueva`."""
    from services.cart_service import clear_cart, get_cart
    tid = update.effective_user.id if update.effective_user else 0

    async def build(query, user):
        cart = get_cart(tid)
        if not cart:
            await query.edit_message_text("Tu carrito está vacío. Usa el menú para agregar servicios.",
                                          reply_markup=main_menu_keyboard())
            return
        platforms, names = await _platforms_for({str(i.get("platform_id")) for i in cart})
        await _place_order(query, update, flow.purchase_items(cart, platforms), platforms, names, user,
                           after=lambda: clear_cart(tid))
    await _entry(update, build)


async def confirm_renewal_cart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Carrito de renovación → una orden de ítems `renovacion` (uno por suscripción, sin reservar perfil)."""
    from database.subscriptions import get_renewable_subscriptions_by_user
    from services.cart_service import clear_renewal_cart, get_renewal_cart
    tid = update.effective_user.id if update.effective_user else 0

    async def build(query, user):
        selected = [v for v in get_renewal_cart(tid).values() if v.get("selected")]
        if not selected:
            await _fail(query, "Selecciona al menos un servicio de tu carrito de renovación.", InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Volver al carrito", callback_data="menu:subscribe")], _MENU]))
            return
        subs = {str(s["id"]): s for s in await get_renewable_subscriptions_by_user(str(user["id"]))}
        # get_renewable_subscriptions_by_user ya filtra por cliente; se conserva user_id para la comprobación.
        for s in subs.values():
            s.setdefault("user_id", str(user["id"]))
        platforms, names = await _platforms_for({str(s.get("platform_id")) for s in subs.values()})
        await _place_order(query, update, flow.renewal_items(selected, subs, platforms, str(user["id"])),
                           platforms, names, user, after=lambda: clear_renewal_cart(tid))
    await _entry(update, build)


async def _confirm_single(update: Update, plan_type: str) -> None:
    query = update.callback_query
    parts = (query.data or "").split(":") if query else []
    platform_id = parts[2] if len(parts) > 2 else ""

    async def build(query, user):
        if not platform_id:
            await _fail(query, "Sesión expirada. Usa /start para comenzar de nuevo.", InlineKeyboardMarkup([_MENU]))
            return
        platforms, names = await _platforms_for({platform_id})
        items = flow.purchase_items([{"platform_id": platform_id, "plan_type": plan_type}], platforms)
        await _place_order(query, update, items, platforms, names, user)
    await _entry(update, build)


async def confirm_single_order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`confirm:monthly:<platform>` → orden con un ítem `nueva` mensual."""
    await _confirm_single(update, "monthly")


async def confirm_express_order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`confirm:express:<platform>` → orden con un ítem `nueva` express."""
    await _confirm_single(update, "express")


# ── Cancelar y reenviar ─────────────────────────────────────────────────────

async def handle_order_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`order:cancel:<id>`: el cliente cancela su orden pendiente (si aún no envió comprobante)."""
    from database import orders
    query = update.callback_query
    if not query or not update.effective_user:
        return
    await query.answer()
    order_id = (query.data or "").split(":")[-1]
    user = await _user_for(update, create=False)
    order = await orders.get_order(order_id)
    if not user or not order or str(order.get("user_id")) != str(user["id"]):
        await _fail(query, "No encontramos ese pedido.", InlineKeyboardMarkup([_MENU]))
        return
    if order.get("status") != "pending":
        await _fail(query, "Ese pedido ya no está pendiente.", InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 Mis servicios", callback_data="menu:my_services")], _MENU]))
        return
    if flow.order_has_receipt(order):
        await _fail(query, "Ya enviaste un comprobante de este pedido y está en revisión; no se puede cancelar. "
                           "Si es un error, contacta a soporte.", receipt_sent_keyboard())
        return
    try:
        ok = await orders.decide_order(order_id, "cancelled", f"cliente:{update.effective_user.id}")
    except CheckoutError:
        ok = False
    if ok:
        clear_user_state(update.effective_user.id)
        clear_user_data(update.effective_user.id)
    await query.edit_message_text(
        "✅ Pedido cancelado." if ok else "No se pudo cancelar el pedido; es posible que ya haya cambiado. Revisa «Mis servicios».",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📺 Hacer un pedido", callback_data="menu:subscribe")],
                                           [InlineKeyboardButton("📋 Mis servicios", callback_data="menu:my_services")], _MENU]),
    )


async def handle_payment_resend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`payment:resend`: pide de nuevo el comprobante de la orden pendiente. Siempre ofrece una salida."""
    query = update.callback_query
    if not query or not update.effective_user:
        return
    await query.answer()
    if not v2_enabled():
        await query.edit_message_text(
            "📸 Envía de nuevo la foto de tu comprobante en este chat y la revisamos.",
            reply_markup=InlineKeyboardMarkup([_MENU]))
        return
    from database import orders
    user = await _user_for(update, create=False)
    order = await orders.get_pending_order_for_user(str(user["id"])) if user else None
    if not order:
        await _fail(query, "No tienes un pedido pendiente. Haz un pedido para poder enviar un comprobante.",
                    InlineKeyboardMarkup([[InlineKeyboardButton("📺 Hacer un pedido", callback_data="menu:subscribe")], _MENU]))
        return
    set_user_state(update.effective_user.id, STATE_AWAITING)
    set_user_data(update.effective_user.id, "order_id", str(order["id"]))
    await query.edit_message_text(
        f"📸 Envía el comprobante del pedido #{short_id(order['id'])} (foto, imagen como archivo o PDF). "
        "Si ya enviaste uno, el nuevo lo reemplaza para la revisión.",
        reply_markup=payment_keyboard(str(order["id"])))


# ── Comprobante ─────────────────────────────────────────────────────────────

def _media_of(message):
    """(objeto con get_file, tipo, nombre) o None. Tipo: «image» o «pdf»."""
    if getattr(message, "photo", None):
        return message.photo[-1], "image", "comprobante.jpg"
    doc = getattr(message, "document", None)
    if doc:
        mime = (doc.mime_type or "").lower()
        if mime.startswith("image/"):
            return doc, "image", doc.file_name or "comprobante.jpg"
        if mime == "application/pdf":
            return doc, "pdf", doc.file_name or "comprobante.pdf"
        return doc, "unsupported", doc.file_name or "archivo"
    return None


def _ocr_text(ex: dict, flags: dict) -> str:
    def v(k):
        return str(ex.get(k)).strip() if ex.get(k) else "—"
    if flags.get("ocr") != "ok":
        return "OCR no disponible"
    return (f"Referencia: {v('referencia')}\nFecha: {v('fecha')} {v('hora') if ex.get('hora') else ''}".rstrip() + "\n"
            f"Monto: Bs {v('monto')}\nCelular destino: {v('celular_destino')}\nCédula receptor: {v('cedula_receptor')}\n"
            f"Banco emisor: {v('banco_emisor')}\nBanco receptor: {v('banco_receptor')}\n"
            f"Concepto: {v('concepto')}\nConfianza OCR: {v('confianza')}")


async def handle_order_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Foto, imagen como archivo o PDF. Devuelve True si lo atendió (el cliente tiene una orden
    pendiente), para que el enrutador de fotos NO lo pase al flujo de Hogar ni al flujo anterior."""
    if not v2_enabled() or not update.message or not update.effective_user:
        return False
    from database import orders
    user = await _user_for(update, create=False)
    order = await orders.get_pending_order_for_user(str(user["id"])) if user else None
    if not order:
        return False
    msg = update.message
    media = _media_of(msg)
    if media is None:
        return False
    handle, kind, filename = media
    if kind == "unsupported":
        await msg.reply_text("Ese formato no se puede revisar. Envía el comprobante como foto, imagen o PDF.",
                             reply_markup=receipt_sent_keyboard())
        return True
    if getattr(handle, "file_size", None) and handle.file_size > MAX_RECEIPT_BYTES:
        await msg.reply_text("El archivo es muy pesado (máximo 10 MB). Envía una foto o un PDF más liviano.",
                             reply_markup=receipt_sent_keyboard())
        return True

    try:
        data = bytes(await (await handle.get_file()).download_as_bytearray())
        await msg.reply_text("⏳ <b>Comprobante recibido</b>\n\nEstamos verificando tu pago. En breve recibirás confirmación. 🙏",
                             parse_mode="HTML", reply_markup=receipt_sent_keyboard())

        # Señales (solo marcan). Imagen repetida y referencia repetida son marcas, no bloqueos.
        image_hash = hashlib.sha256(data).hexdigest()
        duplicate = False
        try:
            from services.payment_service import _check_image_hash_duplicate, _store_image_hash
            duplicate = await _check_image_hash_duplicate(image_hash)
        except Exception:
            pass
        extracted: dict = {}
        if kind == "image":
            try:
                from services.gemini_service import validate_payment_image
                extracted = await validate_payment_image(data) or {}
            except Exception as e:
                logger.warning(f"OCR failed (non-blocking): {e}")
        from services.payment_service import get_payment_config
        reference = str(extracted.get("referencia") or "").strip() or None
        seen = await orders.payment_reference_used(reference, str(order["id"])) if reference else False
        flags = ocr_signals.evaluate_signals(
            extracted, total_bs=order.get("total_bs"), payment_cfg=await get_payment_config(),
            reference_seen=seen, image_duplicate=duplicate, order_created_at=_created_at(order))
        if kind == "pdf":
            flags["ocr"] = "unavailable"
            flags["alertas"] = ["PDF recibido: no se lee con OCR, revísalo a mano"] + [
                a for a in flags.get("alertas", []) if "OCR" not in a]

        attached = await orders.attach_receipt(str(order["id"]), reference, _ocr_text(extracted, flags), flags)
        if not attached:
            await msg.reply_text("Tu pedido ya no está pendiente, así que no se guardó el comprobante. Revisa «Mis servicios».",
                                 reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📋 Mis servicios", callback_data="menu:my_services")], _MENU]))
            return True
        try:
            from services.payment_service import _store_image_hash
            await _store_image_hash(image_hash)
        except Exception:
            pass

        await _notify_admin(order, user, update.effective_user.id, data, kind, filename, flags)
        clear_user_state(update.effective_user.id)
        clear_user_data(update.effective_user.id)
    except Exception as e:
        logger.error(f"handle_order_receipt error: {e}", exc_info=True)
        await msg.reply_text("Hubo un problema procesando tu comprobante. Reenvíalo o contacta a soporte. 📞",
                             reply_markup=receipt_sent_keyboard())
    return True


def _in_hogar_photo_flow(telegram_id: int) -> bool:
    """¿El cliente está esperando justo ahora que le pidan la foto del soporte de Hogar? En ese caso su
    foto es de Hogar aunque tenga una orden pendiente (excepción a la prioridad de la orden)."""
    try:
        from bot.handlers.hogar import STATE_WAITING_PHOTO, get_state
        return get_state(telegram_id) == STATE_WAITING_PHOTO
    except Exception as e:
        logger.warning(f"checkout_v2: no se pudo leer el estado de Hogar: {e}")
        return False


async def route_photo(update: Update, context: ContextTypes.DEFAULT_TYPE, hogar_photo, payment_image) -> None:
    """Enrutador de fotos: una orden pendiente (con el flag encendido) se atiende ANTES que el flujo
    de Hogar; si no aplica, todo sigue como siempre (Hogar y luego el flujo de comprobante anterior)."""
    if not _in_hogar_photo_flow(update.effective_user.id if update.effective_user else 0):
        if await handle_order_receipt(update, context):
            return
    if not await hogar_photo(update, context):
        await payment_image(update, context)


def _created_at(order: dict):
    try:
        from utils.dates import parse_db_datetime
        return parse_db_datetime(order.get("created_at"))
    except (ValueError, TypeError):
        return None


async def _notify_admin(order, user, telegram_id, data: bytes, kind: str, filename: str, flags: dict) -> None:
    from services.notification_service import send_document_to_admin, send_to_admin
    fresh = dict(order)  # el manifiesto no cambia al adjuntar el comprobante
    pids = {str(i.get("platform_id")) for i in order["manifest"]["items"]}
    _, names = await _platforms_for(pids)
    ticket = flow.admin_order_ticket(fresh, names, user.get("name") or str(telegram_id), telegram_id,
                                     ocr_signals.summary_lines(flags))
    if kind == "pdf":
        await send_document_to_admin(data, filename, "📎 Comprobante del cliente (PDF)")
    else:
        await send_to_admin("📎 Comprobante del cliente:", photo_bytes=data)
    await send_to_admin(ticket, keyboard=order_admin_keyboard(str(order["id"])))


# ── Mis servicios (con vencidas y Renovar) ──────────────────────────────────

async def show_my_services(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """«Mis servicios» con flag: activas, vencidas con botón Renovar y el pedido en curso.
    Devuelve True si mostró la pantalla; False deja que el flujo de siempre responda (sin servicios ni orden)."""
    if not v2_enabled() or not update.effective_user:
        return False
    from bot.messages import SERVICE_DETAIL
    from database import orders
    from database.subscriptions import get_renewable_subscriptions_by_user
    from services.checkout_service import days_overdue, is_overdue
    from utils.dates import caracas_date, now_caracas
    from utils.helpers import days_remaining, format_datetime_vzla
    from utils.dates import parse_db_datetime

    user = await _user_for(update, create=False)
    if not user:
        return False
    subs = await get_renewable_subscriptions_by_user(str(user["id"]))
    order = await orders.get_pending_order_for_user(str(user["id"]))
    if not subs and not order:
        return False

    now = now_caracas()
    active = [s for s in subs if not is_overdue(s, now)]
    overdue = [s for s in subs if is_overdue(s, now)]
    text = "📋 <b>Tus servicios</b>\n\n"
    plan_labels = {"monthly": "Mensual", "express": "Express 24h"}
    for s in active:
        plat, prof = s.get("platforms") or {}, s.get("profiles") or {}
        end = parse_db_datetime(s["end_date"]) if s.get("end_date") else None
        text += SERVICE_DETAIL.format(
            icon=plat.get("icon_emoji", "📺"), platform=html.escape(plat.get("name", "?")),
            plan_type=plan_labels.get(s.get("plan_type"), s.get("plan_type")),
            profile_name=html.escape(prof.get("profile_name", "N/A")),
            end_date=format_datetime_vzla(end) if end else "N/A", days_left=days_remaining(end) if end else 0,
            sub_id=short_id(str(s.get("id", "")))) + "\n"
    if overdue:
        text += "\n⚠️ <b>Vencidos (renueva para no perder el acceso):</b>\n"
        for s in overdue:
            plat = s.get("platforms") or {}
            n = days_overdue(s, now)
            text += (f"{plat.get('icon_emoji', '📺')} {html.escape(plat.get('name', '?'))} — venció el "
                     f"{caracas_date(s['end_date']).strftime('%d/%m/%Y')} ({n} día{'s' if n != 1 else ''})\n")
    buttons = [[InlineKeyboardButton(f"{(s.get('platforms') or {}).get('icon_emoji', '📺')} "
                                     f"{(s.get('platforms') or {}).get('name', '?')} #{short_id(str(s['id']))}",
                                     callback_data=f"service:detail:{s['id']}")] for s in active[:5]]
    if overdue:
        buttons.append([InlineKeyboardButton("🔄 Renovar servicios vencidos", callback_data="menu:subscribe")])
    if order:
        waiting = flow.order_has_receipt(order)
        text += (f"\n⏳ Pedido #{short_id(order['id'])} "
                 f"{'en revisión por el equipo' if waiting else 'pendiente de pago'}.\n")
        buttons.append([InlineKeyboardButton("🔄 Reenviar comprobante", callback_data="payment:resend")])
        if not waiting:
            buttons.append([InlineKeyboardButton("❌ Cancelar pedido", callback_data=f"order:cancel:{order['id']}")])
    buttons.append(_MENU)
    markup = InlineKeyboardMarkup(buttons)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
    elif update.message:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=markup)
    return True
