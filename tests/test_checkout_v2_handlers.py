"""Pruebas de los handlers del cliente con órdenes de pago (O02 Lote 4): Telegram, base, Redis y
OCR falsos; datos ficticios. Con el flag apagado todo sigue como antes."""
from tests import support  # noqa: F401

import unittest
from decimal import Decimal as D
from types import SimpleNamespace
from unittest import mock

from bot.handlers import checkout_v2 as v2
from services import checkout_service as cs
from tests.test_checkout_service import sub as mk_sub

TID = 555
USER = {"id": "user-1", "telegram_id": TID, "name": "Cliente Ficticio"}
PLAT = {"nf": {"id": "nf", "name": "Netflix", "icon_emoji": "🎬", "monthly_price_usd": 4.5, "express_price_usd": 1.0},
        "hbo": {"id": "hbo", "name": "HBO", "icon_emoji": "💜", "monthly_price_usd": 5, "express_price_usd": 1.0}}
CFG = {"banco": "Banco Ficticio", "telefono": "04141234567", "cedula": "V-12345678", "titular": "Titular Ficticio"}
OID = "aaaaaaaa-0000-0000-0000-000000000001"


def callback(data):
    q = SimpleNamespace(data=data, answer=mock.AsyncMock(), edit_message_text=mock.AsyncMock())
    return SimpleNamespace(callback_query=q, message=None, effective_user=SimpleNamespace(id=TID, username="u", full_name="Cliente Ficticio")), q


