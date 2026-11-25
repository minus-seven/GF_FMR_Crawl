#!/usr/bin/env bash
set -euo pipefail

PLATFORM_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT_ROOT="$(cd "$PLATFORM_ROOT/.." && pwd)"
STATE_ROOT="${FACTSHEET_STATE_ROOT:-$PROJECT_ROOT/../../fund-research-state}"
PLATFORM_RUNTIME_ROOT="${PLATFORM_RUNTIME_ROOT:-$STATE_ROOT/platform-runtime}"
FACTSHEET_VENV_ROOT="${FACTSHEET_VENV_ROOT:-$STATE_ROOT/dependencies/venv}"
PG_BIN="$PLATFORM_RUNTIME_ROOT/postgres/bin"
HOST="${PGHOST:-127.0.0.1}"
PORT="${PGPORT:-${PLATFORM_POSTGRES_PORT:-55432}}"
USER_NAME="${PGUSER:-$(id -un)}"
STAMP="$(date +%Y%m%d_%H%M%S)"
FRESH_DB="${PLATFORM_D5_MIGRATION_DB:-fund_research_d5_migration_$STAMP}"
OUTPUT_DIR="${PLATFORM_D5_BACKUP_DIR:-$PLATFORM_ROOT/backups/d5}"
REPORT_FILE="$OUTPUT_DIR/migration_report_$STAMP.json"
SOURCE_URL="${PLATFORM_DATABASE_URL:-postgresql+psycopg://$USER_NAME@$HOST:$PORT/fund_research}"
FRESH_URL="postgresql+psycopg://$USER_NAME@$HOST:$PORT/$FRESH_DB"
HKEX_WORKBOOK="${PLATFORM_D5_HKEX_WORKBOOK:-$STATE_ROOT/inputs/HKEX基金综合资料库_完整_20261008.xlsx}"
CRAWLER_WORKBOOK="${PLATFORM_D5_CRAWLER_WORKBOOK:-$STATE_ROOT/inputs/爬虫list.xlsx}"
STRUCTURED_ROOT="${PLATFORM_D5_STRUCTURED_ROOT:-$STATE_ROOT/data/20260908162205}"

mkdir -p "$OUTPUT_DIR"

if "$PG_BIN/psql" -h "$HOST" -p "$PORT" -U "$USER_NAME" -d postgres \
    -Atc "SELECT 1 FROM pg_database WHERE datname='$FRESH_DB'" | grep -q 1; then
    echo "Migration database already exists: $FRESH_DB" >&2
    exit 2
fi

echo "Checking migration idempotency on existing database"
PLATFORM_DATABASE_URL="$SOURCE_URL" \
    "$FACTSHEET_VENV_ROOT/bin/alembic" \
    -c "$PLATFORM_ROOT/api/alembic.ini" \
    upgrade head

echo "Creating empty migration database: $FRESH_DB"
"$PG_BIN/createdb" -h "$HOST" -p "$PORT" -U "$USER_NAME" "$FRESH_DB"

echo "Applying full migration to empty database"
PLATFORM_DATABASE_URL="$FRESH_URL" \
    "$FACTSHEET_VENV_ROOT/bin/alembic" \
    -c "$PLATFORM_ROOT/api/alembic.ini" \
    upgrade head

echo "Importing seed data into empty migrated database"
PYTHONPATH="$PLATFORM_ROOT/api" \
    "$FACTSHEET_VENV_ROOT/bin/python" \
    -m app.importers.cli \
    --database-url "$FRESH_URL" \
    --hkex-workbook "$HKEX_WORKBOOK" \
    --crawler-workbook "$CRAWLER_WORKBOOK" \
    --structured-root "$STRUCTURED_ROOT"

echo "Reapplying migration to verify idempotency"
PLATFORM_DATABASE_URL="$FRESH_URL" \
    "$FACTSHEET_VENV_ROOT/bin/alembic" \
    -c "$PLATFORM_ROOT/api/alembic.ini" \
    upgrade head

FRESH_REVISION="$("$PG_BIN/psql" -h "$HOST" -p "$PORT" -U "$USER_NAME" \
    -d "$FRESH_DB" -Atc "SELECT version_num FROM alembic_version;")"
SOURCE_REVISION="$("$PG_BIN/psql" -h "$HOST" -p "$PORT" -U "$USER_NAME" \
    -d "${PGDATABASE:-fund_research}" -Atc "SELECT version_num FROM alembic_version;")"
SEED_COUNTS="$("$PG_BIN/psql" -h "$HOST" -p "$PORT" -U "$USER_NAME" \
    -d "$FRESH_DB" -Atc "
      SELECT
        (SELECT count(*) FROM funds),
        (SELECT count(*) FROM fund_report_sources),
        (SELECT count(*) FROM fund_metrics),
        (SELECT count(*) FROM fund_holdings),
        (SELECT count(*) FROM fund_allocations);
    ")"
IFS='|' read -r SEED_FUNDS SEED_SOURCES SEED_METRICS SEED_HOLDINGS SEED_ALLOCATIONS <<< "$SEED_COUNTS"
if [[ "$SEED_FUNDS" != "2479" || "$SEED_SOURCES" != "775" ]]; then
    echo "Seed import check failed: $SEED_COUNTS" >&2
    exit 3
fi

printf '{\n  "fresh_database": "%s",\n  "fresh_revision": "%s",\n  "existing_revision": "%s",\n  "empty_upgrade": true,\n  "repeated_upgrade": true,\n  "existing_upgrade": true,\n  "seed_import": true,\n  "seed_funds": %s,\n  "seed_report_sources": %s,\n  "seed_metrics": %s,\n  "seed_holdings": %s,\n  "seed_allocations": %s,\n  "passed": true\n}\n' \
    "$FRESH_DB" \
    "$FRESH_REVISION" \
    "$SOURCE_REVISION" \
    "$SEED_FUNDS" \
    "$SEED_SOURCES" \
    "$SEED_METRICS" \
    "$SEED_HOLDINGS" \
    "$SEED_ALLOCATIONS" > "$REPORT_FILE"

echo "Migration check passed: $FRESH_DB"
echo "Report: $REPORT_FILE"
