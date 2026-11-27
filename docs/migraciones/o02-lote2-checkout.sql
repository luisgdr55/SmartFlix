-- =============================================================================
-- O02 Lote 2 — Migración: órdenes de pago, historial de periodos y aprobación atómica
-- Proyecto: SmartFlixVE · Orden: docs/arquitecto/ordenes/o02-pagos-estados-checkout.md
-- Preparada el 2026-11-27 · NO EJECUTADA contra Supabase: la ejecuta el Orquestador (Luis)
-- en el editor SQL ANTES de desplegar el código que la use (el flag CHECKOUT_V2_ENABLED
-- sigue apagado, así que desplegar el código actual no depende de esta migración).
--
-- QUÉ HACE (solo agrega; no modifica ni borra datos existentes)
--   1. Tabla checkout_orders (orden de pago: un pago cubre N servicios).
--   2. Tabla subscription_periods (historial: solo se inserta, nunca se actualiza ni borra).
--   3. Columnas subscriptions.void_reason y subscriptions.voided_at (estado `voided`, aún sin uso).
--   4. Índices de consulta y el índice único de referencias de pago aprobadas.
--   5. Funciones: approve_checkout_order (aprobación atómica e idempotente) y dos auxiliares
--      internas (checkout_pick_profile, checkout_release_profile).
--   6. Seguridad: RLS activado SIN políticas en las tablas nuevas (solo el service role entra) y
--      EXECUTE de las funciones solo para service_role.
--
-- QUÉ NO HACE
--   * No agrega CHECK sobre subscriptions.status (hace falta antes el resultado del BLOQUE 0b del
--     script de diagnóstico: podría haber valores históricos fuera de catálogo).
--   * No toca perfiles, suscripciones ni clientes existentes. No convierte `expired` (decisión 2).
--   * No guarda el «día de aniversario» (decisión aprobada: se acepta la deriva de fin de mes).
--
-- CÓMO EJECUTARLA
--   1. Revisa el script completo (es tuyo aprobarlo). Está en UNA transacción: si algo falla, no
--      queda nada a medias. Es idempotente (IF NOT EXISTS / CREATE OR REPLACE / DROP TRIGGER IF
--      EXISTS): se puede volver a correr.
--   2. Ejecútalo en horario de baja actividad: CREATE INDEX sobre subscriptions toma un candado
--      de escritura breve (la tabla es pequeña).
--   3. Corre la sección «VERIFICACIÓN» del final (solo SELECT) y pega el resultado en el chat.
--   4. Reversa: sección «REVERSA» al final (comentada). Solo es seguro mientras las tablas nuevas
--      estén vacías o no se necesiten.
--
-- Requiere PostgreSQL 13+ (gen_random_uuid nativo; Supabase lo cumple). Los datos de Bs y USD son
-- NUMERIC; los instantes, TIMESTAMPTZ (UTC al guardar); fin de periodo = 23:59:59 America/Caracas.
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- 0. Precondición: las columnas que usan las funciones deben existir en producción.
--    (supabase_schema.sql del repo no declara profiles.reserved_for / reserved_until, que el código
--    sí usa; el BLOQUE 0 del script de diagnóstico ya lo verifica.) Si falta alguna, la migración
--    se detiene aquí y no cambia nada.
-- -----------------------------------------------------------------------------
DO $pre$
DECLARE v_missing TEXT;
BEGIN
    SELECT string_agg(r.t || '.' || r.c, ', ') INTO v_missing
    FROM (VALUES
        ('users','id'), ('platforms','id'), ('accounts','id'), ('accounts','email'),
        ('profiles','id'), ('profiles','platform_id'), ('profiles','account_id'), ('profiles','profile_name'),
        ('profiles','profile_type'), ('profiles','status'), ('profiles','pin'), ('profiles','last_released'),
        ('profiles','created_at'), ('profiles','reserved_for'), ('profiles','reserved_until'),
        ('subscriptions','id'), ('subscriptions','user_id'), ('subscriptions','platform_id'),
        ('subscriptions','profile_id'), ('subscriptions','plan_type'), ('subscriptions','start_date'),
        ('subscriptions','end_date'), ('subscriptions','price_usd'), ('subscriptions','price_bs'),
        ('subscriptions','rate_used'), ('subscriptions','status'), ('subscriptions','payment_reference'),
        ('subscriptions','payment_confirmed_at'), ('subscriptions','reminder_sent'),
        ('subscriptions','expiry_notified'), ('subscriptions','debt_reminder_count')
    ) AS r(t, c)
    WHERE NOT EXISTS (
        SELECT 1 FROM information_schema.columns ic
        WHERE ic.table_schema = 'public' AND ic.table_name = r.t AND ic.column_name = r.c);
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'Migración detenida: faltan columnas requeridas: %', v_missing;
    END IF;
