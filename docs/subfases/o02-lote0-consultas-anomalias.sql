-- =============================================================================
-- O02 Lote 0 — Consultas de anomalías (SOLO LECTURA)
-- Proyecto: SmartFlixVE · Orden: docs/arquitecto/ordenes/o02-pagos-estados-checkout.md
-- Preparado el 2026-11-27 · NO ejecutado por el Arquitecto: lo ejecuta el Orquestador (Luis).
--
-- QUÉ ES
--   Diagnóstico para saber cuántos datos reales están afectados por los problemas
--   que documenta docs/subfases/o02-lote0-mapa-de-estados.md. No corrige nada.
--
-- GARANTÍAS
--   * Solo contiene SELECT / WITH ... SELECT sobre tablas públicas y catálogos
--     (information_schema, pg_constraint). No hay INSERT, UPDATE, DELETE, DDL,
--     funciones con efectos ni COPY. Revísalo tú mismo antes de ejecutar:
--     busca las palabras INSERT, UPDATE, DELETE, ALTER, DROP, CREATE, TRUNCATE.
--   * Ninguna consulta devuelve nombres, correos, teléfonos, usernames ni
--     credenciales. Las consultas de detalle solo muestran los primeros 8
--     caracteres de un UUID, plataforma, estado y fechas.
--
-- CÓMO EJECUTARLO (Supabase → SQL Editor)
--   1. El editor de Supabase suele mostrar SOLO el resultado de la última
--      sentencia. Ejecuta CADA BLOQUE por separado: selecciona el bloque (desde su
--      comentario «BLOQUE n» hasta su «;» final) y pulsa Run (Ctrl/Cmd+Enter).
--   2. Orden: BLOQUE 0 primero. Si algún bloque da «column ... does not exist», NO
--      lo arregles tú: copia solo el mensaje de error en el chat (no contiene
--      datos) y se ajusta la consulta.
--   3. Lo que puedes pegar en el chat: el resultado del BLOQUE 0 y del BLOQUE 9
--      (RESUMEN; son solo conteos). Los bloques 1 a 8 son de detalle: NO los
--      pegues enteros; sirven para que mires tú y decidas.
--   4. Opcional (más seguro todavía): ejecútalo con un rol de solo lectura
--      (por ejemplo, el rol «postgres» con la conexión de solo lectura del
--      proyecto, si la tienes). No es necesario para que el script sea inocuo.
--
-- CONVENCIONES
--   «mora»  = end_date < now() y status IN ('active','expired') (predicado is_overdue
--             que propone el Lote 6 de la orden).
--   Días de mora = floor((now() - end_date) en días).
--   Hora local = America/Caracas.
-- =============================================================================


-- =============================================================================
-- BLOQUE 0 — Verificación de esquema (information_schema) · PEGABLE EN EL CHAT
--   Comprueba que existen las columnas que usan las consultas de abajo y que el
--   tipo de las fechas es timestamptz. supabase_schema.sql del repo está
--   DESACTUALIZADO: no incluye las columnas marcadas «dudosa en repo».
--   Lee la columna «existe»: debe ser true en todas. Las «false» se reportan.
-- =============================================================================
WITH expected(table_name, column_name, nota) AS (
  VALUES
    ('subscriptions','id',''),
    ('subscriptions','user_id',''),
    ('subscriptions','platform_id',''),
    ('subscriptions','profile_id',''),
    ('subscriptions','plan_type',''),
    ('subscriptions','status',''),
    ('subscriptions','start_date',''),
    ('subscriptions','end_date',''),
    ('subscriptions','price_usd',''),
    ('subscriptions','price_bs',''),
    ('subscriptions','rate_used',''),
    ('subscriptions','payment_reference',''),
    ('subscriptions','payment_confirmed_at',''),
    ('subscriptions','reminder_sent',''),
    ('subscriptions','expiry_notified',''),
    ('subscriptions','debt_reminder_count',''),
    ('subscriptions','created_at',''),
    ('profiles','id',''),
    ('profiles','platform_id',''),
    ('profiles','account_id',''),
    ('profiles','profile_type',''),
    ('profiles','status',''),
    ('profiles','last_released',''),
    ('profiles','reserved_for','dudosa en repo (no está en supabase_schema.sql; el código la usa)'),
    ('profiles','reserved_until','dudosa en repo (no está en supabase_schema.sql; el código la usa)'),
    ('profiles','is_extra_member','dudosa en repo (no está en supabase_schema.sql; el código la usa)'),
    ('platforms','id',''),
    ('platforms','name','')
)
SELECT e.table_name,
       e.column_name,
       (c.column_name IS NOT NULL) AS existe,
       c.data_type,
       e.nota