def keyboard_callbacks(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.created = []
        self.cart, self.rcart = [], {}
        self.pending = None
        self.rate = {"usd_binance": 1000}
        self.cfg = CFG
        self.subs = []

        async def create_order(user_id, manifest, **kw):
            row = {"id": OID, "user_id": user_id, "status": "pending", "manifest": manifest, "total_usd": manifest["total_usd"],
                   "total_bs": manifest["total_bs"], "created_at": "2026-12-01T14:00:00+00:00"}
            self.created.append(row)
            return row

        self.m = SimpleNamespace(
            create_order=mock.AsyncMock(side_effect=create_order),
            get_pending=mock.AsyncMock(side_effect=lambda uid: self.pending),
            decide=mock.AsyncMock(return_value=True),
            attach=mock.AsyncMock(return_value=True),
            get_order=mock.AsyncMock(),
            ref_used=mock.AsyncMock(return_value=False),
            reserve=mock.AsyncMock(return_value=True),
            free=mock.AsyncMock(return_value=[{"id": "prof-1"}]),
            send_admin=mock.AsyncMock(return_value=True),
            send_doc=mock.AsyncMock(return_value=True),
            set_state=mock.Mock(), set_data=mock.Mock(), clear_state=mock.Mock(), clear_data=mock.Mock(),
            clear_cart=mock.Mock(), clear_rcart=mock.Mock(),
            create_sub=mock.AsyncMock(),
        )
        patches = [
            mock.patch.object(v2, "v2_enabled", return_value=True),
            mock.patch("database.users.get_user_by_telegram_id", new=mock.AsyncMock(return_value=USER)),
            mock.patch("database.users.get_or_create_user", new=mock.AsyncMock(return_value=USER)),
            mock.patch("database.platforms.get_platform_by_id", new=mock.AsyncMock(side_effect=lambda pid: PLAT.get(pid))),
            mock.patch("database.orders.create_order", self.m.create_order),
            mock.patch("database.orders.get_pending_order_for_user", self.m.get_pending),
            mock.patch("database.orders.decide_order", self.m.decide),
            mock.patch("database.orders.attach_receipt", self.m.attach),
            mock.patch("database.orders.get_order", self.m.get_order),
            mock.patch("database.orders.payment_reference_used", self.m.ref_used),
            mock.patch("database.profiles.get_available_profiles", self.m.free),
            mock.patch("database.profiles.reserve_profile", self.m.reserve),
            mock.patch("database.subscriptions.create_subscription", self.m.create_sub),
            mock.patch("database.subscriptions.get_renewable_subscriptions_by_user", new=mock.AsyncMock(side_effect=lambda uid: self.subs)),
            mock.patch("services.exchange_service.get_current_rate", new=mock.AsyncMock(side_effect=lambda: self.rate)),
            mock.patch("services.payment_service.get_payment_config", new=mock.AsyncMock(side_effect=lambda: self.cfg)),
            mock.patch("services.payment_service._check_image_hash_duplicate", new=mock.AsyncMock(return_value=False)),
            mock.patch("services.payment_service._store_image_hash", new=mock.AsyncMock()),
            mock.patch("services.cart_service.get_cart", side_effect=lambda tid: self.cart),
            mock.patch("services.cart_service.clear_cart", self.m.clear_cart),
            mock.patch("services.cart_service.get_renewal_cart", side_effect=lambda tid: self.rcart),
            mock.patch("services.cart_service.clear_renewal_cart", self.m.clear_rcart),
            mock.patch("services.notification_service.send_to_admin", self.m.send_admin),
            mock.patch("services.notification_service.send_document_to_admin", self.m.send_doc),
            mock.patch.object(v2, "set_user_state", self.m.set_state), mock.patch.object(v2, "set_user_data", self.m.set_data),
            mock.patch.object(v2, "clear_user_state", self.m.clear_state), mock.patch.object(v2, "clear_user_data", self.m.clear_data),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)


class TestCreateOrder(Base):
    async def test_renewal_cart_makes_one_order_without_subscriptions_or_reservations(self):
        self.subs = [{**mk_sub("a", platform="nf"), "user_id": "user-1"}, {**mk_sub("b", platform="nf"), "user_id": "user-1"}]  # dos Netflix vencidas
        self.rcart = {"a": {"sub_id": "a", "selected": True}, "b": {"sub_id": "b", "selected": True}}
        upd, q = callback("rcart:confirm")
        await v2.confirm_renewal_cart(upd, None)
        self.assertEqual(len(self.created), 1)
        items = self.created[0]["manifest"]["items"]
        self.assertEqual([i["subscription_id"] for i in items], ["a", "b"])
        self.assertEqual(self.created[0]["manifest"]["total_usd"], "9.00")
        self.m.create_sub.assert_not_awaited()
        self.m.reserve.assert_not_awaited()                        # las renovaciones no reservan perfil
        self.m.clear_rcart.assert_called_once()
        text = q.edit_message_text.await_args.args[0]
        self.assertIn("Bs 9,000.00", text)
        cbs = keyboard_callbacks(q.edit_message_text.await_args.kwargs["reply_markup"])
        self.assertIn(f"order:cancel:{OID}", cbs)
        self.assertIn("menu:main", cbs)
        self.m.set_state.assert_called_with(TID, v2.STATE_AWAITING)

    async def test_purchase_cart_reserves_one_profile_per_new_item(self):
        self.cart = [{"platform_id": "nf", "plan_type": "monthly", "price_usd": 0.01}, {"platform_id": "hbo", "plan_type": "monthly"}]
        upd, q = callback("cart:confirm")
        await v2.confirm_purchase_cart(upd, None)
        m = self.created[0]["manifest"]
        self.assertEqual([i["tipo"] for i in m["items"]], ["nueva", "nueva"])
        self.assertEqual(m["total_usd"], "9.50")                   # precios de la plataforma, no del carrito
        self.assertEqual(self.m.reserve.await_count, 2)
        self.m.reserve.assert_awaited_with("prof-1", "user-1", 30)
        self.m.clear_cart.assert_called_once()
        self.m.create_sub.assert_not_awaited()

    async def test_single_and_express_create_one_item_orders(self):
        upd, q = callback("confirm:monthly:nf")
        await v2.confirm_single_order(upd, None)
        upd2, q2 = callback("confirm:express:hbo")
        self.pending = None
        await v2.confirm_express_order(upd2, None)
        self.assertEqual([c["manifest"]["items"][0]["plan_type"] for c in self.created], ["monthly", "express"])
        self.assertEqual(self.created[1]["manifest"]["total_usd"], "1.00")

    async def test_empty_cart_and_empty_renewal_selection_offer_a_way_out(self):
        upd, q = callback("cart:confirm")
        await v2.confirm_purchase_cart(upd, None)
        self.assertEqual(self.created, [])
        self.assertIn("menu:subscribe", keyboard_callbacks(q.edit_message_text.await_args.kwargs["reply_markup"]))
        self.rcart = {"a": {"sub_id": "a", "selected": False}}
        upd, q = callback("rcart:confirm")
        await v2.confirm_renewal_cart(upd, None)
        self.assertEqual(self.created, [])
        self.assertTrue(keyboard_callbacks(q.edit_message_text.await_args.kwargs["reply_markup"]))

    async def test_no_rate_blocks_the_order_without_fallback(self):
        self.cart = [{"platform_id": "nf", "plan_type": "monthly"}]
        for bad in (None, {}, {"usd_binance": 0}):
            self.rate = bad
            upd, q = callback("cart:confirm")
            await v2.confirm_purchase_cart(upd, None)
            self.assertEqual(self.created, [])
            self.assertIn("tasa de cambio", q.edit_message_text.await_args.args[0])
            self.assertIn("menu:subscribe", keyboard_callbacks(q.edit_message_text.await_args.kwargs["reply_markup"]))
        self.m.clear_cart.assert_not_called()                      # el carrito no se pierde

    async def test_missing_payment_config_blocks(self):
        self.cfg = None
        self.cart = [{"platform_id": "nf", "plan_type": "monthly"}]
        upd, q = callback("cart:confirm")
        await v2.confirm_purchase_cart(upd, None)
        self.assertEqual(self.created, [])

    async def test_foreign_subscription_in_a_tampered_cart_is_rejected(self):
        self.subs = [{**mk_sub("a", platform="nf"), "user_id": "user-1"}]
        self.rcart = {"zzz": {"sub_id": "zzz", "selected": True}}   # no es del cliente
        upd, q = callback("rcart:confirm")
        await v2.confirm_renewal_cart(upd, None)
        self.assertEqual(self.created, [])
        self.assertIn("ya no está disponible", q.edit_message_text.await_args.args[0])

    async def test_order_with_receipt_in_review_is_not_replaced(self):
        self.pending = {"id": "old-order-id", "status": "pending", "payment_reference": "R1"}
        self.cart = [{"platform_id": "nf", "plan_type": "monthly"}]
        upd, q = callback("cart:confirm")
        await v2.confirm_purchase_cart(upd, None)
        self.assertEqual(self.created, [])
        self.m.decide.assert_not_awaited()
        self.assertIn("en revisión", q.edit_message_text.await_args.args[0])
        self.assertIn("payment:resend", keyboard_callbacks(q.edit_message_text.await_args.kwargs["reply_markup"]))

    async def test_order_without_receipt_is_superseded(self):
        self.pending = {"id": "old-order-id", "status": "pending"}
        self.cart = [{"platform_id": "nf", "plan_type": "monthly"}]
        upd, q = callback("cart:confirm")
        await v2.confirm_purchase_cart(upd, None)
        self.m.decide.assert_awaited_once_with("old-order-id", "cancelled", f"cliente:{TID}")
        self.assertEqual(len(self.created), 1)

    async def test_db_failure_creating_the_order_keeps_the_cart_and_offers_retry(self):
        self.m.create_order.side_effect = None
        self.m.create_order.return_value = None
        self.cart = [{"platform_id": "nf", "plan_type": "monthly"}]
        upd, q = callback("cart:confirm")
        await v2.confirm_purchase_cart(upd, None)
        self.m.clear_cart.assert_not_called()
        self.assertIn("menu:subscribe", keyboard_callbacks(q.edit_message_text.await_args.kwargs["reply_markup"]))


def make_message(photo=None, document=None):
    return SimpleNamespace(photo=photo or (), document=document, reply_text=mock.AsyncMock())


def tg_file(data=b"IMG-BYTES", size=1000, mime=None, name=None):
    f = SimpleNamespace(download_as_bytearray=mock.AsyncMock(return_value=bytearray(data)))
    return SimpleNamespace(file_size=size, mime_type=mime, file_name=name, get_file=mock.AsyncMock(return_value=f))


def msg_update(message):
    return SimpleNamespace(message=message, callback_query=None, effective_user=SimpleNamespace(id=TID, username="u", full_name="Cliente Ficticio"))


GOOD_OCR = {"is_comprobante_valido": True, "referencia": "009988", "fecha": "01/12/2026", "monto": "9000.00",
            "celular_destino": "04141234567", "cedula_receptor": "V12345678", "confianza": "alta"}


class TestReceipt(Base):
    def setUp(self):
        super().setUp()
        items = [cs.build_new_item("nf", "monthly", "9")]
        self.pending = {"id": OID, "user_id": "user-1", "status": "pending", "total_bs": "9000.00",
                        "manifest": cs.build_manifest(items, "1000"), "created_at": "2026-12-01T14:00:00+00:00"}
        self.ocr = mock.AsyncMock(return_value=dict(GOOD_OCR))
        p = mock.patch("services.gemini_service.validate_payment_image", self.ocr)
        p.start(); self.addCleanup(p.stop)

    async def test_photo_attaches_to_the_order_with_signals_and_notifies_admin(self):
        msg = make_message(photo=(tg_file(), tg_file()))
        handled = await v2.handle_order_receipt(msg_update(msg), None)
        self.assertTrue(handled)
        args = self.m.attach.await_args.args
        self.assertEqual(args[0], OID)
        self.assertEqual(args[1], "009988")
        flags = args[3]
        self.assertEqual(flags["monto"]["nivel"], "ok")
        self.assertEqual(flags["telefono"]["nivel"], "ok")
        self.assertEqual(self.m.send_admin.await_count, 2)                       # foto y ticket
        ticket_call = self.m.send_admin.await_args_list[1]
        self.assertIn("ORDEN DE PAGO", ticket_call.args[0])
        self.assertEqual(keyboard_callbacks(ticket_call.kwargs["keyboard"]), [f"admin:oapprove:{OID}", f"admin:oreject:{OID}"])
        ack = msg.reply_text.await_args_list[0]
        self.assertIn("payment:resend", keyboard_callbacks(ack.kwargs["reply_markup"]))   # el aviso tiene acción
        self.m.clear_state.assert_called_once()

    async def test_signals_only_mark_a_wrong_receipt_is_still_forwarded_to_the_admin(self):
        self.ocr.return_value = {**GOOD_OCR, "monto": "100", "celular_destino": "04149999999"}
        self.m.ref_used.return_value = True
        handled = await v2.handle_order_receipt(msg_update(make_message(photo=(tg_file(),))), None)
        self.assertTrue(handled)
        flags = self.m.attach.await_args.args[3]
        self.assertEqual((flags["monto"]["nivel"], flags["telefono"]["nivel"], flags["referencia"]["nivel"]), ("warn", "warn", "warn"))
        self.assertEqual(self.m.send_admin.await_count, 2)                       # igual se envía
        self.assertIn("⚠️ Monto", self.m.send_admin.await_args_list[1].args[0])

    async def test_duplicate_image_is_flagged_not_blocked(self):
        with mock.patch("services.payment_service._check_image_hash_duplicate", new=mock.AsyncMock(return_value=True)):
            await v2.handle_order_receipt(msg_update(make_message(photo=(tg_file(),))), None)
        self.assertTrue(self.m.attach.await_args.args[3]["imagen_repetida"])
        self.assertEqual(self.m.send_admin.await_count, 2)

    async def test_ocr_failure_never_blocks(self):
        self.ocr.side_effect = RuntimeError("gemini caído")
        await v2.handle_order_receipt(msg_update(make_message(photo=(tg_file(),))), None)
        self.assertEqual(self.m.attach.await_args.args[3]["ocr"], "unavailable")
        self.assertEqual(self.m.send_admin.await_count, 2)

    async def test_image_sent_as_file_is_read_with_ocr(self):
        doc = tg_file(mime="image/png", name="pago.png")
        self.assertTrue(await v2.handle_order_receipt(msg_update(make_message(document=doc)), None))
        self.ocr.assert_awaited_once()
        self.m.send_doc.assert_not_awaited()

    async def test_pdf_goes_to_the_admin_as_a_file_without_ocr(self):
        doc = tg_file(data=b"%PDF-1.4", mime="application/pdf", name="pago.pdf")
        self.assertTrue(await v2.handle_order_receipt(msg_update(make_message(document=doc)), None))
        self.ocr.assert_not_awaited()
        self.m.send_doc.assert_awaited_once()
        self.assertEqual(self.m.send_doc.await_args.args[1], "pago.pdf")
        self.assertEqual(self.m.attach.await_args.args[3]["ocr"], "unavailable")
        self.assertIn("PDF", " ".join(self.m.attach.await_args.args[3]["alertas"]))
        self.assertEqual(self.m.send_admin.await_count, 1)                       # solo el ticket

    async def test_unsupported_or_huge_files_are_answered_with_an_action(self):
        for doc in (tg_file(mime="application/zip", name="x.zip"), tg_file(mime="image/jpeg", size=v2.MAX_RECEIPT_BYTES + 1)):
            msg = make_message(document=doc)
            self.assertTrue(await v2.handle_order_receipt(msg_update(msg), None))
            self.assertTrue(keyboard_callbacks(msg.reply_text.await_args.kwargs["reply_markup"]))
        self.m.attach.assert_not_awaited()

    async def test_not_handled_without_pending_order_or_with_flag_off(self):
        self.pending = None
        self.assertFalse(await v2.handle_order_receipt(msg_update(make_message(photo=(tg_file(),))), None))
        self.pending = {"id": OID, "status": "pending", "manifest": {"items": []}}
        with mock.patch.object(v2, "v2_enabled", return_value=False):
            self.assertFalse(await v2.handle_order_receipt(msg_update(make_message(photo=(tg_file(),))), None))
        self.assertFalse(await v2.handle_order_receipt(msg_update(make_message()), None))  # sin media
        self.m.attach.assert_not_awaited()

    async def test_order_no_longer_pending_when_attaching(self):
        self.m.attach.return_value = False
        msg = make_message(photo=(tg_file(),))
        self.assertTrue(await v2.handle_order_receipt(msg_update(msg), None))
        self.assertEqual(self.m.send_admin.await_count, 0)
        self.assertIn("menu:my_services", keyboard_callbacks(msg.reply_text.await_args.kwargs["reply_markup"]))

    async def test_photo_router_gives_the_order_priority_over_hogar(self):
        hogar, legacy = mock.AsyncMock(return_value=True), mock.AsyncMock()
        await v2.route_photo(msg_update(make_message(photo=(tg_file(),))), None, hogar, legacy)
        hogar.assert_not_awaited(); legacy.assert_not_awaited()
        self.assertEqual(self.m.attach.await_count, 1)

    async def test_photo_router_unchanged_without_an_order(self):
        self.pending = None
        hogar, legacy = mock.AsyncMock(return_value=True), mock.AsyncMock()
        upd = msg_update(make_message(photo=(tg_file(),)))
        await v2.route_photo(upd, None, hogar, legacy)
        hogar.assert_awaited_once(); legacy.assert_not_awaited()
        hogar.return_value = False
        await v2.route_photo(upd, None, hogar, legacy)
        legacy.assert_awaited_once()


class TestCancelResendMyServices(Base):
    def own(self, **kw):
        order = {"id": OID, "user_id": "user-1", "status": "pending", **kw}
        self.m.get_order.return_value = order
        self.pending = order
        return order

    async def test_cancel_ok(self):
        self.own()
        upd, q = callback(f"order:cancel:{OID}")
        await v2.handle_order_cancel(upd, None)
        self.m.decide.assert_awaited_once_with(OID, "cancelled", f"cliente:{TID}")
        self.assertIn("cancelado", q.edit_message_text.await_args.args[0])

    async def test_cancel_refused_with_receipt_foreign_or_not_pending(self):
        for order in (self.own(payment_reference="R1"), {"id": OID, "user_id": "otro", "status": "pending"},
                      {"id": OID, "user_id": "user-1", "status": "approved"}):
            self.m.get_order.return_value = order
            upd, q = callback(f"order:cancel:{OID}")
            await v2.handle_order_cancel(upd, None)
            self.assertTrue(keyboard_callbacks(q.edit_message_text.await_args.kwargs["reply_markup"]))
        self.m.decide.assert_not_awaited()

    async def test_resend_flag_off_gives_guidance_and_an_exit(self):
        upd, q = callback("payment:resend")
        with mock.patch.object(v2, "v2_enabled", return_value=False):
            await v2.handle_payment_resend(upd, None)
        self.assertIn("menu:main", keyboard_callbacks(q.edit_message_text.await_args.kwargs["reply_markup"]))
        self.m.set_state.assert_not_called()

    async def test_resend_with_and_without_a_pending_order(self):
        self.own()
        upd, q = callback("payment:resend")
        await v2.handle_payment_resend(upd, None)
        self.m.set_state.assert_called_with(TID, v2.STATE_AWAITING)
        self.pending = None
        upd, q = callback("payment:resend")
        await v2.handle_payment_resend(upd, None)
        self.assertIn("menu:subscribe", keyboard_callbacks(q.edit_message_text.await_args.kwargs["reply_markup"]))

    async def test_my_services_shows_overdue_with_renew_button_and_the_open_order(self):
        self.subs = [{**mk_sub("act", end="2099-01-01T03:59:59+00:00"), "user_id": "user-1", "platforms": {"name": "Netflix", "icon_emoji": "🎬"}, "profiles": {"profile_name": "P1"}},
                     {**mk_sub("old"), "user_id": "user-1", "platforms": {"name": "HBO", "icon_emoji": "💜"}}]
        self.own(payment_reference="R1")
        upd, q = callback("menu:my_services")
        self.assertTrue(await v2.show_my_services(upd, None))
        text = q.edit_message_text.await_args.args[0]
        self.assertIn("Vencidos", text)
        self.assertIn("HBO", text)
        self.assertIn("en revisión", text)
        cbs = keyboard_callbacks(q.edit_message_text.await_args.kwargs["reply_markup"])
        self.assertIn("menu:subscribe", cbs)                       # Renovar
        self.assertIn("payment:resend", cbs)
        self.assertNotIn(f"order:cancel:{OID}", cbs)               # con comprobante ya no se cancela
        self.assertIn("menu:main", cbs)

    async def test_my_services_falls_through_when_nothing_to_show_or_flag_off(self):
        self.subs, self.pending = [], None
        upd, q = callback("menu:my_services")
        self.assertFalse(await v2.show_my_services(upd, None))
        with mock.patch.object(v2, "v2_enabled", return_value=False):
            self.assertFalse(await v2.show_my_services(upd, None))


class TestFlagOff(unittest.IsolatedAsyncioTestCase):
    """Con el flag apagado los puntos de entrada existentes NO llaman al flujo nuevo."""

    async def test_hooks_follow_the_flag(self):
        from bot.handlers import express, subscription
        cases = [(subscription.handle_order_confirmed, "confirm_single_order"), (subscription.handle_cart_confirm, "confirm_purchase_cart"),
                 (subscription.handle_renewal_cart_confirm, "confirm_renewal_cart"), (express.handle_express_confirmed, "confirm_express_order")]
        upd = SimpleNamespace(callback_query=None, effective_user=SimpleNamespace(id=TID))   # el flujo anterior sale de inmediato
        for legacy, v2name in cases:
            with mock.patch.object(v2, v2name, new=mock.AsyncMock()) as fn:
                with mock.patch.object(v2, "v2_enabled", return_value=False):
                    await legacy(upd, None)
                fn.assert_not_awaited()
                with mock.patch.object(v2, "v2_enabled", return_value=True):
                    await legacy(upd, None)
                fn.assert_awaited_once_with(upd, None)

    def test_flag_defaults_to_off_and_reads_settings(self):
        from config import settings
        self.assertFalse(settings.CHECKOUT_V2_ENABLED)
        self.assertFalse(v2.v2_enabled())
        with mock.patch.object(settings, "CHECKOUT_V2_ENABLED", True):
            self.assertTrue(v2.v2_enabled())

    async def test_my_services_legacy_untouched_when_flag_off(self):
        from bot.handlers import my_services
        upd = SimpleNamespace(callback_query=None, message=SimpleNamespace(reply_text=mock.AsyncMock()), effective_user=SimpleNamespace(id=TID))
        with mock.patch.object(v2, "show_my_services", new=mock.AsyncMock()) as fn, \
             mock.patch.object(v2, "v2_enabled", return_value=False), \
             mock.patch("bot.handlers.my_services.get_user_by_telegram_id", new=mock.AsyncMock(return_value=None)):
            await my_services.show_my_services(upd, None)
        fn.assert_not_awaited()
        upd.message.reply_text.assert_awaited()                   # el flujo de siempre respondió


if __name__ == "__main__":
    unittest.main()
