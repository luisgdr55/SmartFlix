-- Escenarios de la migración O02 Lote 2 (ASSERT de plpgsql; cualquier fallo aborta con ERROR).
-- PROTECCIÓN: este archivo BORRA todas las filas de las tablas del proyecto. Solo corre en la base
-- desechable llamada o02_test (creada por tests/sql/run_o02_lote2.sh). Nunca contra Supabase.
DO $guard$ BEGIN
  IF current_database() <> 'o02_test' THEN
    RAISE EXCEPTION 'ABORTADO: este archivo borra datos y solo puede correr en la base desechable o02_test (estás en %)', current_database();
  END IF;
END $guard$;
\set ON_ERROR_STOP 1
\echo '== 1. nueva mensual + idempotencia'
DO $$ DECLARE pl uuid; u uuid; p uuid; o uuid; r jsonb; r2 jsonb; n1 int; n2 int; s subscriptions%ROWTYPE; exp timestamptz;
BEGIN
  PERFORM t.reset(); pl := t.plat('Netflix'); u := t.usr(1); p := t.prof(pl,'P1');
  o := t.order_(u, jsonb_build_array(t.item_new(pl)), 1000, 'REF-1');
  r := t.approve(o);
  ASSERT (r->>'already_applied')::bool = false AND (r->>'ok')::bool;
  SELECT * INTO s FROM subscriptions WHERE user_id = u;
  ASSERT s.status = 'active' AND s.profile_id = p AND s.price_usd = 5 AND s.price_bs = 5000 AND s.rate_used = 1000;
  exp := (((now() AT TIME ZONE 'America/Caracas')::date + interval '1 month')::date + time '23:59:59')::timestamp AT TIME ZONE 'America/Caracas';
  ASSERT s.end_date = exp, 'fin de periodo ' || s.end_date || ' vs ' || exp;
  ASSERT (SELECT status FROM profiles WHERE id = p) = 'occupied';
  ASSERT (SELECT count(*) FROM subscription_periods WHERE order_id = o AND kind = 'nueva') = 1;
  ASSERT (SELECT status FROM checkout_orders WHERE id = o) = 'approved';
  ASSERT (SELECT decided_by FROM checkout_orders WHERE id = o) = 'admin:1';
  SELECT count(*) INTO n1 FROM subscriptions; 
  r2 := t.approve(o);
  ASSERT (r2->>'already_applied')::bool = true, 'segunda llamada debe ser already_applied';
  SELECT count(*) INTO n2 FROM subscriptions;
  ASSERT n1 = n2 AND (SELECT count(*) FROM subscription_periods) = 1, 'sin efectos adicionales';
  ASSERT r2->'items' = r->'items', 'devuelve lo ya aplicado';
END $$;
\echo '== 1b. nueva express = ahora + 24 h'
DO $$ DECLARE pl uuid; u uuid; o uuid; s subscriptions%ROWTYPE;
BEGIN
  PERFORM t.reset(); pl := t.plat('Netflix'); u := t.usr(1); PERFORM t.prof(pl,'E1','available','2222','express');
  o := t.order_(u, jsonb_build_array(t.item_new(pl,'express',1)));
  PERFORM t.approve(o);
  SELECT * INTO s FROM subscriptions WHERE user_id = u;
  ASSERT s.plan_type = 'express' AND s.end_date - s.start_date = interval '24 hours';
END $$;
\echo '== 2. renovacion de vencida: 2 periodos desde end_date; reinicia avisos; conserva perfil'
DO $$ DECLARE pl uuid; u uuid; p uuid; sid uuid; o uuid; s subscriptions%ROWTYPE; r jsonb;
BEGIN
  PERFORM t.reset(); pl := t.plat('Netflix'); u := t.usr(1); p := t.prof(pl,'P1','occupied');
  -- 19/05/2026 23:59:59 Caracas
  sid := t.sub(u, pl, p, 'expired', timestamptz '2026-05-19 23:59:59-04');
  o := t.order_(u, jsonb_build_array(t.item_ren(sid, pl, 2)));
  r := t.approve(o, 'admin:1', NULL, 'REF-2');
  SELECT * INTO s FROM subscriptions WHERE id = sid;
  ASSERT s.end_date = timestamptz '2026-07-19 23:59:59-04', 'nuevo fin ' || s.end_date;
  ASSERT s.status = 'active' AND s.profile_id = p AND s.payment_reference = 'REF-2';
  ASSERT s.reminder_sent = false AND s.expiry_notified = false AND s.debt_reminder_count = 0, 'avisos reiniciados';
  ASSERT (SELECT count(*) FROM subscriptions) = 1, 'no se crea ninguna suscripción';
  ASSERT (SELECT period_start FROM subscription_periods WHERE order_id = o) = timestamptz '2026-05-19 23:59:59-04';
  ASSERT (SELECT amount_usd FROM subscription_periods WHERE order_id = o) = 10;
  ASSERT (r->'items'->0->>'profile_changed')::bool = false;