FROM expected e
LEFT JOIN information_schema.columns c
       ON c.table_schema = 'public'
      AND c.table_name   = e.table_name
      AND c.column_name  = e.column_name
ORDER BY existe, e.table_name, e.column_name;


-- BLOQUE 0b — ¿Hay restricciones CHECK sobre subscriptions (p. ej. valores permitidos de status)?
--   Importa para definir el estado nuevo `voided` sin romper nada. Pegable (sin datos).
SELECT conname AS restriccion,
       contype AS tipo,
       pg_get_constraintdef(oid) AS definicion
FROM pg_constraint
WHERE conrelid = 'public.subscriptions'::regclass
ORDER BY contype, conname;


-- BLOQUE 0c — ¿Ya existen las tablas nuevas de la orden? (evita choques en el Lote 2). Pegable.
SELECT to_regclass('public.checkout_orders')       AS checkout_orders_existe,
       to_regclass('public.subscription_periods')  AS subscription_periods_existe;


-- =============================================================================
-- BLOQUE 1 — Duplicados por usuario + plataforma (activas / vencidas)
--   Detecta: clientes con más de una suscripción `active`/`expired` en la misma
--   plataforma. Puede ser LEGÍTIMO (dos perfiles de Netflix pagados a la vez) o
--   un duplicado creado por la renovación que crea suscripción nueva.
--   Importa: el motor actual elige la renovación por usuario+plataforma
--   (get_user_platform_active_subscription, limit 1) y puede renovar la equivocada.
--   Cómo leerlo: `mismo_perfil = true` es duplicado casi seguro (dos filas para el
--   mismo perfil); `perfiles_distintos` > 1 suele ser legítimo.
-- =============================================================================
SELECT pl.name                                        AS plataforma,
       left(s.user_id::text, 8)                       AS usuario_8,
       count(*)                                       AS filas,
       count(DISTINCT s.profile_id)                   AS perfiles_distintos,
       (count(*) > count(DISTINCT s.profile_id)
        AND count(s.profile_id) = count(*))           AS mismo_perfil,
       string_agg(s.status || '/' || s.plan_type, ', ' ORDER BY s.end_date DESC) AS estados,
       max(s.end_date)                                AS fin_mas_reciente
FROM subscriptions s
JOIN platforms pl ON pl.id = s.platform_id
WHERE s.status IN ('active', 'expired')
GROUP BY pl.name, s.user_id, s.platform_id
HAVING count(*) > 1
ORDER BY mismo_perfil DESC, filas DESC, plataforma;


-- =============================================================================
-- BLOQUE 2 — Suscripciones `expired_payment` y su antigüedad
--   Detecta: pedidos sin pagar que el job de limpieza (cada 4 h, corte a las 4 h)
--   dejó en un estado sin salida. `con_comprobante` = el cliente SÍ subió un
--   comprobante pero nadie lo aprobó a tiempo (dinero potencialmente cobrado sin
--   servicio). Importa: el bot los cuenta como deuda y el formulario de edición
--   del panel los activaría sin pago.
-- =============================================================================
SELECT pl.name                                          AS plataforma,
       s.plan_type,
       left(s.id::text, 8)                              AS sub_8,
       s.created_at,
       floor(extract(epoch FROM (now() - s.created_at)) / 86400)::int AS antiguedad_dias,
       (s.payment_reference IS NOT NULL
        AND btrim(s.payment_reference) <> '')           AS con_comprobante,
       (s.profile_id IS NOT NULL)                       AS tiene_perfil
FROM subscriptions s
JOIN platforms pl ON pl.id = s.platform_id
WHERE s.status = 'expired_payment'
ORDER BY s.created_at;


