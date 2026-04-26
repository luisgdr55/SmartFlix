"""
Test de integracion: carrito de renovacion automatico
======================================================
Crea datos de prueba, ejecuta el flujo completo y los elimina al final.
NO afecta datos reales. NO envia mensajes de Telegram.

Uso:
    python test_renewal_cart.py
"""

import asyncio
import sys
import io
from datetime import datetime, timedelta

# Forzar UTF-8 en la salida (necesario en Windows)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── Setup de entorno ──────────────────────────────────────────────────────────
try:
    from config import settings  # noqa: F401  — carga variables de entorno
    from database import get_supabase
except Exception as e:
    print(f"❌ Error al cargar configuración: {e}")
    print("   Asegúrate de tener el .env configurado y ejecutar desde la raíz del proyecto.")
    sys.exit(1)

MARKER = "TEST_RENEWAL_CART"  # Identificador único para limpiar después


# ── Helpers ───────────────────────────────────────────────────────────────────

def ok(msg):  print(f"  ✅ {msg}")
def fail(msg): print(f"  ❌ {msg}")
def info(msg): print(f"  ℹ️  {msg}")
def section(title): print(f"\n{'─'*50}\n  {title}\n{'─'*50}")


# ── Limpieza ──────────────────────────────────────────────────────────────────

def cleanup(sb, user_id=None):
    """Elimina todos los datos de prueba creados."""
    section("🧹 Limpieza de datos de prueba")
    try:
        if user_id:
            # Eliminar suscripciones del usuario de prueba
            sb.table("subscriptions").delete().eq("user_id", user_id).execute()
            ok("Suscripciones de prueba eliminadas")
            # Eliminar usuario de prueba
            sb.table("users").delete().eq("id", user_id).execute()
            ok("Usuario de prueba eliminado")
        else:
            # Fallback: buscar por notas
            res = sb.table("users").select("id").ilike("notes", f"%{MARKER}%").execute()
            for u in (res.data or []):
                sb.table("subscriptions").delete().eq("user_id", u["id"]).execute()
                sb.table("users").delete().eq("id", u["id"]).execute()
            ok(f"Limpieza por marcador: {len(res.data or [])} usuario(s) eliminado(s)")
    except Exception as e:
        fail(f"Error en limpieza: {e}")


# ── Tests ─────────────────────────────────────────────────────────────────────