END $$;
\echo '== 3. dos servicios de la misma plataforma: solo cambia el renovado'
DO $$ DECLARE pl uuid; u uuid; pa uuid; pb uuid; a uuid; b uuid; o uuid; ea timestamptz; eb timestamptz;
BEGIN
  PERFORM t.reset(); pl := t.plat('Netflix'); u := t.usr(1); pa := t.prof(pl,'A','occupied'); pb := t.prof(pl,'B','occupied');
  a := t.sub(u, pl, pa, 'active', timestamptz '2026-12-10 23:59:59-04');
  b := t.sub(u, pl, pb, 'expired', timestamptz '2026-05-19 23:59:59-04');
  SELECT end_date INTO ea FROM subscriptions WHERE id = a;
  o := t.order_(u, jsonb_build_array(t.item_ren(b, pl, 1)));
  PERFORM t.approve(o);
  ASSERT (SELECT end_date FROM subscriptions WHERE id = a) = ea, 'la otra no cambia';
  ASSERT (SELECT status FROM subscriptions WHERE id = a) = 'active';
  ASSERT (SELECT end_date FROM subscriptions WHERE id = b) = timestamptz '2026-06-19 23:59:59-04';
  -- y las dos juntas
  o := t.order_(u, jsonb_build_array(t.item_ren(a, pl, 1), t.item_ren(b, pl, 1)));
  PERFORM t.approve(o);
  ASSERT (SELECT count(*) FROM subscription_periods) = 3;
END $$;
\echo '== 4. fin de mes y bisiesto'
DO $$ DECLARE pl uuid; u uuid; p uuid; s1 uuid; s2 uuid; o uuid;
BEGIN
  PERFORM t.reset(); pl := t.plat('Netflix'); u := t.usr(1); p := t.prof(pl,'A','occupied');
  s1 := t.sub(u, pl, p, 'active', timestamptz '2026-01-31 23:59:59-04');
  o := t.order_(u, jsonb_build_array(t.item_ren(s1, pl, 1))); PERFORM t.approve(o);
  ASSERT (SELECT end_date FROM subscriptions WHERE id = s1) = timestamptz '2026-02-28 23:59:59-04';
  UPDATE subscriptions SET end_date = timestamptz '2028-01-31 23:59:59-04' WHERE id = s1;
  o := t.order_(u, jsonb_build_array(t.item_ren(s1, pl, 1))); PERFORM t.approve(o);
  ASSERT (SELECT end_date FROM subscriptions WHERE id = s1) = timestamptz '2028-02-29 23:59:59-04';
END $$;
\echo '== 4b. cerca de medianoche UTC/Caracas: 19/05 23:59:59 Caracas = 20/05 03:59:59 UTC conserva el día 19'
DO $$ DECLARE pl uuid; u uuid; p uuid; s1 uuid; o uuid;
BEGIN
  PERFORM t.reset(); pl := t.plat('Netflix'); u := t.usr(1); p := t.prof(pl,'A','occupied');
  s1 := t.sub(u, pl, p, 'active', timestamptz '2026-05-20 03:59:59+00');
  o := t.order_(u, jsonb_build_array(t.item_ren(s1, pl, 1))); PERFORM t.approve(o);
  ASSERT (SELECT end_date FROM subscriptions WHERE id = s1) = timestamptz '2026-06-19 23:59:59-04';
  -- legado con hora arbitraria (20:30 Caracas = 00:30 UTC del día siguiente)
  UPDATE subscriptions SET end_date = timestamptz '2026-05-21 00:30:00+00' WHERE id = s1;
  o := t.order_(u, jsonb_build_array(t.item_ren(s1, pl, 1))); PERFORM t.approve(o);
  ASSERT (SELECT end_date FROM subscriptions WHERE id = s1) = timestamptz '2026-06-20 23:59:59-04';