-- =============================================================================
-- BLOQUE 3 — Suscripciones vencidas con el perfil todavía ocupado y días de mora
--   Detecta: status `active` o `expired` con end_date < now() cuyo perfil sigue
--   `occupied`. Es la mora real que hoy nadie corta (el corte D+7 casi nunca
--   corre) y las `expired` que NO liberaron el perfil (cancel_and_buy, auto_expire,
--   corte suave, carrera con job_express_release). `dias_mora` en días enteros.
--   `debt_reminder_count` muestra si los recordatorios D+1..D+6 llegaron a salir.
-- =============================================================================
SELECT pl.name                                           AS plataforma,
       s.plan_type,
       s.status,
       left(s.id::text, 8)                               AS sub_8,
       s.end_date,
       floor(extract(epoch FROM (now() - s.end_date)) / 86400)::int AS dias_mora,
       s.debt_reminder_count,
       s.expiry_notified,
       p.status                                          AS estado_perfil
FROM subscriptions s
JOIN platforms pl ON pl.id = s.platform_id
JOIN profiles  p  ON p.id  = s.profile_id
WHERE s.status IN ('active', 'expired')
  AND s.end_date < now()
  AND p.status = 'occupied'
ORDER BY dias_mora DESC, plataforma;


-- =============================================================================
-- BLOQUE 4a — Perfiles `occupied` SIN ninguna suscripción activa que los use
--   Detecta fuga de perfiles (stock «perdido»). Se clasifican:
--     * solo_vencida_expired   → hay una `expired` que lo apunta (mora o corte suave):
--                                no es huérfano real; es mora heredada.
--     * solo_cancelada         → solo `cancelled` lo apunta: fuga (se canceló sin liberar).
--     * ninguna_suscripcion    → nadie lo apunta: fuga (cliente/suscripción borrados
--                                sin liberar, o perfil ocupado manualmente).
--   Ojo: un perfil puede estar `occupied` a propósito (uso del admin, mantenimiento
--   manual): revisa los casos antes de concluir.
-- =============================================================================
SELECT pl.name                                           AS plataforma,
       p.profile_type,
       left(p.id::text, 8)                               AS perfil_8,
       CASE
         WHEN EXISTS (SELECT 1 FROM subscriptions s2
                      WHERE s2.profile_id = p.id AND s2.status = 'expired')
           THEN 'solo_vencida_expired'
         WHEN EXISTS (SELECT 1 FROM subscriptions s3
                      WHERE s3.profile_id = p.id AND s3.status = 'cancelled')
           THEN 'solo_cancelada'
         ELSE 'ninguna_suscripcion'
       END                                               AS clasificacion,
       p.last_released
FROM profiles p
JOIN platforms pl ON pl.id = p.platform_id
WHERE p.status = 'occupied'
  AND NOT EXISTS (SELECT 1 FROM subscriptions s
                  WHERE s.profile_id = p.id AND s.status = 'active')
ORDER BY clasificacion, plataforma;


-- BLOQUE 4b — Perfiles asignados a MÁS DE UNA suscripción activa
--   Detecta: dos clientes con el mismo perfil (p. ej. renovar una `cancelled` cuyo
--   perfil ya se había entregado a otro). Importa: es el riesgo más grave para el
--   cliente (comparten credenciales/PIN). Debe devolver 0 filas en un sistema sano.
SELECT pl.name                                           AS plataforma,
       left(p.id::text, 8)                               AS perfil_8,
       p.status                                          AS estado_perfil,
       count(*)                                          AS suscripciones_activas,
       count(DISTINCT s.user_id)                         AS clientes_distintos
FROM subscriptions s
JOIN profiles  p  ON p.id  = s.profile_id
JOIN platforms pl ON pl.id = p.platform_id
WHERE s.status = 'active'
GROUP BY pl.name, p.id, p.status
HAVING count(*) > 1
ORDER BY clientes_distintos DESC, suscripciones_activas DESC;


-- BLOQUE 4c — Suscripciones activas cuyo perfil figura `available` (libre)
--   Detecta: perfil liberado pero con suscripción activa → puede entregarse a un
--   tercero (doble asignación futura). Debe devolver 0 filas.
SELECT pl.name                                           AS plataforma,
       s.plan_type,
       left(s.id::text, 8)                               AS sub_8,
       left(p.id::text, 8)                               AS perfil_8,
       s.end_date,
       p.status                                          AS estado_perfil
