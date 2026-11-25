#!/usr/bin/env bash
set -euo pipefail

PLATFORM_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT_ROOT="$(cd "$PLATFORM_ROOT/.." && pwd)"
PLATFORM_RUNTIME_ROOT="${PLATFORM_RUNTIME_ROOT:-${FACTSHEET_STATE_ROOT:-$PROJECT_ROOT/../../fund-research-state}/platform-runtime}"
PG_BIN="$PLATFORM_RUNTIME_ROOT/postgres/bin"
PGDATA="${PLATFORM_PGDATA:-$PLATFORM_RUNTIME_ROOT/postgres-data}"
PGSOCKET="${PLATFORM_PGSOCKET:-${TMPDIR:-/tmp}/gf-fmr-postgres-socket-$UID}"
PGLOG="${PLATFORM_PGLOG:-$PLATFORM_RUNTIME_ROOT/postgres.log}"
PGPORT="${PLATFORM_POSTGRES_PORT:-55432}"
PGUSER_NAME="$(id -un)"
DATABASE="${PLATFORM_DATABASE_NAME:-fund_research}"
READONLY_ROLE="${PLATFORM_READONLY_ROLE:-fund_research_ro}"

require_runtime() {
    if [[ ! -x "$PG_BIN/postgres" ]]; then
        echo "PostgreSQL runtime missing: $PG_BIN/postgres" >&2
        exit 2
    fi
}

ensure_macos_runtime_paths() {
    # Conda Postgres embeds the build prefix; recreate only the two compatibility
    # links needed when that temporary prefix is cleaned by macOS.
    [[ "$(uname -s)" == "Darwin" ]] || return 0
    local compiled_share
    # awk exits after the first match; with pipefail this can surface the
    # producer's SIGPIPE as exit 141 and abort startup before pg_ctl runs.
    compiled_share="$(
        set +o pipefail
        strings "$PG_BIN/postgres" |
            awk '/^\/.*\/share$/ { print; exit }'
    )"
    [[ -n "$compiled_share" ]] || return 0
    local compiled_prefix
    compiled_prefix="$(dirname "$compiled_share")"
    mkdir -p "$compiled_prefix"
    if [[ ! -d "$compiled_share/zoneinfo" ]]; then
        if [[ -e "$compiled_share" && ! -L "$compiled_share" ]]; then
            mv "$compiled_share" \
                "$compiled_share.stale-$(date +%Y%m%d%H%M%S)"
        fi
        ln -sfn "$PLATFORM_RUNTIME_ROOT/postgres/share" "$compiled_share"
    fi
    if [[ ! -e "$compiled_prefix/lib/pg_trgm.dylib" ]]; then
        mkdir -p "$compiled_prefix/lib"
        ln -s \
            "$PLATFORM_RUNTIME_ROOT/postgres/lib/pg_trgm.dylib" \
            "$compiled_prefix/lib/pg_trgm.dylib"
    fi
    # pgvector is loaded by PostgreSQL from the compiled prefix. The bundled
    # runtime keeps the extension in its managed lib directory, so restore the
    # link after the temporary build prefix is removed.
    if [[ ! -e "$compiled_prefix/lib/vector.dylib" ]]; then
        mkdir -p "$compiled_prefix/lib"
        ln -s \
            "$PLATFORM_RUNTIME_ROOT/postgres/lib/vector.dylib" \
            "$compiled_prefix/lib/vector.dylib"
    fi
    # psql loads libncurses, which re-exports libtinfo from the same prefix.
    if [[ ! -e "$compiled_prefix/lib/libtinfo.6.dylib" ]]; then
        mkdir -p "$compiled_prefix/lib"
        ln -s \
            "$PLATFORM_RUNTIME_ROOT/postgres/lib/libtinfo.6.dylib" \
            "$compiled_prefix/lib/libtinfo.6.dylib"
    fi
}

initialize_cluster() {
    require_runtime
    ensure_macos_runtime_paths
    mkdir -p "$PGSOCKET" "$(dirname "$PGDATA")"
    if [[ ! -f "$PGDATA/PG_VERSION" ]]; then
        "$PG_BIN/initdb" \
            -D "$PGDATA" \
            --no-locale \
            --encoding=UTF8 \
            --auth=trust
    fi
}

is_running() {
    [[ -f "$PGDATA/PG_VERSION" ]] &&
        "$PG_BIN/pg_ctl" -D "$PGDATA" status >/dev/null 2>&1
}

start_server() {
    initialize_cluster
    if is_running; then
        echo "PostgreSQL already running on 127.0.0.1:$PGPORT"
        return
    fi
    mkdir -p "$PGSOCKET"
    "$PG_BIN/pg_ctl" \
        -D "$PGDATA" \
        -l "$PGLOG" \
        -o "-k $PGSOCKET -p $PGPORT -c listen_addresses=127.0.0.1" \
        start
    "$PG_BIN/pg_isready" -h 127.0.0.1 -p "$PGPORT"
}

stop_server() {
    require_runtime
    if [[ ! -f "$PGDATA/PG_VERSION" ]]; then
        echo "PostgreSQL cluster is not initialized"
        return
    fi
    if is_running; then
        "$PG_BIN/pg_ctl" -D "$PGDATA" stop -m fast
    else
        echo "PostgreSQL is not running"
    fi
}

initialize_platform_database() {
    start_server
    local admin_url="postgresql://$PGUSER_NAME@127.0.0.1:$PGPORT/postgres"
    PYTHONPATH="$PLATFORM_ROOT/api" \
        "${FACTSHEET_VENV_ROOT:-${FACTSHEET_STATE_ROOT:-$PROJECT_ROOT/../../fund-research-state}/dependencies/venv}/bin/python" \
        "$PLATFORM_ROOT/scripts/bootstrap_postgres.py" \
        --admin-url "$admin_url" \
        --database "$DATABASE" \
        --readonly-role "$READONLY_ROLE"
}

status_server() {
    require_runtime
    if is_running; then
        "$PG_BIN/pg_ctl" -D "$PGDATA" status
        "$PG_BIN/pg_isready" -h 127.0.0.1 -p "$PGPORT"
    else
        echo "PostgreSQL is not running"
        exit 1
    fi
}

open_psql() {
    require_runtime
    exec "$PG_BIN/psql" \
        -h 127.0.0.1 \
        -p "$PGPORT" \
        -U "$PGUSER_NAME" \
        -d "$DATABASE" \
        "$@"
}

case "${1:-}" in
    init)
        initialize_platform_database
        ;;
    start)
        start_server
        ;;
    stop)
        stop_server
        ;;
    restart)
        stop_server
        start_server
        ;;
    status)
        status_server
        ;;
    psql)
        shift
        open_psql "$@"
        ;;
    logs)
        tail -n "${2:-100}" "$PGLOG"
        ;;
    url)
        echo "postgresql+psycopg://$PGUSER_NAME@127.0.0.1:$PGPORT/$DATABASE"
        ;;
    *)
        echo "Usage: $0 {init|start|stop|restart|status|psql|logs|url}" >&2
        exit 2
        ;;
esac