END $$;
\echo '== 5. N-A perfil: reasignado a otro cliente -> se asigna otro y no se toca el ajeno'
DO $$ DECLARE pl uuid; u1 uuid; u2 uuid; p1 uuid; p2 uuid; s1 uuid; s2 uuid; o uuid; r jsonb;
BEGIN
  PERFORM t.reset(); pl := t.plat('Netflix'); u1 := t.usr(1); u2 := t.usr(2);
  p1 := t.prof(pl,'P1','occupied','1111'); p2 := t.prof(pl,'P2','available','2222');
  s1 := t.sub(u1, pl, p1, 'expired', timestamptz '2026-05-19 23:59:59-04');   -- su perfil...
  s2 := t.sub(u2, pl, p1, 'active',  timestamptz '2027-01-19 23:59:59-04');   -- ...ya lo usa otro cliente
  o := t.order_(u1, jsonb_build_array(t.item_ren(s1, pl, 1)));
  r := t.approve(o);
  ASSERT (r->'items'->0->>'profile_changed')::bool = true;
  ASSERT (SELECT profile_id FROM subscriptions WHERE id = s1) = p2, 'recibe el otro perfil';
  ASSERT (SELECT profile_id FROM subscriptions WHERE id = s2) = p1, 'el ajeno no se toca';
  ASSERT (SELECT status FROM profiles WHERE id = p2) = 'occupied' AND (SELECT pin FROM profiles WHERE id = p1) = '1111';
END $$;
\echo '== 5b. perfil liberado y nadie lo usa -> se reutiliza y vuelve a ocupar'
DO $$ DECLARE pl uuid; u uuid; p uuid; s uuid; o uuid; r jsonb;
BEGIN
  PERFORM t.reset(); pl := t.plat('Netflix'); u := t.usr(1); p := t.prof(pl,'P1','available','3333');
  s := t.sub(u, pl, p, 'expired', timestamptz '2026-05-19 23:59:59-04');
  o := t.order_(u, jsonb_build_array(t.item_ren(s, pl, 1))); r := t.approve(o);
  ASSERT (r->'items'->0->>'profile_changed')::bool = false AND (SELECT profile_id FROM subscriptions WHERE id = s) = p;
  ASSERT (SELECT status FROM profiles WHERE id = p) = 'occupied';
END $$;
\echo '== 5c. sin perfil disponible -> profile_unavailable y TODO revierte (la orden sigue pendiente)'
DO $$ DECLARE pl uuid; u1 uuid; u2 uuid; p1 uuid; s1 uuid; s3 uuid; o uuid; before_end timestamptz;
BEGIN
  PERFORM t.reset(); pl := t.plat('Netflix'); u1 := t.usr(1); u2 := t.usr(2);
  p1 := t.prof(pl,'P1','occupied');
  s1 := t.sub(u1, pl, p1, 'expired', timestamptz '2026-05-19 23:59:59-04');
  PERFORM t.sub(u2, pl, p1, 'active', timestamptz '2027-01-19 23:59:59-04');
  s3 := t.sub(u1, pl, t.prof(pl,'P9','occupied'), 'expired', timestamptz '2026-04-19 23:59:59-04');
  SELECT end_date INTO before_end FROM subscriptions WHERE id = s3;
  -- ítem 1 renovable sin perfil libre disponible (p1 lo usa otro y no hay más); ítem 2 sí es renovable
  o := t.order_(u1, jsonb_build_array(t.item_ren(s3, pl, 1), t.item_ren(s1, pl, 1)));
  PERFORM t.expect_err(format('SELECT approve_checkout_order(%L, ''admin:1'', NULL, NULL)', o), 'profile_unavailable');
  ASSERT (SELECT status FROM checkout_orders WHERE id = o) = 'pending', 'la orden sigue pendiente';
  ASSERT (SELECT end_date FROM subscriptions WHERE id = s3) = before_end, 'nada a medias';
  ASSERT (SELECT count(*) FROM subscription_periods) = 0;
END $$;
\echo '== 6. cambio de plataforma'
DO $$ DECLARE a uuid; b uuid; u uuid; pa uuid; pb uuid; s uuid; o uuid; r jsonb; sb subscriptions%ROWTYPE;
BEGIN
  PERFORM t.reset(); a := t.plat('Netflix'); b := t.plat('HBO'); u := t.usr(1);
  pa := t.prof(a,'PA','occupied','1111'); pb := t.prof(b,'PB','available','2222');
  s := t.sub(u, a, pa, 'expired', timestamptz '2026-05-19 23:59:59-04');
  o := t.order_(u, jsonb_build_array(t.item_chg(s, b)));
  r := t.approve(o);
  SELECT * INTO sb FROM subscriptions WHERE id = s;
  ASSERT sb.platform_id = b AND sb.profile_id = pb AND sb.status = 'active' AND sb.price_usd = 6 AND sb.debt_reminder_count = 0;
  ASSERT (SELECT count(*) FROM subscriptions) = 1;
  ASSERT (SELECT status FROM profiles WHERE id = pa) = 'available' AND (SELECT pin FROM profiles WHERE id = pa) <> '1111', 'origen liberado y PIN rotado';
  ASSERT (SELECT status FROM profiles WHERE id = pb) = 'occupied';
  ASSERT (r->'items'->0->'released_profile'->>'released')::bool = true AND r->'items'->0->'released_profile'->>'old_pin' = '1111';
  ASSERT (SELECT amount_usd FROM subscription_periods WHERE order_id = o) = 11 AND (SELECT kind FROM subscription_periods WHERE order_id = o) = 'cambio';