FROM subscriptions s
JOIN profiles  p  ON p.id  = s.profile_id
JOIN platforms pl ON pl.id = s.platform_id
WHERE s.status = 'active'
  AND p.status = 'available'
ORDER BY s.end_date;


-- BLOQUE 4d — Perfiles `reserved` con reserva ya vencida [columna dudosa: reserved_until]
--   Detecta: reservas que el job (cada 10 min) debió liberar. Si el BLOQUE 0 dijo que
--   reserved_until no existe, NO ejecutes este bloque.
SELECT pl.name                                           AS plataforma,
       left(p.id::text, 8)                               AS perfil_8,
       p.reserved_until,
       floor(extract(epoch FROM (now() - p.reserved_until)) / 3600)::int AS horas_vencida
FROM profiles p
JOIN platforms pl ON pl.id = p.platform_id
WHERE p.status = 'reserved'
  AND p.reserved_until < now()
ORDER BY p.reserved_until;


-- =============================================================================
-- BLOQUE 5 — `pending_payment` huérfanos
--   Detecta: pedidos pendientes que el job de limpieza (4 h) debió pasar a
--   `expired_payment`, pendientes sin comprobante, con perfil asignado (no debería)
--   o que duplican una suscripción vigente del mismo cliente y plataforma
--   (renovación pendiente). Importa: el admin los ve como «pagos pendientes» y
--   pueden aprobarse dos veces / sobre la suscripción equivocada.
-- =============================================================================
SELECT pl.name                                           AS plataforma,
       s.plan_type,
       left(s.id::text, 8)                               AS sub_8,
       s.created_at,
       round(extract(epoch FROM (now() - s.created_at)) / 3600)::int AS antiguedad_horas,
       (s.payment_reference IS NOT NULL
        AND btrim(s.payment_reference) <> '')            AS con_comprobante,
       (s.profile_id IS NOT NULL)                        AS con_perfil,
       EXISTS (SELECT 1 FROM subscriptions v
               WHERE v.user_id = s.user_id
                 AND v.platform_id = s.platform_id
                 AND v.id <> s.id
                 AND v.status IN ('active', 'expired'))  AS duplica_suscripcion_vigente
FROM subscriptions s
JOIN platforms pl ON pl.id = s.platform_id
WHERE s.status = 'pending_payment'
ORDER BY s.created_at;


-- =============================================================================
-- BLOQUE 6 — Recuento por estado y plataforma (conteos agregados) · PEGABLE EN EL CHAT
--   Ver la foto completa: cuántas filas hay en cada estado, por plataforma y plan.
-- =============================================================================
SELECT s.status,
       pl.name        AS plataforma,
       s.plan_type,
       count(*)       AS filas
FROM subscriptions s
JOIN platforms pl ON pl.id = s.platform_id
GROUP BY s.status, pl.name, s.plan_type
ORDER BY s.status, pl.name, s.plan_type;


-- =============================================================================
-- BLOQUE 7 — Deriva de fechas: fin de periodo con hora distinta de 23:59:59 (Caracas)
--   Detecta: suscripciones MENSUALES activas/vencidas cuyo `end_date` no termina a
--   las 23:59:59 hora de Caracas, y cuántas tienen un día UTC distinto del día en
--   Caracas (el código recorta `end_date[:10]` sobre el texto UTC que devuelve
--   Supabase: cuando esas dos fechas difieren, cada renovación puede sumar un día).
--   Nota: las compras NUEVAS por el bot fijan end_date = creación + 30 días, con la
--   hora del pedido: son «otra_hora» de forma normal. Lo que alarma es la mezcla
--   con las 23:59:59 y la columna `dia_utc_distinto`.
-- =============================================================================
SELECT (s.end_date AT TIME ZONE 'America/Caracas')::time          AS hora_local_fin,
       ((s.end_date AT TIME ZONE 'America/Caracas')::time
          = TIME '23:59:59')                                        AS es_23_59_59,
       ((s.end_date AT TIME ZONE 'UTC')::date
          <> (s.end_date AT TIME ZONE 'America/Caracas')::date)     AS dia_utc_distinto,
       count(*)                                                     AS filas