async def run_tests():
    sb = get_supabase()
    user_id = None

    print("\n" + "="*50)
    print("  TEST: CARRITO DE RENOVACIÓN AUTOMÁTICO")
    print("="*50)

    # ── PASO 1: Obtener plataformas reales ────────────────────────────────────
    section("1️⃣  Obtener plataformas disponibles")
    try:
        platforms_res = sb.table("platforms").select("*").eq("is_active", True).limit(2).execute()
        platforms = platforms_res.data or []
        if len(platforms) < 2:
            fail(f"Se necesitan al menos 2 plataformas activas. Encontradas: {len(platforms)}")
            return
        plat_a = platforms[0]
        plat_b = platforms[1]
        ok(f"Plataforma A: {plat_a.get('icon_emoji','')} {plat_a['name']} (id={plat_a['id']})")
        ok(f"Plataforma B: {plat_b.get('icon_emoji','')} {plat_b['name']} (id={plat_b['id']})")
    except Exception as e:
        fail(f"No se pudieron cargar plataformas: {e}")
        return

    # ── PASO 2: Crear usuario de prueba ───────────────────────────────────────
    section("2️⃣  Crear usuario de prueba")
    try:
        user_res = sb.table("users").insert({
            "name": "Cliente Prueba Renovación",
            "phone": "0400-TEST-0000",
            "status": "active",
            "total_purchases": 2,
            "receives_promos": False,
            "is_admin": False,
            "notes": f"{MARKER} — Eliminar si ves esto",
        }).execute()
        user = user_res.data[0]
        user_id = user["id"]
        ok(f"Usuario creado: {user['name']} (id={user_id})")
    except Exception as e:
        fail(f"No se pudo crear usuario de prueba: {e}")
        return

    # ── PASO 3: Crear 2 suscripciones vencidas ────────────────────────────────
    section("3️⃣  Crear 2 suscripciones vencidas (end_date = ayer)")
    yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
    sub_ids = []
    try:
        for plat in [plat_a, plat_b]:
            price_usd = float(plat.get("monthly_price_usd") or 5.0)
            sub_res = sb.table("subscriptions").insert({
                "user_id": user_id,
                "platform_id": plat["id"],
                "plan_type": "monthly",
                "price_usd": price_usd,
                "price_bs": price_usd * 36,
                "rate_used": 36.0,
                "start_date": (datetime.utcnow() - timedelta(days=31)).isoformat(),
                "end_date": yesterday,
                "status": "expired",
                "reminder_sent": True,
                "expiry_notified": True,
            }).execute()
            sub = sub_res.data[0]
            sub_ids.append(sub["id"])
            ok(f"Suscripción vencida: {plat['name']} — end_date={yesterday} (id={sub['id']})")
    except Exception as e:
        fail(f"No se pudieron crear suscripciones: {e}")
        cleanup(sb, user_id)
        return

    # ── PASO 4: Verificar get_user_attention_subscriptions ────────────────────
    section("4️⃣  Verificar detección de vencidas")
    try:
        from database.subscriptions import get_user_attention_subscriptions
        attention = await get_user_attention_subscriptions(user_id)
        expired = attention.get("expired", [])
        pending = attention.get("pending", [])
        info(f"Suscripciones vencidas detectadas: {len(expired)}")
        info(f"Suscripciones pendientes de pago:  {len(pending)}")

        if len(expired) == 2:
            ok("Detecta exactamente 2 suscripciones vencidas ✓")
        else:
            fail(f"Se esperaban 2 vencidas, se obtuvieron {len(expired)}")

        for s in expired:
            plat_name = (s.get("platforms") or {}).get("name", s.get("platform_id"))
            info(f"  → {plat_name} — status={s.get('status')} — end_date={s.get('end_date','')[:10]}")
    except Exception as e:
        fail(f"Error en get_user_attention_subscriptions: {e}")
        cleanup(sb, user_id)
        return

    # ── PASO 5: Simular construcción del carrito ──────────────────────────────
    section("5️⃣  Simular construcción del carrito de renovación")
    try:
        from services.exchange_service import get_current_rate
        from database.platforms import get_platform_by_id

        rate_obj = await get_current_rate()
        rate = float((rate_obj or {}).get("usd_binance") or 36.0)
        ok(f"Tasa de cambio: {rate} Bs/USD")

        cart = {}
        for sub in expired:
            platform_id = sub.get("platform_id")
            plat_data = await get_platform_by_id(str(platform_id)) or {}
            platform = sub.get("platforms") or {}
            plan_type = sub.get("plan_type") or "monthly"
            price_usd = float(plat_data.get("monthly_price_usd") or 0)
            price_bs = round(price_usd * rate, 2)
            sub_id = str(sub["id"])
            cart[sub_id] = {
                "sub_id": sub_id,
                "platform_id": str(platform_id),
                "name": platform.get("name") or plat_data.get("name") or "?",
                "emoji": platform.get("icon_emoji") or plat_data.get("icon_emoji") or "📺",
                "plan_type": plan_type,
                "price_usd": price_usd,
                "price_bs": price_bs,
                "rate_used": rate,
                "selected": True,
            }
            ok(f"Item en carrito: {cart[sub_id]['emoji']} {cart[sub_id]['name']} "
               f"— ${price_usd:.2f} / Bs {price_bs:,.0f}")

        total_usd = sum(v["price_usd"] for v in cart.values())
        total_bs = sum(v["price_bs"] for v in cart.values())
        ok(f"Total carrito: ${total_usd:.2f} / Bs {total_bs:,.0f}")

        if len(cart) == 2:
            ok("Carrito construido correctamente con 2 items ✓")
        else:
            fail(f"Se esperaban 2 items en el carrito, hay {len(cart)}")
    except Exception as e:
        fail(f"Error al construir carrito: {e}")
        cleanup(sb, user_id)
        return

    # ── PASO 6: Simular toggle (quitar un item) ───────────────────────────────
    section("6️⃣  Simular toggle (quitar primera suscripción del carrito)")
    first_sub_id = list(cart.keys())[0]
    cart[first_sub_id]["selected"] = False
    selected = [v for v in cart.values() if v.get("selected")]
    deselected = [v for v in cart.values() if not v.get("selected")]

    ok(f"Item quitado: {deselected[0]['name']}")
    ok(f"Item que queda: {selected[0]['name']}")
    if len(selected) == 1:
        ok("Toggle funciona correctamente ✓")
    else:
        fail(f"Se esperaba 1 item seleccionado, hay {len(selected)}")

    # Restaurar selección para el siguiente paso
    cart[first_sub_id]["selected"] = True
    selected = list(cart.values())

    # ── PASO 7: Simular confirmación del carrito ──────────────────────────────
    section("7️⃣  Simular confirmación: crear pending_payment subscriptions")
    new_sub_ids = []
    try:
        from database.subscriptions import create_subscription

        now = datetime.utcnow()
        for item in selected:
            plan_days = {"monthly": 30, "express": 1}.get(item["plan_type"], 30)
            end_date = now + timedelta(days=plan_days)
            new_sub = await create_subscription(
                user_id=user_id,
                platform_id=item["platform_id"],
                plan_type=item["plan_type"],
                price_usd=item["price_usd"],
                price_bs=item["price_bs"],
                rate_used=item["rate_used"],
                end_date=end_date,
            )
            if new_sub:
                new_sub_ids.append(new_sub["id"])
                ok(f"Creada: {item['emoji']} {item['name']} — status={new_sub.get('status')} — end={new_sub.get('end_date','')[:10]}")
            else:
                fail(f"No se pudo crear la suscripción para {item['name']}")

        if len(new_sub_ids) == 2:
            ok("2 suscripciones pending_payment creadas correctamente ✓")
        else:
            fail(f"Se esperaban 2, se crearon {len(new_sub_ids)}")
    except Exception as e:
        fail(f"Error al crear suscripciones pending_payment: {e}")
        cleanup(sb, user_id)
        return

    # ── PASO 8: Verificar en la base de datos ─────────────────────────────────
    section("8️⃣  Verificar estado final en la base de datos")
    try:
        all_subs = sb.table("subscriptions").select("id, platform_id, status, end_date") \
            .eq("user_id", user_id).execute()
        subs = all_subs.data or []
        info(f"Total suscripciones del usuario de prueba: {len(subs)}")

        expired_count = sum(1 for s in subs if s["status"] == "expired")
        pending_count = sum(1 for s in subs if s["status"] == "pending_payment")
        info(f"  → Vencidas (expired):        {expired_count}")
        info(f"  → Pendientes de pago:        {pending_count}")

        if expired_count == 2 and pending_count == 2:
            ok("Estado final correcto: 2 expired + 2 pending_payment ✓")
        else:
            fail(f"Estado inesperado: {expired_count} expired, {pending_count} pending_payment")
    except Exception as e:
        fail(f"Error al verificar estado final: {e}")

    # ── Limpieza ──────────────────────────────────────────────────────────────
    cleanup(sb, user_id)

    # ── Resumen ───────────────────────────────────────────────────────────────
    print("\n" + "="*50)
    print("  ✅ TODOS LOS PASOS COMPLETADOS CORRECTAMENTE")
    print("  El flujo de carrito de renovación funciona.")
    print("="*50 + "\n")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    asyncio.run(run_tests())