END $$;
\echo '== 6b. cambio sobre una suscripción vigente -> subscription_not_overdue; perfil origen compartido no se libera'
DO $$ DECLARE a uuid; b uuid; u uuid; u2 uuid; pa uuid; s uuid; o uuid; r jsonb;
BEGIN
  PERFORM t.reset(); a := t.plat('Netflix'); b := t.plat('HBO'); u := t.usr(1); u2 := t.usr(2);
  pa := t.prof(a,'PA','occupied','1111'); PERFORM t.prof(b,'PB','available');
  s := t.sub(u, a, pa, 'active', now() + interval '10 days');
  o := t.order_(u, jsonb_build_array(t.item_chg(s, b)));
  PERFORM t.expect_err(format('SELECT approve_checkout_order(%L, ''a'', NULL, NULL)', o), 'subscription_not_overdue');
  UPDATE subscriptions SET end_date = now() - interval '3 days', status = 'expired' WHERE id = s;
  PERFORM t.sub(u2, a, pa, 'active', now() + interval '20 days');   -- otro cliente usa el perfil origen
  r := t.approve(o);
  ASSERT (r->'items'->0->'released_profile'->>'released')::bool = false AND r->'items'->0->'released_profile'->>'reason' = 'otra_suscripcion_activa';
  ASSERT (SELECT pin FROM profiles WHERE id = pa) = '1111' AND (SELECT status FROM profiles WHERE id = pa) = 'occupied';
END $$;
\echo '== 7. orden mixta es atómica: el 3.er ítem falla y los dos primeros revierten'
DO $$ DECLARE pl uuid; u uuid; p uuid; s uuid; o uuid; pl2 uuid;
BEGIN
  PERFORM t.reset(); pl := t.plat('Netflix'); pl2 := t.plat('HBO'); u := t.usr(1); p := t.prof(pl,'P1','occupied');
  PERFORM t.prof(pl,'P2','available');   -- sirve para la nueva de Netflix
  s := t.sub(u, pl, p, 'expired', timestamptz '2026-05-19 23:59:59-04');
  o := t.order_(u, jsonb_build_array(t.item_ren(s, pl, 1), t.item_new(pl), t.item_new(pl2)));  -- HBO sin perfiles
  PERFORM t.expect_err(format('SELECT approve_checkout_order(%L, ''a'', NULL, NULL)', o), 'profile_unavailable');
  ASSERT (SELECT status FROM checkout_orders WHERE id = o) = 'pending';
  ASSERT (SELECT count(*) FROM subscriptions) = 1 AND (SELECT status FROM subscriptions WHERE id = s) = 'expired';
  ASSERT (SELECT status FROM profiles WHERE profile_name = 'P2') = 'available';
  ASSERT (SELECT count(*) FROM subscription_periods) = 0;