FROM subscriptions s
WHERE s.status IN ('active', 'expired')
  AND s.plan_type = 'monthly'
GROUP BY 1, 2, 3
ORDER BY filas DESC
LIMIT 40;


-- =============================================================================
-- BLOQUE 8 — Referencias de pago repetidas (anti-duplicado)
--   Detecta: una misma referencia bancaria usada en más de una suscripción
--   (se excluyen las marcas internas MANUAL-*, PANEL-MANUAL y MIGRADO). Importa:
--   el índice único de la orden se crea sobre checkout_orders, no sobre las
--   suscripciones viejas; esto dice si hay repetidas históricas. Solo cuenta y
--   muestra los 6 primeros caracteres de la referencia (no pegar completo).
-- =============================================================================
SELECT left(s.payment_reference, 6) || '…'        AS referencia_inicio,
       count(*)                                    AS filas,
       count(DISTINCT s.user_id)                   AS clientes_distintos
FROM subscriptions s
WHERE s.payment_reference IS NOT NULL
  AND btrim(s.payment_reference) <> ''
  AND s.payment_reference NOT LIKE 'MANUAL-%'
  AND s.payment_reference NOT IN ('PANEL-MANUAL', 'MIGRADO')
GROUP BY s.payment_reference
HAVING count(*) > 1
ORDER BY filas DESC
LIMIT 25;