END $pre$;

-- -----------------------------------------------------------------------------
-- 1. checkout_orders
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS checkout_orders (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status           VARCHAR(20) NOT NULL DEFAULT 'pending'
                     CONSTRAINT checkout_orders_status_chk
                     CHECK (status IN ('pending','approved','rejected','expired','cancelled','voided')),
    manifest         JSONB NOT NULL,
    total_usd        NUMERIC(10,2) NOT NULL CONSTRAINT checkout_orders_total_usd_chk CHECK (total_usd > 0),
    total_bs         NUMERIC(14,2),
    rate_used        NUMERIC(12,4),
    payment_reference VARCHAR(255),
    receipt_text     TEXT,
    ocr_flags        JSONB,
    expires_at       TIMESTAMPTZ,
    decided_by       TEXT,
    decided_at       TIMESTAMPTZ,
    decision_reason  TEXT,
    result           JSONB,           -- efectos aplicados al aprobar (para devolverlos en una 2.ª llamada)
    version          INT NOT NULL DEFAULT 1,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_checkout_orders_user_status ON checkout_orders (user_id, status);
CREATE INDEX IF NOT EXISTS idx_checkout_orders_status_expires ON checkout_orders (status, expires_at);
-- Una referencia bancaria aprobada solo puede usarse una vez (anti doble cobro).
CREATE UNIQUE INDEX IF NOT EXISTS uq_checkout_orders_approved_reference
    ON checkout_orders (payment_reference)
    WHERE status = 'approved' AND payment_reference IS NOT NULL;

-- La base refleja las reglas de la máquina de estados de la orden (services/checkout_service.py):
--   pending  -> approved | rejected | expired | cancelled
--   expired  -> approved           (comprobante tardío; el motivo lo exige la función)
--   approved -> voided             (anulación; el motivo lo exige la capa de aplicación)
--   rejected / cancelled / voided  son terminales. Una orden aprobada es inmutable.
CREATE OR REPLACE FUNCTION checkout_orders_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status = 'approved' THEN
            RAISE EXCEPTION 'checkout_orders: una orden aprobada no se borra (anúlala)'
                USING ERRCODE = 'P0001';
        END IF;
        RETURN OLD;
    END IF;

    IF NEW.status IS DISTINCT FROM OLD.status THEN
        IF NOT (
            (OLD.status = 'pending'  AND NEW.status IN ('approved','rejected','expired','cancelled')) OR
            (OLD.status = 'expired'  AND NEW.status = 'approved') OR
            (OLD.status = 'approved' AND NEW.status = 'voided')
        ) THEN
            RAISE EXCEPTION 'checkout_orders: transición no permitida % -> %', OLD.status, NEW.status
                USING ERRCODE = 'P0001';
        END IF;
    END IF;

    IF OLD.status IN ('approved','voided','rejected','cancelled') AND (
        NEW.manifest IS DISTINCT FROM OLD.manifest OR
        NEW.total_usd IS DISTINCT FROM OLD.total_usd OR
        NEW.total_bs IS DISTINCT FROM OLD.total_bs OR
        NEW.rate_used IS DISTINCT FROM OLD.rate_used OR
        NEW.user_id IS DISTINCT FROM OLD.user_id
    ) THEN
        RAISE EXCEPTION 'checkout_orders: la orden en estado % es inmutable', OLD.status
            USING ERRCODE = 'P0001';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_checkout_orders_guard ON checkout_orders;
CREATE TRIGGER trg_checkout_orders_guard
    BEFORE UPDATE OR DELETE ON checkout_orders
    FOR EACH ROW EXECUTE FUNCTION checkout_orders_guard();

-- -----------------------------------------------------------------------------
-- 2. subscription_periods (historial; solo INSERT)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS subscription_periods (
    id              BIGSERIAL PRIMARY KEY,
    subscription_id UUID NOT NULL REFERENCES subscriptions(id),   -- sin CASCADE: el historial protege la suscripción
    order_id        UUID REFERENCES checkout_orders(id),
    period_start    TIMESTAMPTZ NOT NULL,
    period_end      TIMESTAMPTZ NOT NULL,
    amount_usd      NUMERIC(10,2) NOT NULL,
    rate_used       NUMERIC(12,4),
    amount_bs       NUMERIC(14,2),
    kind            VARCHAR(12) NOT NULL
                    CONSTRAINT subscription_periods_kind_chk CHECK (kind IN ('renovacion','nueva','cambio')),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT subscription_periods_range_chk CHECK (period_end >= period_start)
);

CREATE INDEX IF NOT EXISTS idx_subscription_periods_sub ON subscription_periods (subscription_id, period_end);
-- Una orden aplica a una suscripción una sola vez (red de seguridad contra doble aplicación).
CREATE UNIQUE INDEX IF NOT EXISTS uq_subscription_periods_order_sub
    ON subscription_periods (order_id, subscription_id) WHERE order_id IS NOT NULL;

CREATE OR REPLACE FUNCTION subscription_periods_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'subscription_periods es un historial: solo se inserta (corrige con un asiento contrario)'
        USING ERRCODE = 'P0001';
END $$;

DROP TRIGGER IF EXISTS trg_subscription_periods_immutable ON subscription_periods;
CREATE TRIGGER trg_subscription_periods_immutable
    BEFORE UPDATE OR DELETE ON subscription_periods
    FOR EACH ROW EXECUTE FUNCTION subscription_periods_immutable();

-- -----------------------------------------------------------------------------
-- 3. subscriptions: anulación con motivo + índices de consulta
-- -----------------------------------------------------------------------------
ALTER TABLE subscriptions
    ADD COLUMN IF NOT EXISTS void_reason TEXT,
    ADD COLUMN IF NOT EXISTS voided_at   TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_subscriptions_user_platform_status ON subscriptions (user_id, platform_id, status);
CREATE INDEX IF NOT EXISTS idx_subscriptions_status_end_date      ON subscriptions (status, end_date);

-- -----------------------------------------------------------------------------
-- 4. Funciones auxiliares internas (no expuestas: ver sección 6)
-- -----------------------------------------------------------------------------

-- Elige y bloquea un perfil libre de la plataforma y tipo (mismo criterio que
-- database/profiles.py::get_available_profiles: `available`, o `reserved` con reserva vencida;
-- además un perfil reservado por el propio cliente). Orden: el liberado hace más tiempo primero.
-- Devuelve NULL si no hay.
CREATE OR REPLACE FUNCTION checkout_pick_profile(p_platform UUID, p_type TEXT, p_user UUID)
RETURNS UUID LANGUAGE plpgsql AS $$
DECLARE v_id UUID;
BEGIN
    SELECT p.id INTO v_id
    FROM profiles p
    WHERE p.platform_id = p_platform
      AND p.profile_type = p_type
      AND (
            p.status = 'available'
         OR (p.status = 'reserved' AND (p.reserved_until < now() OR p.reserved_for = p_user))
      )
    ORDER BY p.last_released ASC NULLS FIRST, p.created_at ASC
    LIMIT 1
    FOR UPDATE SKIP LOCKED;
    RETURN v_id;
END $$;

-- Libera un perfil como el corte (available + PIN nuevo de 4 dígitos + last_released + reserva
-- limpia), pero SOLO si está `occupied` y ninguna otra suscripción activa (distinta de
-- p_exclude_sub) lo usa. Devuelve {released, reason, profile_name, old_pin, new_pin, account_email}.
CREATE OR REPLACE FUNCTION checkout_release_profile(p_profile UUID, p_exclude_sub UUID)
RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE
    v_prof    profiles%ROWTYPE;
    v_other   INT;
    v_new_pin TEXT;
    v_email   TEXT;
BEGIN
    SELECT * INTO v_prof FROM profiles WHERE id = p_profile FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('released', false, 'reason', 'perfil_inexistente');
    END IF;
    SELECT a.email INTO v_email FROM accounts a WHERE a.id = v_prof.account_id;
    SELECT count(*) INTO v_other FROM subscriptions s
     WHERE s.profile_id = p_profile AND s.status = 'active' AND s.id <> p_exclude_sub;

    IF v_prof.status <> 'occupied' OR v_other > 0 THEN
        RETURN jsonb_build_object(
            'released', false,
            'reason', CASE WHEN v_other > 0 THEN 'otra_suscripcion_activa' ELSE 'no_ocupado' END,
            'profile_name', v_prof.profile_name, 'old_pin', v_prof.pin, 'account_email', v_email);
    END IF;

    v_new_pin := lpad(floor(random() * 10000)::int::text, 4, '0');
    UPDATE profiles
       SET status = 'available', pin = v_new_pin, last_released = now(),
           reserved_for = NULL, reserved_until = NULL
     WHERE id = p_profile;
    RETURN jsonb_build_object(
        'released', true, 'reason', NULL, 'profile_name', v_prof.profile_name,
        'old_pin', v_prof.pin, 'new_pin', v_new_pin, 'account_email', v_email);
END $$;

-- -----------------------------------------------------------------------------
-- 5. approve_checkout_order: aprobación atómica, idempotente y con candado de fila
-- -----------------------------------------------------------------------------
-- Llamada desde Python (Lote 3):  sb.rpc('approve_checkout_order',
--     {'p_order_id': ..., 'p_actor': 'admin:<id>', 'p_reason': None, 'p_payment_reference': None}).execute()
--
-- Reglas:
--   * Bloquea la orden (FOR UPDATE): dos aprobaciones simultáneas se serializan.
--   * Solo desde `pending`; desde `expired` exige p_reason (comprobante tardío). Si ya está
--     `approved`, NO hace nada y devuelve el resultado guardado con already_applied=true.
--   * Todo o nada: cualquier fallo lanza una excepción y revierte TODA la función. La orden queda
--     como estaba (pendiente). Códigos de error (campo MESSAGE de la excepción; DETAIL = JSON):
--       order_not_found, actor_required, reason_required, invalid_state, invalid_manifest,
--       subscription_not_found, subscription_not_owned, subscription_not_renewable,
--       subscription_not_overdue, profile_unavailable (no hay perfil libre: la orden sigue pendiente),
--       duplicate_reference (la referencia ya se aprobó en otra orden).
--   * Efectos por tipo de ítem (cada ítem apunta a una subscription_id; nunca usuario+plataforma):
--       renovacion: extiende desde el end_date ACTUAL de esa suscripción (siempre, incluso vencida):
--                   fin de día Caracas + N meses de calendario; status 'active'; reinicia
--                   reminder_sent, expiry_notified y debt_reminder_count (hallazgo N-B); comprueba el
--                   perfil (N-A): si es suyo lo conserva (si estaba libre, lo vuelve a ocupar); si lo
--                   usa otra suscripción activa o no está disponible, asigna otro del mismo tipo y
--                   lo reporta (profile_changed).
--       nueva:      crea la suscripción `active`, asigna un perfil libre, el periodo arranca en la
--                   aprobación (N-F): mensual = fin de día Caracas de hoy + 1 mes; express = ahora + 24 h.
--       cambio:     solo sobre una suscripción vencida: libera el perfil de la plataforma origen
--                   (con las guardas de checkout_release_profile), asigna otro en la destino, muta la
--                   misma subscription_id con fechas nuevas desde hoy.
--   * Inserta un renglón en subscription_periods por ítem y guarda el resultado en la orden.
--   * Los importes (subtotales, Bs por ítem, tasa) vienen calculados por Python con Decimal en el
--     manifiesto; aquí solo se comprueba que los subtotales sumen total_usd y los Bs sumen total_bs.
CREATE OR REPLACE FUNCTION approve_checkout_order(
    p_order_id          UUID,
    p_actor             TEXT,
    p_reason            TEXT DEFAULT NULL,
    p_payment_reference TEXT DEFAULT NULL
) RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE
    v_order    checkout_orders%ROWTYPE;
    v_item     jsonb;
    v_idx      INT;
    v_tipo     TEXT;
    v_sub      subscriptions%ROWTYPE;
    v_sub_id   UUID;
    v_periods  INT;
    v_usd      NUMERIC;
    v_bs       NUMERIC;
    v_rate     NUMERIC;
    v_base     DATE;
    v_old_end  TIMESTAMPTZ;
    v_new_end  TIMESTAMPTZ;
    v_new_start TIMESTAMPTZ;
    v_plan     TEXT;
    v_ptype    TEXT;
    v_platform UUID;
    v_prof     UUID;
    v_prof_row profiles%ROWTYPE;
    v_other    INT;
    v_changed  BOOLEAN;
    v_old_prof UUID;
    v_release  jsonb;
    v_ref      TEXT;
    v_today    DATE;
    v_sum_usd  NUMERIC := 0;
    v_sum_bs   NUMERIC := 0;
    v_results  jsonb := '[]'::jsonb;
    v_res      jsonb;
    v_final    jsonb;
    c_tz CONSTANT TEXT := 'America/Caracas';
BEGIN
    IF p_actor IS NULL OR btrim(p_actor) = '' THEN
        RAISE EXCEPTION 'actor_required' USING ERRCODE = 'P0001';
    END IF;

    SELECT * INTO v_order FROM checkout_orders WHERE id = p_order_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'order_not_found' USING ERRCODE = 'P0001', DETAIL = jsonb_build_object('order_id', p_order_id)::text;
    END IF;

    -- Idempotencia: una orden ya aprobada devuelve lo aplicado, sin efectos nuevos.
    IF v_order.status = 'approved' THEN
        RETURN coalesce(v_order.result, '{}'::jsonb)
               || jsonb_build_object('already_applied', true, 'order_id', v_order.id);
    END IF;
    IF v_order.status = 'expired' THEN
        IF p_reason IS NULL OR btrim(p_reason) = '' THEN
            RAISE EXCEPTION 'reason_required' USING ERRCODE = 'P0001',
                DETAIL = jsonb_build_object('order_id', p_order_id, 'status', v_order.status)::text;
        END IF;
    ELSIF v_order.status <> 'pending' THEN
        RAISE EXCEPTION 'invalid_state' USING ERRCODE = 'P0001',
            DETAIL = jsonb_build_object('order_id', p_order_id, 'status', v_order.status)::text;
    END IF;

    -- Manifiesto: forma y coherencia de totales.
    IF jsonb_typeof(v_order.manifest -> 'items') IS DISTINCT FROM 'array'
       OR jsonb_array_length(v_order.manifest -> 'items') = 0
       OR (v_order.manifest ->> 'version') IS DISTINCT FROM '1'
       OR v_order.rate_used IS NULL OR v_order.rate_used <= 0 OR v_order.total_bs IS NULL THEN
        RAISE EXCEPTION 'invalid_manifest' USING ERRCODE = 'P0001', DETAIL = jsonb_build_object('order_id', p_order_id)::text;
    END IF;
    FOR v_item IN SELECT * FROM jsonb_array_elements(v_order.manifest -> 'items') LOOP
        v_sum_usd := v_sum_usd + (v_item ->> 'subtotal_usd')::numeric;
        v_sum_bs  := v_sum_bs  + (v_item ->> 'amount_bs')::numeric;
    END LOOP;
    IF v_sum_usd <> v_order.total_usd OR v_sum_bs <> v_order.total_bs THEN
        RAISE EXCEPTION 'invalid_manifest' USING ERRCODE = 'P0001',
            DETAIL = jsonb_build_object('order_id', p_order_id, 'motivo', 'los importes no suman el total')::text;
    END IF;

    v_ref   := coalesce(nullif(btrim(p_payment_reference), ''), v_order.payment_reference);
    v_rate  := v_order.rate_used;
    v_today := (now() AT TIME ZONE c_tz)::date;

    -- Se procesa en orden estable (por subscription_id, las nuevas al final) para evitar
    -- interbloqueos entre dos órdenes que toquen las mismas suscripciones.
    FOR v_item, v_idx IN
        SELECT e.value, (e.ordinality - 1)::int
        FROM jsonb_array_elements(v_order.manifest -> 'items') WITH ORDINALITY AS e(value, ordinality)
        ORDER BY (e.value ->> 'subscription_id') NULLS LAST, e.ordinality
    LOOP
        v_tipo     := v_item ->> 'tipo';
        v_periods  := coalesce((v_item ->> 'periodos')::int, 1);
        v_usd      := (v_item ->> 'subtotal_usd')::numeric;
        v_bs       := (v_item ->> 'amount_bs')::numeric;
        v_plan     := coalesce(v_item ->> 'plan_type', 'monthly');
        v_platform := (v_item ->> 'platform_id')::uuid;
        v_changed  := false;
        v_old_prof := NULL;
        v_release  := NULL;
        v_prof     := NULL;
        v_prof_row := NULL;
        v_other    := 0;

        IF v_periods < 1 OR v_periods > 12 THEN
            RAISE EXCEPTION 'invalid_manifest' USING ERRCODE = 'P0001',
                DETAIL = jsonb_build_object('item', v_idx, 'motivo', 'periodos fuera de rango')::text;
        END IF;

        -- ── RENOVACIÓN ──────────────────────────────────────────────────────
        IF v_tipo = 'renovacion' THEN
            v_sub_id := (v_item ->> 'subscription_id')::uuid;
            SELECT * INTO v_sub FROM subscriptions WHERE id = v_sub_id FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'subscription_not_found' USING ERRCODE = 'P0001',
                    DETAIL = jsonb_build_object('item', v_idx, 'subscription_id', v_sub_id)::text;
            END IF;
            IF v_sub.user_id <> v_order.user_id THEN
                RAISE EXCEPTION 'subscription_not_owned' USING ERRCODE = 'P0001',
                    DETAIL = jsonb_build_object('item', v_idx, 'subscription_id', v_sub_id)::text;
            END IF;
            IF v_sub.status NOT IN ('active','expired') OR v_sub.plan_type <> 'monthly' THEN
                RAISE EXCEPTION 'subscription_not_renewable' USING ERRCODE = 'P0001',
                    DETAIL = jsonb_build_object('item', v_idx, 'subscription_id', v_sub_id, 'status', v_sub.status)::text;
            END IF;

            v_old_end := v_sub.end_date;
            v_base    := (v_old_end AT TIME ZONE c_tz)::date;
            v_new_end := ((v_base + make_interval(months => v_periods)) + time '23:59:59')::timestamp AT TIME ZONE c_tz;

            -- Perfil (N-A): ¿sigue siendo suyo?
            v_prof := v_sub.profile_id;
            IF v_prof IS NOT NULL THEN
                SELECT * INTO v_prof_row FROM profiles WHERE id = v_prof FOR UPDATE;
                SELECT count(*) INTO v_other FROM subscriptions s
                 WHERE s.profile_id = v_prof AND s.status = 'active' AND s.id <> v_sub_id;
                IF v_prof_row.id IS NULL OR v_other > 0 OR v_prof_row.status NOT IN ('occupied','available') THEN
                    v_old_prof := v_prof;
                    v_prof := NULL;
                END IF;
            END IF;
            IF v_prof IS NULL THEN
                v_ptype := coalesce(v_prof_row.profile_type, v_item ->> 'profile_type', 'monthly');
                v_prof := checkout_pick_profile(v_sub.platform_id, v_ptype, v_order.user_id);
                IF v_prof IS NULL THEN
                    RAISE EXCEPTION 'profile_unavailable' USING ERRCODE = 'P0001',
                        DETAIL = jsonb_build_object('item', v_idx, 'platform_id', v_sub.platform_id,
                                                    'subscription_id', v_sub_id)::text;
                END IF;
                v_changed := true;
            END IF;
            UPDATE profiles SET status = 'occupied', reserved_for = NULL, reserved_until = NULL WHERE id = v_prof;

            UPDATE subscriptions
               SET status = 'active', end_date = v_new_end, profile_id = v_prof,
                   payment_reference = v_ref, payment_confirmed_at = now(),
                   reminder_sent = false, expiry_notified = false, debt_reminder_count = 0
             WHERE id = v_sub_id;

            INSERT INTO subscription_periods
                (subscription_id, order_id, period_start, period_end, amount_usd, rate_used, amount_bs, kind)
            VALUES (v_sub_id, v_order.id, v_old_end, v_new_end, v_usd, v_rate, v_bs, 'renovacion');

            v_res := jsonb_build_object(
                'index', v_idx, 'tipo', v_tipo, 'subscription_id', v_sub_id, 'profile_id', v_prof,
                'profile_changed', v_changed, 'previous_profile_id', v_old_prof,
                'old_end_date', v_old_end, 'end_date', v_new_end, 'periodos', v_periods);

        -- ── NUEVA ───────────────────────────────────────────────────────────
        ELSIF v_tipo = 'nueva' THEN
            v_ptype := coalesce(v_item ->> 'profile_type', v_plan);
            v_prof  := checkout_pick_profile(v_platform, v_ptype, v_order.user_id);
            IF v_prof IS NULL THEN
                RAISE EXCEPTION 'profile_unavailable' USING ERRCODE = 'P0001',
                    DETAIL = jsonb_build_object('item', v_idx, 'platform_id', v_platform)::text;
            END IF;
            v_new_start := now();
            IF v_plan = 'express' THEN
                v_new_end := v_new_start + interval '24 hours';
            ELSE
                v_new_end := ((v_today + interval '1 month')::date + time '23:59:59')::timestamp AT TIME ZONE c_tz;
            END IF;
            UPDATE profiles SET status = 'occupied', reserved_for = NULL, reserved_until = NULL WHERE id = v_prof;

            INSERT INTO subscriptions
                (user_id, platform_id, profile_id, plan_type, start_date, end_date, price_usd, price_bs,
                 rate_used, status, payment_reference, payment_confirmed_at,
                 reminder_sent, expiry_notified, debt_reminder_count)
            VALUES
                (v_order.user_id, v_platform, v_prof, v_plan, v_new_start, v_new_end, v_usd, v_bs,
                 v_rate, 'active', v_ref, now(), false, false, 0)
            RETURNING id INTO v_sub_id;

            INSERT INTO subscription_periods
                (subscription_id, order_id, period_start, period_end, amount_usd, rate_used, amount_bs, kind)
            VALUES (v_sub_id, v_order.id, v_new_start, v_new_end, v_usd, v_rate, v_bs, 'nueva');

            v_res := jsonb_build_object(
                'index', v_idx, 'tipo', v_tipo, 'subscription_id', v_sub_id, 'profile_id', v_prof,
                'profile_changed', false, 'end_date', v_new_end, 'periodos', 1);

        -- ── CAMBIO DE PLATAFORMA ────────────────────────────────────────────
        ELSIF v_tipo = 'cambio' THEN
            v_sub_id := (v_item ->> 'subscription_id')::uuid;
            SELECT * INTO v_sub FROM subscriptions WHERE id = v_sub_id FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'subscription_not_found' USING ERRCODE = 'P0001',
                    DETAIL = jsonb_build_object('item', v_idx, 'subscription_id', v_sub_id)::text;
            END IF;
            IF v_sub.user_id <> v_order.user_id THEN
                RAISE EXCEPTION 'subscription_not_owned' USING ERRCODE = 'P0001',
                    DETAIL = jsonb_build_object('item', v_idx, 'subscription_id', v_sub_id)::text;
            END IF;
            IF v_sub.status NOT IN ('active','expired') OR v_sub.plan_type <> 'monthly' THEN
                RAISE EXCEPTION 'subscription_not_renewable' USING ERRCODE = 'P0001',
                    DETAIL = jsonb_build_object('item', v_idx, 'subscription_id', v_sub_id, 'status', v_sub.status)::text;
            END IF;
            IF v_sub.end_date >= now() THEN
                RAISE EXCEPTION 'subscription_not_overdue' USING ERRCODE = 'P0001',
                    DETAIL = jsonb_build_object('item', v_idx, 'subscription_id', v_sub_id)::text;
            END IF;

            v_ptype := coalesce(v_item ->> 'profile_type', 'monthly');
            v_prof  := checkout_pick_profile(v_platform, v_ptype, v_order.user_id);
            IF v_prof IS NULL THEN
                RAISE EXCEPTION 'profile_unavailable' USING ERRCODE = 'P0001',
                    DETAIL = jsonb_build_object('item', v_idx, 'platform_id', v_platform)::text;
            END IF;

            v_old_prof := v_sub.profile_id;
            IF v_old_prof IS NOT NULL THEN
                v_release := checkout_release_profile(v_old_prof, v_sub_id);
            END IF;

            v_new_start := now();
            v_new_end   := ((v_today + interval '1 month')::date + time '23:59:59')::timestamp AT TIME ZONE c_tz;
            UPDATE profiles SET status = 'occupied', reserved_for = NULL, reserved_until = NULL WHERE id = v_prof;

            UPDATE subscriptions
               SET platform_id = v_platform, plan_type = v_plan, profile_id = v_prof,
                   price_usd = (v_item ->> 'precio_unitario_usd')::numeric,
                   start_date = v_new_start, end_date = v_new_end, status = 'active',
                   payment_reference = v_ref, payment_confirmed_at = now(),
                   reminder_sent = false, expiry_notified = false, debt_reminder_count = 0
             WHERE id = v_sub_id;

            INSERT INTO subscription_periods
                (subscription_id, order_id, period_start, period_end, amount_usd, rate_used, amount_bs, kind)
            VALUES (v_sub_id, v_order.id, v_new_start, v_new_end, v_usd, v_rate, v_bs, 'cambio');

            v_res := jsonb_build_object(
                'index', v_idx, 'tipo', v_tipo, 'subscription_id', v_sub_id, 'profile_id', v_prof,
                'profile_changed', true, 'previous_profile_id', v_old_prof, 'released_profile', v_release,
                'end_date', v_new_end, 'periodos', 1);
        ELSE
            RAISE EXCEPTION 'invalid_manifest' USING ERRCODE = 'P0001',
                DETAIL = jsonb_build_object('item', v_idx, 'tipo', v_tipo)::text;
        END IF;

        v_results := v_results || jsonb_build_array(v_res);
    END LOOP;

    -- Los resultados se devuelven en el orden original del manifiesto.
    SELECT coalesce(jsonb_agg(r ORDER BY (r ->> 'index')::int), '[]'::jsonb)
      INTO v_results FROM jsonb_array_elements(v_results) AS r;

    v_final := jsonb_build_object('ok', true, 'already_applied', false, 'order_id', v_order.id,
                                  'approved_by', p_actor, 'items', v_results);
    BEGIN
        UPDATE checkout_orders
           SET status = 'approved', decided_by = btrim(p_actor), decided_at = now(),
               decision_reason = nullif(btrim(p_reason), ''), payment_reference = v_ref,
               result = v_final, version = version + 1, updated_at = now()
         WHERE id = v_order.id;
    EXCEPTION WHEN unique_violation THEN
        RAISE EXCEPTION 'duplicate_reference' USING ERRCODE = 'P0001',
            DETAIL = jsonb_build_object('order_id', p_order_id)::text;
    END;
    RETURN v_final;
END $$;

-- -----------------------------------------------------------------------------
-- 6. Seguridad
-- -----------------------------------------------------------------------------
-- El bot y el panel usan la service role key (se salta RLS): sin políticas, anon y authenticated
-- no ven ni escriben estas tablas aunque PostgREST las exponga.
ALTER TABLE checkout_orders      ENABLE ROW LEVEL SECURITY;
ALTER TABLE subscription_periods ENABLE ROW LEVEL SECURITY;

-- Las funciones solo las ejecuta service_role (en Supabase, por defecto también las ve anon).
REVOKE ALL ON FUNCTION approve_checkout_order(UUID, TEXT, TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION checkout_pick_profile(UUID, TEXT, UUID)        FROM PUBLIC;
REVOKE ALL ON FUNCTION checkout_release_profile(UUID, UUID)           FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON FUNCTION approve_checkout_order(UUID, TEXT, TEXT, TEXT) FROM anon;
        REVOKE ALL ON FUNCTION checkout_pick_profile(UUID, TEXT, UUID)        FROM anon;
        REVOKE ALL ON FUNCTION checkout_release_profile(UUID, UUID)           FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON FUNCTION approve_checkout_order(UUID, TEXT, TEXT, TEXT) FROM authenticated;
        REVOKE ALL ON FUNCTION checkout_pick_profile(UUID, TEXT, UUID)        FROM authenticated;
        REVOKE ALL ON FUNCTION checkout_release_profile(UUID, UUID)           FROM authenticated;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        GRANT EXECUTE ON FUNCTION approve_checkout_order(UUID, TEXT, TEXT, TEXT) TO service_role;
        GRANT EXECUTE ON FUNCTION checkout_pick_profile(UUID, TEXT, UUID)        TO service_role;
        GRANT EXECUTE ON FUNCTION checkout_release_profile(UUID, UUID)           TO service_role;
    END IF;
END $$;

COMMIT;

-- =============================================================================
-- VERIFICACIÓN (solo SELECT; ejecútala después y pega el resultado en el chat)
-- =============================================================================
-- Debe devolver 2 filas con rls_activo = true, 0 filas en cada tabla.
SELECT c.relname AS tabla, c.relrowsecurity AS rls_activo,
       CASE c.relname
         WHEN 'checkout_orders' THEN (SELECT count(*) FROM checkout_orders)
         ELSE (SELECT count(*) FROM subscription_periods) END AS filas
FROM pg_class c
WHERE c.relnamespace = 'public'::regnamespace
  AND c.relname IN ('checkout_orders', 'subscription_periods');

-- Debe devolver las 2 columnas nuevas.
SELECT column_name, data_type FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'subscriptions'
  AND column_name IN ('void_reason', 'voided_at') ORDER BY column_name;

-- Debe devolver 3 funciones; quien puede ejecutarlas: solo service_role y postgres/dueño
-- (anon y authenticated NO deben aparecer).
SELECT p.proname AS funcion,
       (SELECT string_agg(DISTINCT g.grantee::regrole::text, ', ' ORDER BY g.grantee::regrole::text)
          FROM aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) g
         WHERE g.privilege_type = 'EXECUTE') AS pueden_ejecutar
FROM pg_proc p
WHERE p.pronamespace = 'public'::regnamespace
  AND p.proname IN ('approve_checkout_order', 'checkout_pick_profile', 'checkout_release_profile');

-- Debe devolver 7 índices nuevos.
SELECT indexname FROM pg_indexes
WHERE schemaname = 'public'
  AND indexname IN ('idx_checkout_orders_user_status', 'idx_checkout_orders_status_expires',
                    'uq_checkout_orders_approved_reference', 'idx_subscription_periods_sub',
                    'uq_subscription_periods_order_sub', 'idx_subscriptions_user_platform_status',
                    'idx_subscriptions_status_end_date')
ORDER BY indexname;

-- =============================================================================
-- REVERSA (comentada). Solo mientras no se necesiten los datos de las tablas nuevas.
-- =============================================================================
-- BEGIN;
-- DROP FUNCTION IF EXISTS approve_checkout_order(UUID, TEXT, TEXT, TEXT);
-- DROP FUNCTION IF EXISTS checkout_release_profile(UUID, UUID);
-- DROP FUNCTION IF EXISTS checkout_pick_profile(UUID, TEXT, UUID);
-- DROP TABLE IF EXISTS subscription_periods;      -- (primero: referencia a checkout_orders)
-- DROP TABLE IF EXISTS checkout_orders;
-- DROP FUNCTION IF EXISTS subscription_periods_immutable();
-- DROP FUNCTION IF EXISTS checkout_orders_guard();
-- DROP INDEX IF EXISTS idx_subscriptions_user_platform_status;
-- DROP INDEX IF EXISTS idx_subscriptions_status_end_date;
-- ALTER TABLE subscriptions DROP COLUMN IF EXISTS void_reason, DROP COLUMN IF EXISTS voided_at;
-- COMMIT;
