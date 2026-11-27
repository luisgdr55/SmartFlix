"""Pruebas de O05 Lote 5: el cambio de tasa por texto libre del admin exige orden
explícita, valor plausible y confirmación con botón (stdlib `unittest`).
"""
from tests import support  # noqa: F401  (variables de entorno de prueba)

import time
import unittest
from types import SimpleNamespace
from unittest import mock

ADMIN = 777


def make_message(text="x"):
    return SimpleNamespace(text=text, reply_text=mock.AsyncMock())


def make_update(text):
    return SimpleNamespace(message=make_message(text), effective_user=SimpleNamespace(id=ADMIN),
                           callback_query=None)


def make_callback(data):
    q = SimpleNamespace(data=data, answer=mock.AsyncMock(), edit_message_text=mock.AsyncMock())
    return SimpleNamespace(callback_query=q, effective_user=SimpleNamespace(id=ADMIN)), q


def buttons(reply_markup):
    return [b for row in reply_markup.inline_keyboard for b in row]


class ParsingTests(unittest.TestCase):
    def setUp(self):
        from bot.handlers import ai_admin
        self.m = ai_admin

    def test_number_formats(self):
        p = self.m.parse_rate_number
        self.assertEqual(p("1050"), 1050.0)
        self.assertEqual(p("105.5"), 105.5)
        self.assertEqual(p("105,5"), 105.5)
        self.assertEqual(p("1.050"), 1050.0)        # tres dígitos tras el separador = miles
        self.assertEqual(p("1,050"), 1050.0)
        self.assertEqual(p("1.050,50"), 1050.5)
        self.assertEqual(p("1,050.50"), 1050.5)
        for bad in ("abc", "", "nan", "inf", "-5"):
            self.assertIsNone(p(bad), bad)

    def test_only_explicit_orders_propose_a_change(self):
        e = self.m.extract_rate_command
        self.assertEqual(e("actualiza la tasa a 1050"), ("set", 1050.0))
        self.assertEqual(e("Pon la tasa en 1.071,50"), ("set", 1071.5))
        self.assertEqual(e("cambia la tasa binance a 1100"), ("set", 1100.0))
        # preguntas y comentarios: nunca son una orden
        self.assertEqual(e("cuánto cambió la tasa en 2 días?")[0], "none")
        self.assertEqual(e("¿a cuánto está la tasa?")[0], "none")
        self.assertEqual(e("la tasa de hoy es 1050")[0], "none")
        self.assertEqual(e("tasa 1050")[0], "none")        # sin verbo no hay orden
        # orden sin número claro: se pide aclaración, no se adivina
        self.assertEqual(e("actualiza la tasa")[0], "ambiguous")
        self.assertEqual(e("cambia la tasa a 1050 o 1060")[0], "ambiguous")

    def test_plausibility(self):
        ok = self.m.rate_is_plausible
        self.assertTrue(ok(1100, 1071)[0])
        self.assertTrue(ok(700, 1071)[0])
        for bad in (2, 5000, 0, -5, float("nan"), float("inf"), 100001):
            self.assertFalse(ok(bad, 1071)[0], bad)
        # sin tasa previa solo se aplican los límites absolutos
        self.assertTrue(ok(36.5, None)[0])
        self.assertFalse(ok(0.5, None)[0])

    def test_keyword_detection_no_longer_updates_from_stray_numbers(self):
        d = self.m._detect_intent
        self.assertEqual(d("cuánto cambió la tasa en 2 días")["action"], "show_rate")
        self.assertEqual(d("tasa 2")["action"], "show_rate")
        self.assertEqual(d("cambio de perfil, la tasa subió 3 puntos")["action"], "show_rate")
        self.assertEqual(d("actualiza la tasa a 1050")["action"], "rate_update")
        self.assertNotIn("rate", d("actualiza la tasa a 1050"))   # el número NO viaja en la intención


class RateFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from bot.handlers import ai_admin
        self.m = ai_admin
        self.ctx = SimpleNamespace(user_data={})
        patches = [
            mock.patch("services.exchange_service.get_current_rate", new=mock.AsyncMock(return_value={"usd_binance": 1071.0})),
            mock.patch("services.exchange_service.update_rate", new=mock.AsyncMock(return_value=True)),
            mock.patch("database.users.log_admin_action", new=mock.AsyncMock(return_value=True)),
            mock.patch.object(ai_admin, "_is_admin", return_value=True),
        ]
        self.mocks = [p.start() for p in patches]
        for p in patches:
            self.addCleanup(p.stop)
        self.update_rate = self.mocks[1]
        self.log_action = self.mocks[2]

    async def propose(self, text="actualiza la tasa a 1100"):
        msg = make_message(text)
        await self.m._propose_rate_change(msg, self.ctx, text, ADMIN)
        return msg

    async def test_order_shows_a_card_and_changes_nothing(self):
        msg = await self.propose()
        self.update_rate.assert_not_awaited()
        kb = buttons(msg.reply_text.await_args.kwargs["reply_markup"])
        self.assertEqual([b.text for b in kb], ["✅ Confirmar", "❌ Cancelar"])
        nonce = self.ctx.user_data["rate_draft"]["nonce"]
        self.assertEqual(kb[0].callback_data, f"admin_nl:rate_ok:{nonce}")
        self.assertLessEqual(len(kb[0].callback_data.encode()), 64)        # límite de Telegram
        text = msg.reply_text.await_args.args[0]
        self.assertIn("1,100.00", text)
        self.assertIn("+2.7 %", text)                                        # variación visible

    async def test_implausible_value_is_refused_with_an_exit(self):
        msg = await self.propose("actualiza la tasa a 2")
        self.update_rate.assert_not_awaited()
        self.assertNotIn("rate_draft", self.ctx.user_data)
        self.assertIn("No cambié la tasa", msg.reply_text.await_args.args[0])
        self.assertIsNotNone(msg.reply_text.await_args.kwargs.get("reply_markup"))   # nunca sin salida

    async def test_ambiguous_order_asks_for_a_single_number(self):
        msg = await self.propose("actualiza la tasa")
        self.update_rate.assert_not_awaited()
        self.assertIn("un solo número", msg.reply_text.await_args.args[0])

    async def test_confirm_applies_exactly_once(self):
        await self.propose()
        nonce = self.ctx.user_data["rate_draft"]["nonce"]
        upd, q = make_callback(f"admin_nl:rate_ok:{nonce}")
        await self.m.handle_admin_nl_callback(upd, self.ctx)
        self.update_rate.assert_awaited_once_with(1100.0, ADMIN)
        self.log_action.assert_awaited_once()
        self.assertIn("Tasa actualizada", q.edit_message_text.await_args.args[0])
        # segundo toque (doble clic o botón viejo): no vuelve a aplicar y explica qué hacer
        upd2, q2 = make_callback(f"admin_nl:rate_ok:{nonce}")
        await self.m.handle_admin_nl_callback(upd2, self.ctx)
        self.assertEqual(self.update_rate.await_count, 1)
        self.assertIn("venció o ya se usó", q2.edit_message_text.await_args.args[0])
        self.assertIsNotNone(q2.edit_message_text.await_args.kwargs.get("reply_markup"))

    async def test_cancel_discards_the_draft(self):
        await self.propose()
        nonce = self.ctx.user_data["rate_draft"]["nonce"]
        upd, q = make_callback(f"admin_nl:rate_no:{nonce}")
        await self.m.handle_admin_nl_callback(upd, self.ctx)
        self.update_rate.assert_not_awaited()
        self.assertNotIn("rate_draft", self.ctx.user_data)
        self.assertIn("La tasa no cambió", q.edit_message_text.await_args.args[0])

    async def test_wrong_nonce_does_not_apply_and_keeps_the_real_draft(self):
        await self.propose()
        upd, q = make_callback("admin_nl:rate_ok:deadbeef")
        await self.m.handle_admin_nl_callback(upd, self.ctx)
        self.update_rate.assert_not_awaited()
        self.assertIn("rate_draft", self.ctx.user_data)

    async def test_expired_draft_does_not_apply(self):
        await self.propose()
        nonce = self.ctx.user_data["rate_draft"]["nonce"]
        self.ctx.user_data["rate_draft"]["expires"] = time.time() - 1
        upd, q = make_callback(f"admin_nl:rate_ok:{nonce}")
        await self.m.handle_admin_nl_callback(upd, self.ctx)
        self.update_rate.assert_not_awaited()
        self.assertNotIn("rate_draft", self.ctx.user_data)
        self.assertIn("venció", q.edit_message_text.await_args.args[0])

    async def test_rate_is_revalidated_at_confirmation_time(self):
        await self.propose("actualiza la tasa a 1100")
        nonce = self.ctx.user_data["rate_draft"]["nonce"]
        with mock.patch("services.exchange_service.get_current_rate",
                        new=mock.AsyncMock(return_value={"usd_binance": 300.0})):
            upd, q = make_callback(f"admin_nl:rate_ok:{nonce}")
            await self.m.handle_admin_nl_callback(upd, self.ctx)
        self.update_rate.assert_not_awaited()
        self.assertIn("No cambié la tasa", q.edit_message_text.await_args.args[0])

    async def test_failed_update_offers_an_alternative(self):
        self.update_rate.return_value = False
        await self.propose()
        nonce = self.ctx.user_data["rate_draft"]["nonce"]
        upd, q = make_callback(f"admin_nl:rate_ok:{nonce}")
        await self.m.handle_admin_nl_callback(upd, self.ctx)
        self.assertIn("/tasa", q.edit_message_text.await_args.args[0])

    async def test_non_admin_cannot_confirm(self):
        await self.propose()
        nonce = self.ctx.user_data["rate_draft"]["nonce"]
        upd, q = make_callback(f"admin_nl:rate_ok:{nonce}")
        with mock.patch.object(self.m, "_is_admin", return_value=False):
            await self.m.handle_admin_nl_callback(upd, self.ctx)
        self.update_rate.assert_not_awaited()

    async def test_free_text_question_never_changes_the_rate(self):
        upd = make_update("cuánto cambió la tasa en 2 días?")
        await self.m.handle_admin_free_text(upd, self.ctx)
        self.update_rate.assert_not_awaited()
        self.assertNotIn("rate_draft", self.ctx.user_data)
        self.assertIn("Tasa actual", upd.message.reply_text.await_args.args[0])

    async def test_llm_classified_rate_update_still_needs_an_explicit_order(self):
        """Si la IA clasifica «rate_update» pero el texto no es una orden, no se propone nada."""
        upd = make_update("mmm creo que la tasa debería estar por 2")
        with mock.patch.object(self.m, "_detect_intent", return_value=None), \
                mock.patch.object(self.m, "_llm_intent", new=mock.AsyncMock(return_value={"action": "rate_update", "rate": 2})):
            await self.m.handle_admin_free_text(upd, self.ctx)
        self.update_rate.assert_not_awaited()
        self.assertNotIn("rate_draft", self.ctx.user_data)
        self.assertIn("No pude identificar", upd.message.reply_text.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
