#!/usr/bin/env bash
set -euo pipefail

PLATFORM_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLATFORM_RUNTIME_ROOT="${PLATFORM_RUNTIME_ROOT:-${FACTSHEET_STATE_ROOT:-$PROJECT_ROOT/../../fund-research-state}/platform-runtime}"
PG_BIN="$PLATFORM_RUNTIME_ROOT/postgres/bin"
HOST="${PGHOST:-127.0.0.1}"
PORT="${PGPORT:-${PLATFORM_POSTGRES_PORT:-55432}}"
USER_NAME="${PGUSER:-$(id -un)}"
DATABASE="${PGDATABASE:-${PLATFORM_DATABASE_NAME:-fund_research}}"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUTPUT_DIR="${PLATFORM_D5_BACKUP_DIR:-$PLATFORM_ROOT/backups/d5}"
RESTORE_DB="${PLATFORM_D5_RESTORE_DB:-fund_research_d5_restore_$STAMP}"
DUMP_FILE="$OUTPUT_DIR/fund_research_$STAMP.dump"
REPORT_FILE="$OUTPUT_DIR/restore_report_$STAMP.json"

mkdir -p "$OUTPUT_DIR"

if [[ ! -x "$PG_BIN/pg_dump" || ! -x "$PG_BIN/pg_restore" ]]; then
    echo "PostgreSQL client runtime is missing: $PG_BIN" >&2
    exit 2
fi

if "$PG_BIN/psql" -h "$HOST" -p "$PORT" -U "$USER_NAME" -d postgres \
    -Atc "SELECT 1 FROM pg_database WHERE datname='$RESTORE_DB'" | grep -q 1; then
    echo "Restore database already exists: $RESTORE_DB" >&2
    exit 2
fi

echo "Creating database dump: $DUMP_FILE"
"$PG_BIN/pg_dump" \
    -h "$HOST" \
    -p "$PORT" \
    -U "$USER_NAME" \
    -d "$DATABASE" \
    -Fc \
    -f "$DUMP_FILE"

echo "Creating restore database: $RESTORE_DB"
"$PG_BIN/createdb" -h "$HOST" -p "$PORT" -U "$USER_NAME" "$RESTORE_DB"

echo "Restoring dump"
"$PG_BIN/pg_restore" \
    -h "$HOST" \
    -p "$PORT" \
    -U "$USER_NAME" \
    -d "$RESTORE_DB" \
    --no-owner \
    --no-privileges \
    "$DUMP_FILE"

read_counts() {
    local database="$1"
    "$PG_BIN/psql" -h "$HOST" -p "$PORT" -U "$USER_NAME" -d "$database" \
        -Atc "
          SELECT
            (SELECT count(*) FROM funds),
            (SELECT count(*) FROM fund_report_sources),
            (SELECT count(*) FROM dataset_batches),
            (SELECT current_batch_id FROM publication_state WHERE id = 1);
        "
}

SOURCE_COUNTS="$(read_counts "$DATABASE")"
RESTORE_COUNTS="$(read_counts "$RESTORE_DB")"
if [[ "$SOURCE_COUNTS" != "$RESTORE_COUNTS" ]]; then
    echo "Restore count mismatch: source=$SOURCE_COUNTS restore=$RESTORE_COUNTS" >&2
    exit 3
fi

RESTORE_CURRENT_STATUS="$("$PG_BIN/psql" \
    -h "$HOST" -p "$PORT" -U "$USER_NAME" -d "$RESTORE_DB" -Atc "
      SELECT b.status
      FROM publication_state ps
      JOIN dataset_batches b ON b.id = ps.current_batch_id
      WHERE ps.id = 1;
    ")"
if [[ "$RESTORE_CURRENT_STATUS" != "published" ]]; then
    echo "Restored current batch is not published: $RESTORE_CURRENT_STATUS" >&2
    exit 3
fi

DUMP_SHA256="$(shasum -a 256 "$DUMP_FILE" | awk '{print $1}')"
DUMP_SIZE="$(stat -f '%z' "$DUMP_FILE" 2>/dev/null || stat -c '%s' "$DUMP_FILE")"
printf '%s\n' "$SOURCE_COUNTS" | awk -F'|' \
    -v dump="$DUMP_FILE" \
    -v dump_sha="$DUMP_SHA256" \
    -v dump_size="$DUMP_SIZE" \
    -v restore_db="$RESTORE_DB" \
    -v snapshot_status="$RESTORE_CURRENT_STATUS" \
    '{
      printf "{\n";
      printf "  \"dump_file\": \"%s\",\n", dump;
      printf "  \"dump_sha256\": \"%s\",\n", dump_sha;
      printf "  \"dump_size_bytes\": %s,\n", dump_size;
      printf "  \"restore_database\": \"%s\",\n", restore_db;
      printf "  \"funds\": %s,\n", $1;
      printf "  \"report_sources\": %s,\n", $2;
      printf "  \"batches\": %s,\n", $3;
      printf "  \"current_batch_id\": \"%s\",\n", $4;
      printf "  \"current_batch_status\": \"%s\",\n", snapshot_status;
      printf "  \"passed\": true\n";
      printf "}\n";
    }' > "$REPORT_FILE"

echo "Restore smoke passed: $RESTORE_DB"
echo "Dump: $DUMP_FILE"
echo "Report: $REPORT_FILE"