END $$;
\echo '== 8. estados, motivo, actor, dueño y manifiesto'
DO $$ DECLARE pl uuid; u uuid; u2 uuid; p uuid; s uuid; o uuid; s2 uuid;
BEGIN
  PERFORM t.reset(); pl := t.plat('Netflix'); u := t.usr(1); u2 := t.usr(2); PERFORM t.prof(pl,'P1','available');
  p := t.prof(pl,'PX','occupied'); s := t.sub(u, pl, p, 'active', now() + interval '5 days');
  -- no existe
  PERFORM t.expect_err('SELECT approve_checkout_order(gen_random_uuid(), ''a'', NULL, NULL)', 'order_not_found');
  -- actor
  o := t.order_(u, jsonb_build_array(t.item_new(pl)));
  PERFORM t.expect_err(format('SELECT approve_checkout_order(%L, '''', NULL, NULL)', o), 'actor_required');
  PERFORM t.expect_err(format('SELECT approve_checkout_order(%L, NULL, NULL, NULL)', o), 'actor_required');
  -- estados que no se aprueban
  UPDATE checkout_orders SET status = 'rejected' WHERE id = o;
  PERFORM t.expect_err(format('SELECT approve_checkout_order(%L, ''a'', ''x'', NULL)', o), 'invalid_state');
  o := t.order_(u, jsonb_build_array(t.item_new(pl)), 1000, NULL, 'pending');
  UPDATE checkout_orders SET status = 'cancelled' WHERE id = o;
  PERFORM t.expect_err(format('SELECT approve_checkout_order(%L, ''a'', ''x'', NULL)', o), 'invalid_state');
  -- caducada: exige motivo
  o := t.order_(u, jsonb_build_array(t.item_new(pl)));
  UPDATE checkout_orders SET status = 'expired' WHERE id = o;
  PERFORM t.expect_err(format('SELECT approve_checkout_order(%L, ''a'', NULL, NULL)', o), 'reason_required');
  PERFORM t.expect_err(format('SELECT approve_checkout_order(%L, ''a'', ''   '', NULL)', o), 'reason_required');
  PERFORM t.approve(o, 'admin:1', 'comprobante tardío verificado');
  ASSERT (SELECT decision_reason FROM checkout_orders WHERE id = o) = 'comprobante tardío verificado';
  -- suscripción ajena
  s2 := t.sub(u2, pl, t.prof(pl,'PY','occupied'), 'active', now() + interval '5 days');
  o := t.order_(u, jsonb_build_array(t.item_ren(s2, pl, 1)));
  PERFORM t.expect_err(format('SELECT approve_checkout_order(%L, ''a'', NULL, NULL)', o), 'subscription_not_owned');
  -- suscripción inexistente
  o := t.order_(u, jsonb_build_array(t.item_ren(gen_random_uuid(), pl, 1)));
  PERFORM t.expect_err(format('SELECT approve_checkout_order(%L, ''a'', NULL, NULL)', o), 'subscription_not_found');
  -- no renovable (cancelada)
  UPDATE subscriptions SET status = 'cancelled' WHERE id = s;
  o := t.order_(u, jsonb_build_array(t.item_ren(s, pl, 1)));
  PERFORM t.expect_err(format('SELECT approve_checkout_order(%L, ''a'', NULL, NULL)', o), 'subscription_not_renewable');
  -- importes que no suman
  o := t.order_(u, jsonb_build_array(t.item_new(pl)));
  UPDATE checkout_orders SET total_bs = total_bs + 1 WHERE id = o;
  PERFORM t.expect_err(format('SELECT approve_checkout_order(%L, ''a'', NULL, NULL)', o), 'invalid_manifest');
  -- periodos fuera de rango
  o := t.order_(u, jsonb_build_array(jsonb_set(t.item_ren(s2, pl, 1), '{periodos}', '13')));
  PERFORM t.expect_err(format('SELECT approve_checkout_order(%L, ''a'', NULL, NULL)', o), 'invalid_manifest');
END $$;
\echo '== 9. referencia duplicada entre órdenes aprobadas revierte los efectos'
DO $$ DECLARE pl uuid; u uuid; o1 uuid; o2 uuid;
BEGIN
  PERFORM t.reset(); pl := t.plat('Netflix'); u := t.usr(1); PERFORM t.prof(pl,'P1'); PERFORM t.prof(pl,'P2');
  o1 := t.order_(u, jsonb_build_array(t.item_new(pl)), 1000, 'DUP-1'); PERFORM t.approve(o1);
  o2 := t.order_(u, jsonb_build_array(t.item_new(pl)), 1000, 'DUP-1');
  PERFORM t.expect_err(format('SELECT approve_checkout_order(%L, ''a'', NULL, NULL)', o2), 'duplicate_reference');
  ASSERT (SELECT count(*) FROM subscriptions) = 1 AND (SELECT status FROM profiles WHERE profile_name = 'P2') = 'available';
  ASSERT (SELECT status FROM checkout_orders WHERE id = o2) = 'pending';
END $$;
\echo '== 10. inmutabilidad: orden aprobada, historial y transiciones'
DO $$ DECLARE pl uuid; u uuid; o uuid;
BEGIN
  PERFORM t.reset(); pl := t.plat('Netflix'); u := t.usr(1); PERFORM t.prof(pl,'P1');
  o := t.order_(u, jsonb_build_array(t.item_new(pl)), 1000, 'R-1'); PERFORM t.approve(o);
  PERFORM t.expect_err(format('UPDATE checkout_orders SET total_usd = 1 WHERE id = %L', o), 'checkout_orders: la orden en estado approved es inmutable');
  PERFORM t.expect_err(format('UPDATE checkout_orders SET manifest = ''{}'' WHERE id = %L', o), 'checkout_orders: la orden en estado approved es inmutable');
  PERFORM t.expect_err(format('DELETE FROM checkout_orders WHERE id = %L', o), 'checkout_orders: una orden aprobada no se borra (anúlala)');
  PERFORM t.expect_err(format('UPDATE checkout_orders SET status = ''pending'' WHERE id = %L', o), 'checkout_orders: transición no permitida approved -> pending');
  PERFORM t.expect_err('UPDATE subscription_periods SET amount_usd = 1', 'subscription_periods es un historial: solo se inserta (corrige con un asiento contrario)');
  PERFORM t.expect_err('DELETE FROM subscription_periods', 'subscription_periods es un historial: solo se inserta (corrige con un asiento contrario)');
  UPDATE checkout_orders SET status = 'voided', decision_reason = 'error' WHERE id = o;   -- anular sí se puede
  PERFORM t.expect_err(format('UPDATE checkout_orders SET status = ''approved'' WHERE id = %L', o), 'checkout_orders: transición no permitida voided -> approved');
  -- rechazada es terminal
  o := t.order_(u, jsonb_build_array(t.item_new(pl)));
  UPDATE checkout_orders SET status = 'rejected' WHERE id = o;
  PERFORM t.expect_err(format('UPDATE checkout_orders SET status = ''approved'' WHERE id = %L', o), 'checkout_orders: transición no permitida rejected -> approved');
  -- no se borra una suscripción con periodos
  PERFORM t.expect_err('DELETE FROM subscriptions', 'update or delete on table "subscriptions" violates foreign key constraint "subscription_periods_subscription_id_fkey" on table "subscription_periods"');
END $$;
\echo '== 12. aprobación parcial: se divide la orden, atomicidad e idempotencia'
DO $$ DECLARE pl uuid; pl2 uuid; u uuid; p1 uuid; sid uuid; o uuid; r jsonb; r2 jsonb; child uuid; po checkout_orders%ROWTYPE; co checkout_orders%ROWTYPE;
BEGIN
  PERFORM t.reset(); pl := t.plat('Netflix'); pl2 := t.plat('HBO'); u := t.usr(1);
  p1 := t.prof(pl,'P1','occupied'); PERFORM t.prof(pl,'P2'); PERFORM t.prof(pl2,'H1');
  sid := t.sub(u, pl, p1, 'expired', timestamptz '2026-05-19 23:59:59-04');
  o := t.order_(u, jsonb_build_array(t.item_ren(sid, pl, 1), t.item_new(pl), t.item_new(pl2)), 1000, 'REF-P');
  UPDATE checkout_orders SET receipt_text = 'OCR texto', ocr_flags = '{"alertas":["x"]}' WHERE id = o;
  r := split_and_approve_checkout_order(o, 'admin:1', ARRAY[0,2], NULL, NULL);
  ASSERT (r->>'partial')::bool AND (r->>'ok')::bool;
  child := (r->>'remainder_order_id')::uuid;
  SELECT * INTO po FROM checkout_orders WHERE id = o; SELECT * INTO co FROM checkout_orders WHERE id = child;
  ASSERT po.status = 'approved' AND jsonb_array_length(po.manifest->'items') = 2 AND po.total_usd = 10 AND po.total_bs = 10000, 'original queda con lo aprobado';
  ASSERT po.manifest->>'total_usd' = '10.00' AND po.manifest->>'rate_used' = '1000.0000', 'manifiesto con el mismo formato que el de Python';
  ASSERT co.status = 'pending' AND co.parent_order_id = o AND jsonb_array_length(co.manifest->'items') = 1 AND co.total_usd = 5 AND co.total_bs = 5000, 'hija con el resto';
  ASSERT co.payment_reference IS NULL AND co.receipt_text = 'OCR texto' AND co.ocr_flags->'alertas'->>0 = 'x', 'hereda señales, no la referencia';
  ASSERT (SELECT count(*) FROM subscription_periods WHERE order_id = o) = 2 AND (SELECT count(*) FROM subscription_periods WHERE order_id = child) = 0;
  ASSERT (SELECT count(*) FROM subscriptions WHERE user_id = u) = 2, 'renovada + 1 nueva (HBO); la nueva de Netflix NO se aprobó';
  ASSERT (SELECT end_date FROM subscriptions WHERE id = sid) = timestamptz '2026-06-19 23:59:59-04';
  r2 := split_and_approve_checkout_order(o, 'admin:2', ARRAY[0], NULL, NULL);
  ASSERT (r2->>'already_applied')::bool;
  ASSERT (SELECT count(*) FROM checkout_orders) = 2, 'no divide otra vez';
  r2 := approve_checkout_order(child, 'admin:1', NULL, 'REF-HIJA');
  ASSERT (r2->>'ok')::bool AND (SELECT status FROM checkout_orders WHERE id = child) = 'approved', 'la hija se aprueba después';
  ASSERT (SELECT count(*) FROM subscriptions WHERE user_id = u) = 3;
END $$;
\echo '== 12b. selecciones inválidas y fallo atómico no dejan ninguna orden hija'
DO $$ DECLARE pl uuid; pl2 uuid; u uuid; o uuid; sel text; before_m jsonb; child uuid;
BEGIN
  PERFORM t.reset(); pl := t.plat('Netflix'); pl2 := t.plat('HBO'); u := t.usr(1); PERFORM t.prof(pl,'P1');
  o := t.order_(u, jsonb_build_array(t.item_new(pl), t.item_new(pl), t.item_new(pl2)));
  SELECT manifest INTO before_m FROM checkout_orders WHERE id = o;
  FOREACH sel IN ARRAY ARRAY['{}', '{0,0}', '{5}', '{-1}', '{0,1,2}'] LOOP
    PERFORM t.expect_err(format('SELECT split_and_approve_checkout_order(%L, ''a'', %L::int[], NULL, NULL)', o, sel), 'invalid_selection');
  END LOOP;
  PERFORM t.expect_err(format('SELECT split_and_approve_checkout_order(%L, ''a'', NULL, NULL, NULL)', o), 'invalid_selection');
  PERFORM t.expect_err(format('SELECT split_and_approve_checkout_order(%L, %L, ARRAY[0], NULL, NULL)', o, ''), 'actor_required');
  PERFORM t.expect_err(format('SELECT split_and_approve_checkout_order(%L, ''a'', ARRAY[2], NULL, NULL)', o), 'profile_unavailable');
  ASSERT (SELECT count(*) FROM checkout_orders) = 1 AND (SELECT status FROM checkout_orders WHERE id = o) = 'pending', 'ni división ni aprobación';
  ASSERT (SELECT manifest FROM checkout_orders WHERE id = o) = before_m, 'el manifiesto original no cambió';
  UPDATE checkout_orders SET status = 'expired' WHERE id = o;
  PERFORM t.expect_err(format('SELECT split_and_approve_checkout_order(%L, ''a'', ARRAY[0], NULL, NULL)', o), 'reason_required');
  PERFORM split_and_approve_checkout_order(o, 'a', ARRAY[0], 'comprobante tardío', NULL);
  child := (SELECT id FROM checkout_orders WHERE parent_order_id = o);
  UPDATE checkout_orders SET status = 'cancelled' WHERE id = child;
  PERFORM t.expect_err(format('SELECT split_and_approve_checkout_order(%L, ''a'', ARRAY[0], NULL, NULL)', child), 'invalid_state');
END $$;
\echo '== 13. anular una orden aprobada con asiento contrario'
DO $$ DECLARE pl uuid; u uuid; p1 uuid; sid uuid; o uuid; r jsonb; r2 jsonb; nsub uuid; nprof uuid; old_end timestamptz;
BEGIN
  PERFORM t.reset(); pl := t.plat('Netflix'); u := t.usr(1);
  p1 := t.prof(pl,'P1','occupied','1111'); nprof := t.prof(pl,'P2','available','2222');
  old_end := timestamptz '2026-05-19 23:59:59-04';
  sid := t.sub(u, pl, p1, 'expired', old_end);
  o := t.order_(u, jsonb_build_array(t.item_ren(sid, pl, 2), t.item_new(pl)), 1000, 'REF-V');
  PERFORM t.approve(o);
  ASSERT (SELECT end_date FROM subscriptions WHERE id = sid) = timestamptz '2026-07-19 23:59:59-04';
  nsub := (SELECT id FROM subscriptions WHERE user_id = u AND id <> sid);
  PERFORM t.expect_err(format('SELECT void_checkout_order(%L, ''a'', NULL)', o), 'reason_required');
  PERFORM t.expect_err(format('SELECT void_checkout_order(%L, %L, ''x'')', o, ''), 'actor_required');
  r := void_checkout_order(o, 'admin:1', 'aprobada por error');
  ASSERT (r->>'ok')::bool AND NOT (r->>'already_applied')::bool;
  ASSERT (SELECT end_date FROM subscriptions WHERE id = sid) = old_end, 'la renovación vuelve a su fecha anterior';
  ASSERT (SELECT status FROM subscriptions WHERE id = nsub) = 'voided' AND (SELECT void_reason FROM subscriptions WHERE id = nsub) = '[admin:1] aprobada por error';
  ASSERT (SELECT status FROM profiles WHERE id = nprof) = 'available' AND (SELECT pin FROM profiles WHERE id = nprof) <> '2222', 'perfil de la nueva liberado con PIN rotado';
  ASSERT (SELECT count(*) FROM subscription_periods WHERE order_id = o AND kind = 'reversa') = 2;
  ASSERT (SELECT sum(amount_usd) FROM subscription_periods WHERE order_id = o) = 0 AND (SELECT sum(amount_bs) FROM subscription_periods WHERE order_id = o) = 0, 'el asiento contrario cuadra a cero';
  ASSERT (SELECT status FROM checkout_orders WHERE id = o) = 'voided' AND (SELECT decision_reason FROM checkout_orders WHERE id = o) = 'aprobada por error';
  ASSERT (SELECT count(*) FROM subscriptions WHERE user_id = u) = 2, 'no se borró nada';
  r2 := void_checkout_order(o, 'admin:2', 'otra vez');
  ASSERT (r2->>'already_applied')::bool AND (SELECT count(*) FROM subscription_periods WHERE order_id = o) = 4, 'idempotente';
END $$;
\echo '== 13b. no se anula lo que no es reversible o ya cambió'
DO $$ DECLARE a uuid; b uuid; u uuid; pa uuid; s uuid; o uuid; o2 uuid; sid uuid; p1 uuid; e1 timestamptz;
BEGIN
  PERFORM t.reset(); a := t.plat('Netflix'); b := t.plat('HBO'); u := t.usr(1);
  pa := t.prof(a,'PA','occupied'); PERFORM t.prof(b,'PB','available');
  s := t.sub(u, a, pa, 'expired', timestamptz '2026-05-19 23:59:59-04');
  o := t.order_(u, jsonb_build_array(t.item_chg(s, b)), 1000, 'REF-C'); PERFORM t.approve(o);
  PERFORM t.expect_err(format('SELECT void_checkout_order(%L, ''a'', ''x'')', o), 'manual_reversal_required');
  ASSERT (SELECT status FROM checkout_orders WHERE id = o) = 'approved' AND (SELECT count(*) FROM subscription_periods WHERE kind = 'reversa') = 0;
  o2 := t.order_(u, jsonb_build_array(t.item_new(a)));
  PERFORM t.expect_err(format('SELECT void_checkout_order(%L, ''a'', ''x'')', o2), 'invalid_state');
  PERFORM t.expect_err('SELECT void_checkout_order(gen_random_uuid(), ''a'', ''x'')', 'order_not_found');
  PERFORM t.reset(); a := t.plat('Netflix'); u := t.usr(1); p1 := t.prof(a,'P1','occupied');
  sid := t.sub(u, a, p1, 'active', timestamptz '2026-12-10 23:59:59-04');
  o := t.order_(u, jsonb_build_array(t.item_ren(sid, a, 1)), 1000, 'R1'); PERFORM t.approve(o);
  o2 := t.order_(u, jsonb_build_array(t.item_ren(sid, a, 1)), 1000, 'R2'); PERFORM t.approve(o2);
  SELECT end_date INTO e1 FROM subscriptions WHERE id = sid;
  PERFORM t.expect_err(format('SELECT void_checkout_order(%L, ''a'', ''x'')', o), 'subscription_changed');
  ASSERT (SELECT end_date FROM subscriptions WHERE id = sid) = e1 AND (SELECT status FROM checkout_orders WHERE id = o) = 'approved';
  PERFORM void_checkout_order(o2, 'a', 'x'); PERFORM void_checkout_order(o, 'a', 'x');
  ASSERT (SELECT end_date FROM subscriptions WHERE id = sid) = timestamptz '2026-12-10 23:59:59-04', 'vuelve al inicio';
END $$;
\echo '== 13c. el historial de reversas es inmutable y la orden anulada es terminal'
DO $$ DECLARE pl uuid; u uuid; o uuid;
BEGIN
  PERFORM t.reset(); pl := t.plat('Netflix'); u := t.usr(1); PERFORM t.prof(pl,'P1');
  o := t.order_(u, jsonb_build_array(t.item_new(pl)), 1000, 'R-9'); PERFORM t.approve(o); PERFORM void_checkout_order(o, 'a', 'error');
  PERFORM t.expect_err('UPDATE subscription_periods SET amount_usd = 0', 'subscription_periods es un historial: solo se inserta (corrige con un asiento contrario)');
  PERFORM t.expect_err(format('UPDATE checkout_orders SET status = ''approved'' WHERE id = %L', o), 'checkout_orders: transición no permitida voided -> approved');
  PERFORM t.expect_err(format('SELECT approve_checkout_order(%L, ''a'', ''x'', NULL)', o), 'invalid_state');
END $$;
\echo 'OK bloque de escenarios'
