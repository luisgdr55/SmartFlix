-- Ayudantes de las pruebas de la migración O02 Lote 2 (datos ficticios). Ver tests/sql/README.md.
-- PROTECCIÓN: este archivo BORRA todas las filas de las tablas del proyecto. Solo corre en la base
-- desechable llamada o02_test (creada por tests/sql/run_o02_lote2.sh). Nunca contra Supabase.
DO $guard$ BEGIN
  IF current_database() <> 'o02_test' THEN
    RAISE EXCEPTION 'ABORTADO: este archivo borra datos y solo puede correr en la base desechable o02_test (estás en %)', current_database();
  END IF;
END $guard$;
CREATE SCHEMA IF NOT EXISTS t;
CREATE OR REPLACE FUNCTION t.reset() RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  IF current_database() <> 'o02_test' THEN RAISE EXCEPTION 'ABORTADO: t.reset() solo corre en o02_test'; END IF;
  ALTER TABLE subscription_periods DISABLE TRIGGER trg_subscription_periods_immutable;
  ALTER TABLE checkout_orders DISABLE TRIGGER trg_checkout_orders_guard;
  DELETE FROM subscription_periods; DELETE FROM checkout_orders; DELETE FROM subscriptions;
  DELETE FROM profiles; DELETE FROM accounts; DELETE FROM users; DELETE FROM platforms;
  ALTER TABLE subscription_periods ENABLE TRIGGER trg_subscription_periods_immutable;
  ALTER TABLE checkout_orders ENABLE TRIGGER trg_checkout_orders_guard;
END $$;
CREATE OR REPLACE FUNCTION t.plat(n text) RETURNS uuid LANGUAGE sql AS
  $$ INSERT INTO platforms(name, slug) VALUES (n, lower(n)) RETURNING id $$;
CREATE OR REPLACE FUNCTION t.usr(tg bigint) RETURNS uuid LANGUAGE sql AS
  $$ INSERT INTO users(telegram_id, name) VALUES (tg, 'Cliente '||tg) RETURNING id $$;
CREATE OR REPLACE FUNCTION t.prof(pl uuid, nm text, st text DEFAULT 'available', pin text DEFAULT '1111', typ text DEFAULT 'monthly')
RETURNS uuid LANGUAGE plpgsql AS $$
DECLARE a uuid; p uuid;
BEGIN
  SELECT id INTO a FROM accounts WHERE platform_id = pl LIMIT 1;
  IF a IS NULL THEN INSERT INTO accounts(platform_id,email,password) VALUES (pl,'c@ejemplo.test','x') RETURNING id INTO a; END IF;
  INSERT INTO profiles(account_id, platform_id, profile_name, pin, profile_type, status) VALUES (a, pl, nm, pin, typ, st) RETURNING id INTO p;
  RETURN p;
END $$;
CREATE OR REPLACE FUNCTION t.sub(u uuid, pl uuid, pr uuid, st text, endd timestamptz, plan text DEFAULT 'monthly')
RETURNS uuid LANGUAGE sql AS $$
  INSERT INTO subscriptions(user_id, platform_id, profile_id, plan_type, start_date, end_date, price_usd, status,
                            reminder_sent, expiry_notified, debt_reminder_count)
  VALUES (u, pl, pr, plan, now() - interval '40 days', endd, 5, st, true, true, 4) RETURNING id $$;
-- items
CREATE OR REPLACE FUNCTION t.order_(u uuid, items jsonb, rate numeric DEFAULT 1000, ref text DEFAULT NULL, st text DEFAULT 'pending')
RETURNS uuid LANGUAGE plpgsql AS $$
DECLARE tu numeric := 0; tb numeric := 0; i jsonb; id uuid;
BEGIN
  FOR i IN SELECT * FROM jsonb_array_elements(items) LOOP
    tu := tu + (i->>'subtotal_usd')::numeric; tb := tb + (i->>'amount_bs')::numeric; END LOOP;
  INSERT INTO checkout_orders(user_id, status, manifest, total_usd, total_bs, rate_used, payment_reference)
  VALUES (u, st, jsonb_build_object('version',1,'items',items,'total_usd',tu,'total_bs',tb,'rate_used',rate), tu, tb, rate, ref)
  RETURNING checkout_orders.id INTO id;
  RETURN id;
END $$;
CREATE OR REPLACE FUNCTION t.item_ren(sid uuid, pl uuid, n int, unit numeric DEFAULT 5) RETURNS jsonb LANGUAGE sql AS $$
  SELECT jsonb_build_object('tipo','renovacion','subscription_id',sid,'platform_id',pl,'plan_type','monthly','periodos',n,
     'precio_unitario_usd',unit,'cargo_deuda_usd',0,'subtotal_usd',unit*n,'amount_bs',unit*n*1000) $$;
CREATE OR REPLACE FUNCTION t.item_new(pl uuid, plan text DEFAULT 'monthly', unit numeric DEFAULT 5) RETURNS jsonb LANGUAGE sql AS $$
  SELECT jsonb_build_object('tipo','nueva','subscription_id',NULL,'platform_id',pl,'plan_type',plan,'profile_type',plan,'periodos',1,
     'precio_unitario_usd',unit,'cargo_deuda_usd',0,'subtotal_usd',unit,'amount_bs',unit*1000) $$;
CREATE OR REPLACE FUNCTION t.item_chg(sid uuid, newpl uuid, unit numeric DEFAULT 6, debt numeric DEFAULT 5) RETURNS jsonb LANGUAGE sql AS $$
  SELECT jsonb_build_object('tipo','cambio','subscription_id',sid,'platform_id',newpl,'plan_type','monthly','periodos',1,
     'precio_unitario_usd',unit,'cargo_deuda_usd',debt,'subtotal_usd',unit+debt,'amount_bs',(unit+debt)*1000) $$;
-- espera un error con ese mensaje
CREATE OR REPLACE FUNCTION t.expect_err(q text, code text) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  EXECUTE q;
  RAISE EXCEPTION 'se esperaba el error %, pero no falló: %', code, q;
EXCEPTION WHEN OTHERS THEN
  IF SQLERRM <> code THEN RAISE EXCEPTION 'esperaba %, obtuve %', code, SQLERRM; END IF;
END $$;
CREATE OR REPLACE FUNCTION t.approve(o uuid, actor text DEFAULT 'admin:1', reason text DEFAULT NULL, ref text DEFAULT NULL)
RETURNS jsonb LANGUAGE sql AS $$ SELECT approve_checkout_order(o, actor, reason, ref) $$;
