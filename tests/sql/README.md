# Pruebas SQL de la migración O02 Lote 2

Prueban `docs/migraciones/o02-lote2-checkout.sql` (tablas, triggers y `approve_checkout_order`) en una
**base PostgreSQL desechable** con datos ficticios. **No se ejecutan nunca contra Supabase**: borran
datos; los archivos abortan si la base no se llama `o02_test`.

```bash
PGHOST=/tmp PGPORT=5498 PGUSER=postgres tests/sql/run_o02_lote2.sh
```

Cubren: nueva (mensual y express), renovación (periodos, fin de mes, bisiesto, medianoche UTC/Caracas,
avisos reiniciados), dos servicios de la misma plataforma, perfil reasignado/liberado/sin stock,
cambio de plataforma, atomicidad de una orden mixta, idempotencia, estados y motivos, dueño de la
suscripción, importes que no suman, referencia duplicada, inmutabilidad del historial y de las
órdenes aprobadas. Aparte (manual, una vez): la aritmética de meses de Postgres coincide con
`utils.dates.add_months` (4.216 casos, 0 diferencias) y dos aprobaciones simultáneas se serializan.