-- =============================================================================
-- BLOQUE 9 — RESUMEN SOLO CON CONTEOS AGREGADOS (pegable en el chat)
--   Una sola tabla (metrica, valor). No contiene nombres, correos, teléfonos ni
--   identificadores. Es lo único que necesito para el Lote 0 → Lote 7.
--   Ejecuta este bloque completo (desde «SELECT * FROM (» hasta «ORDER BY metrica;»).
--   Si una métrica agrupada (estado.*, mora.<estado>.*, perfiles.total_*) NO aparece en la
--   tabla, significa que su valor es 0 (no había filas que agrupar).
--   Sintaxis comprobada por el Arquitecto en un PostgreSQL 16 local desechable con tablas
--   vacías de prueba y datos ficticios (no contra Supabase ni producción); el comportamiento
--   sobre tus datos reales NO está verificado.
--   Si falla por la columna `reserved_until`, no la usa: este bloque solo usa
--   columnas del núcleo (verificadas en el BLOQUE 0).
-- =============================================================================
SELECT * FROM (

  -- Foto por estado
  SELECT 'estado.' || s.status AS metrica, count(*)::bigint AS valor
  FROM subscriptions s GROUP BY s.status

  UNION ALL
  -- (1) Duplicados usuario+plataforma entre activas/vencidas
  SELECT 'dup.grupos_usuario_plataforma_con_2mas', count(*)::bigint FROM (
    SELECT 1 FROM subscriptions s WHERE s.status IN ('active','expired')
    GROUP BY s.user_id, s.platform_id HAVING count(*) > 1) g
  UNION ALL
  SELECT 'dup.grupos_con_el_mismo_perfil', count(*)::bigint FROM (
    SELECT 1 FROM subscriptions s WHERE s.status IN ('active','expired') AND s.profile_id IS NOT NULL
    GROUP BY s.user_id, s.platform_id, s.profile_id HAVING count(*) > 1) g

  UNION ALL
  -- (2) expired_payment
  SELECT 'exp_pago.total', count(*)::bigint FROM subscriptions s WHERE s.status = 'expired_payment'
  UNION ALL
  SELECT 'exp_pago.con_comprobante', count(*)::bigint FROM subscriptions s
   WHERE s.status = 'expired_payment' AND s.payment_reference IS NOT NULL AND btrim(s.payment_reference) <> ''
  UNION ALL
  SELECT 'exp_pago.edad_0_a_1_dias', count(*)::bigint FROM subscriptions s
   WHERE s.status = 'expired_payment' AND s.created_at >= now() - interval '2 days'
  UNION ALL
  SELECT 'exp_pago.edad_2_a_7_dias', count(*)::bigint FROM subscriptions s
   WHERE s.status = 'expired_payment' AND s.created_at < now() - interval '2 days' AND s.created_at >= now() - interval '8 days'
  UNION ALL
  SELECT 'exp_pago.edad_8_a_30_dias', count(*)::bigint FROM subscriptions s
   WHERE s.status = 'expired_payment' AND s.created_at < now() - interval '8 days' AND s.created_at >= now() - interval '31 days'
  UNION ALL
  SELECT 'exp_pago.edad_mas_de_30_dias', count(*)::bigint FROM subscriptions s
   WHERE s.status = 'expired_payment' AND s.created_at < now() - interval '31 days'

  UNION ALL
  -- (3) Mora (active/expired con end_date < ahora) con perfil ocupado
  SELECT 'mora.' || s.status || '.perfil_ocupado', count(*)::bigint
  FROM subscriptions s JOIN profiles p ON p.id = s.profile_id
  WHERE s.status IN ('active','expired') AND s.end_date < now() AND p.status = 'occupied'
  GROUP BY s.status
  UNION ALL
  SELECT 'mora.' || s.status || '.perfil_libre_o_otro_estado', count(*)::bigint
  FROM subscriptions s JOIN profiles p ON p.id = s.profile_id
  WHERE s.status IN ('active','expired') AND s.end_date < now() AND p.status <> 'occupied'
  GROUP BY s.status
  UNION ALL
  SELECT 'mora.' || s.status || '.sin_perfil_asignado', count(*)::bigint
  FROM subscriptions s
  WHERE s.status IN ('active','expired') AND s.end_date < now() AND s.profile_id IS NULL
  GROUP BY s.status
  UNION ALL
  SELECT 'mora.mensual.dias_0', count(*)::bigint FROM subscriptions s
   WHERE s.status IN ('active','expired') AND s.plan_type = 'monthly'
     AND s.end_date < now() AND s.end_date >= now() - interval '1 day'
  UNION ALL
  SELECT 'mora.mensual.dias_1_a_6', count(*)::bigint FROM subscriptions s
   WHERE s.status IN ('active','expired') AND s.plan_type = 'monthly'
     AND s.end_date < now() - interval '1 day' AND s.end_date >= now() - interval '7 days'
  UNION ALL
  SELECT 'mora.mensual.dias_7_a_13', count(*)::bigint FROM subscriptions s
   WHERE s.status IN ('active','expired') AND s.plan_type = 'monthly'
     AND s.end_date < now() - interval '7 days' AND s.end_date >= now() - interval '14 days'
  UNION ALL
  SELECT 'mora.mensual.dias_14_a_29', count(*)::bigint FROM subscriptions s
   WHERE s.status IN ('active','expired') AND s.plan_type = 'monthly'
     AND s.end_date < now() - interval '14 days' AND s.end_date >= now() - interval '30 days'
  UNION ALL
  SELECT 'mora.mensual.dias_30_o_mas', count(*)::bigint FROM subscriptions s
   WHERE s.status IN ('active','expired') AND s.plan_type = 'monthly'
     AND s.end_date < now() - interval '30 days'
  UNION ALL
  -- ¿Llegaron a salir los recordatorios de deuda? (hipótesis: el contador casi no avanza)
  SELECT 'mora.mensual.recordatorios_deuda_0', count(*)::bigint FROM subscriptions s
   WHERE s.status IN ('active','expired') AND s.plan_type = 'monthly' AND s.end_date < now()
     AND coalesce(s.debt_reminder_count, 0) = 0
  UNION ALL
  SELECT 'mora.mensual.recordatorios_deuda_1_a_5', count(*)::bigint FROM subscriptions s
   WHERE s.status IN ('active','expired') AND s.plan_type = 'monthly' AND s.end_date < now()
     AND s.debt_reminder_count BETWEEN 1 AND 5
  UNION ALL
  SELECT 'mora.mensual.recordatorios_deuda_6_o_mas', count(*)::bigint FROM subscriptions s
   WHERE s.status IN ('active','expired') AND s.plan_type = 'monthly' AND s.end_date < now()
     AND s.debt_reminder_count >= 6
  UNION ALL
  -- Aviso D+0: de las vencidas mensuales, cuántas nunca recibieron el aviso
  SELECT 'mora.mensual.sin_aviso_d0', count(*)::bigint FROM subscriptions s
   WHERE s.status IN ('active','expired') AND s.plan_type = 'monthly' AND s.end_date < now()
     AND coalesce(s.expiry_notified, false) = false
  UNION ALL
  -- Express vencidas con perfil aún ocupado (carrera con auto_expire)
  SELECT 'express.vencida_perfil_ocupado', count(*)::bigint
  FROM subscriptions s JOIN profiles p ON p.id = s.profile_id
  WHERE s.plan_type = 'express' AND s.status IN ('active','expired')
    AND s.end_date < now() AND p.status = 'occupied'

  UNION ALL
  -- (4) Perfiles
  SELECT 'perfiles.total_' || p.status, count(*)::bigint FROM profiles p GROUP BY p.status
  UNION ALL
  SELECT 'perfiles.occupied_sin_sub_activa.con_sub_expired', count(*)::bigint FROM profiles p
   WHERE p.status = 'occupied'
     AND NOT EXISTS (SELECT 1 FROM subscriptions s WHERE s.profile_id = p.id AND s.status = 'active')
     AND EXISTS (SELECT 1 FROM subscriptions s WHERE s.profile_id = p.id AND s.status = 'expired')
  UNION ALL
  SELECT 'perfiles.occupied_sin_sub_activa.solo_cancelada', count(*)::bigint FROM profiles p
   WHERE p.status = 'occupied'
     AND NOT EXISTS (SELECT 1 FROM subscriptions s WHERE s.profile_id = p.id AND s.status IN ('active','expired'))
     AND EXISTS (SELECT 1 FROM subscriptions s WHERE s.profile_id = p.id AND s.status = 'cancelled')
  UNION ALL
  SELECT 'perfiles.occupied_sin_ninguna_suscripcion', count(*)::bigint FROM profiles p
   WHERE p.status = 'occupied'
     AND NOT EXISTS (SELECT 1 FROM subscriptions s WHERE s.profile_id = p.id AND s.status IN ('active','expired','cancelled'))
  UNION ALL
  SELECT 'perfiles.con_2_o_mas_suscripciones_activas', count(*)::bigint FROM (
    SELECT 1 FROM subscriptions s WHERE s.status = 'active' AND s.profile_id IS NOT NULL
    GROUP BY s.profile_id HAVING count(*) > 1) g
  UNION ALL
  SELECT 'perfiles.available_con_suscripcion_activa', count(DISTINCT p.id)::bigint
  FROM profiles p JOIN subscriptions s ON s.profile_id = p.id AND s.status = 'active'
  WHERE p.status = 'available'

  UNION ALL
  -- (5) pending_payment
  SELECT 'pendiente.total', count(*)::bigint FROM subscriptions s WHERE s.status = 'pending_payment'
  UNION ALL
  SELECT 'pendiente.mas_de_4h', count(*)::bigint FROM subscriptions s
   WHERE s.status = 'pending_payment' AND s.created_at < now() - interval '4 hours'
  UNION ALL
  SELECT 'pendiente.mas_de_24h', count(*)::bigint FROM subscriptions s
   WHERE s.status = 'pending_payment' AND s.created_at < now() - interval '24 hours'
  UNION ALL
  SELECT 'pendiente.con_comprobante', count(*)::bigint FROM subscriptions s
   WHERE s.status = 'pending_payment' AND s.payment_reference IS NOT NULL AND btrim(s.payment_reference) <> ''
  UNION ALL
  SELECT 'pendiente.con_perfil_asignado', count(*)::bigint FROM subscriptions s
   WHERE s.status = 'pending_payment' AND s.profile_id IS NOT NULL
  UNION ALL
  SELECT 'pendiente.duplica_suscripcion_vigente', count(*)::bigint FROM subscriptions s
   WHERE s.status = 'pending_payment'
     AND EXISTS (SELECT 1 FROM subscriptions v WHERE v.user_id = s.user_id AND v.platform_id = s.platform_id
                 AND v.id <> s.id AND v.status IN ('active','expired'))

  UNION ALL
  -- Avisos que quedaron «gastados» tras renovar (hallazgo N1): aún vigentes pero con banderas viejas
  SELECT 'avisos.vigente_con_reminder_sent_true', count(*)::bigint FROM subscriptions s
   WHERE s.status = 'active' AND s.end_date > now() + interval '3 days' AND s.reminder_sent = true
  UNION ALL
  SELECT 'avisos.vigente_con_expiry_notified_true', count(*)::bigint FROM subscriptions s
   WHERE s.status = 'active' AND s.end_date > now() AND s.expiry_notified = true
  UNION ALL
  SELECT 'avisos.vigente_con_debt_reminder_count_mayor_0', count(*)::bigint FROM subscriptions s
   WHERE s.status = 'active' AND s.end_date > now() AND s.debt_reminder_count > 0

  UNION ALL
  -- (7) Deriva de fechas (mensuales activas/vencidas)
  SELECT 'fechas.mensual_total', count(*)::bigint FROM subscriptions s
   WHERE s.status IN ('active','expired') AND s.plan_type = 'monthly'
  UNION ALL
  SELECT 'fechas.fin_23_59_59_caracas', count(*)::bigint FROM subscriptions s
   WHERE s.status IN ('active','expired') AND s.plan_type = 'monthly'
     AND (s.end_date AT TIME ZONE 'America/Caracas')::time = TIME '23:59:59'
  UNION ALL
  SELECT 'fechas.fin_otra_hora', count(*)::bigint FROM subscriptions s
   WHERE s.status IN ('active','expired') AND s.plan_type = 'monthly'
     AND (s.end_date AT TIME ZONE 'America/Caracas')::time <> TIME '23:59:59'
  UNION ALL
  SELECT 'fechas.dia_utc_distinto_a_caracas', count(*)::bigint FROM subscriptions s
   WHERE s.status IN ('active','expired') AND s.plan_type = 'monthly'
     AND (s.end_date AT TIME ZONE 'UTC')::date <> (s.end_date AT TIME ZONE 'America/Caracas')::date

  UNION ALL
  -- (8) Referencias de pago repetidas (sin marcas internas)
  SELECT 'refs.referencias_repetidas', count(*)::bigint FROM (
    SELECT 1 FROM subscriptions s
    WHERE s.payment_reference IS NOT NULL AND btrim(s.payment_reference) <> ''
      AND s.payment_reference NOT LIKE 'MANUAL-%' AND s.payment_reference NOT IN ('PANEL-MANUAL','MIGRADO')
    GROUP BY s.payment_reference HAVING count(*) > 1) g

) t
ORDER BY metrica;


-- =============================================================================
-- PLANTILLA PARA PEGAR EN EL CHAT (solo conteos; copia desde el BLOQUE 9 y el BLOQUE 0)
--
--   Fecha/hora de la ejecución: ____
--   BLOQUE 0 — columnas con existe=false: ____ (o «todas existen»)
--   BLOQUE 0b — restricciones CHECK sobre subscriptions: ____ (o «ninguna»)
--   BLOQUE 0c — checkout_orders / subscription_periods ya existen: ____ / ____
--   BLOQUE 9 (pega la tabla completa: metrica | valor)
--     estado.active ....................................... __
--     estado.expired ...................................... __
--     estado.expired_payment .............................. __
--     estado.pending_payment .............................. __
--     estado.cancelled .................................... __
--     dup.grupos_usuario_plataforma_con_2mas .............. __
--     dup.grupos_con_el_mismo_perfil ...................... __
--     exp_pago.total / con_comprobante .................... __ / __
--     mora.active.perfil_ocupado / mora.expired.perfil_ocupado  __ / __
--     mora.mensual.dias_7_a_13 / 14_a_29 / 30_o_mas ....... __ / __ / __
--     mora.mensual.recordatorios_deuda_0 / 1_a_5 / 6_o_mas  __ / __ / __
--     express.vencida_perfil_ocupado ...................... __
--     perfiles.occupied_sin_ninguna_suscripcion ........... __
--     perfiles.con_2_o_mas_suscripciones_activas .......... __
--     perfiles.available_con_suscripcion_activa ........... __
--     pendiente.total / mas_de_24h / con_comprobante ...... __ / __ / __
--     avisos.vigente_con_expiry_notified_true ............. __
--     fechas.fin_otra_hora / dia_utc_distinto_a_caracas ... __ / __
--     refs.referencias_repetidas .......................... __
--
--   NO pegues: los resultados de los BLOQUES 1 a 8 (son de detalle; solo para ti).
-- =============================================================================
