#!/usr/bin/env bash
# Prueba la migración O02 Lote 2 en una base DESECHABLE (o02_test). Nunca apuntar a Supabase.
# Uso:  PGHOST=/tmp PGPORT=5498 PGUSER=postgres tests/sql/run_o02_lote2.sh
# Requiere un servidor PostgreSQL 13+ local de prueba y los roles anon/authenticated/service_role
# (el script los crea si faltan). Recrea la base o02_test cada vez.
set -euo pipefail
cd "$(dirname "$0")/../.."
: "${PGHOST:?define PGHOST (socket o host local)}" "${PGUSER:?define PGUSER}"
case "$PGHOST" in *supabase*|*pooler*|*railway*|*amazonaws*) echo "ABORTADO: PGHOST parece un servicio real"; exit 2;; esac
psql_() { psql -q -v ON_ERROR_STOP=1 "$@"; }
psql_ -d postgres -c "DROP DATABASE IF EXISTS o02_test" -c "CREATE DATABASE o02_test"
for r in anon authenticated service_role; do
  psql_ -d postgres -c "DO \$\$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='$r') THEN CREATE ROLE $r NOLOGIN; END IF; END \$\$"
done
# Esquema base del repo (sin la extensión uuid-ossp) + las columnas de profiles que el código usa y el repo no declara.
sed -e 's/uuid_generate_v4()/gen_random_uuid()/g' -e '/CREATE EXTENSION/d' supabase_schema.sql | psql_ -d o02_test >/dev/null
psql_ -d o02_test -c "ALTER TABLE profiles ADD COLUMN IF NOT EXISTS reserved_for UUID, ADD COLUMN IF NOT EXISTS reserved_until TIMESTAMPTZ, ADD COLUMN IF NOT EXISTS is_extra_member BOOLEAN DEFAULT false"
psql_ -d o02_test -f docs/migraciones/o02-lote2-checkout.sql >/dev/null
psql_ -d o02_test -f docs/migraciones/o02-lote2-checkout.sql >/dev/null   # idempotencia: segunda corrida
psql_ -d o02_test -f tests/sql/o02_lote2_helpers.sql
psql_ -d o02_test -f tests/sql/o02_lote2_escenarios.sql
echo "TODO OK: migración aplicada dos veces y escenarios en verde"
