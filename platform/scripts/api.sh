#!/usr/bin/env bash
set -euo pipefail

PLATFORM_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT_ROOT="$(cd "$PLATFORM_ROOT/.." && pwd)"
STATE_ROOT="${FACTSHEET_STATE_ROOT:-$PROJECT_ROOT/../../fund-research-state}"
PLATFORM_RUNTIME_ROOT="${PLATFORM_RUNTIME_ROOT:-$STATE_ROOT/platform-runtime}"
FACTSHEET_VENV_ROOT="${FACTSHEET_VENV_ROOT:-$STATE_ROOT/dependencies/venv}"
PID_FILE="$PLATFORM_RUNTIME_ROOT/api.pid"
LOG_FILE="$PLATFORM_RUNTIME_ROOT/api.log"
HOST="${PLATFORM_API_HOST:-127.0.0.1}"
PORT="${PLATFORM_API_PORT:-8000}"
DATABASE_URL="${PLATFORM_DATABASE_URL:-postgresql+psycopg://$(id -un)@127.0.0.1:${PLATFORM_POSTGRES_PORT:-55432}/fund_research}"

is_running() {
    [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

api_ready() {
    curl -fsS "http://$HOST:$PORT/api/health" >/dev/null 2>&1
}

start_api() {
    if api_ready; then
        echo "API already running on http://$HOST:$PORT"
        return
    fi
    mkdir -p "$PLATFORM_RUNTIME_ROOT"
    PYTHONPATH="$PLATFORM_ROOT/api" \
        PLATFORM_DATABASE_URL="$DATABASE_URL" \
        nohup "$FACTSHEET_VENV_ROOT/bin/uvicorn" \
        app.main:app \
        --host "$HOST" \
        --port "$PORT" \
        >"$LOG_FILE" 2>&1 &
    echo $! >"$PID_FILE"
    for _ in $(seq 1 50); do
        if api_ready; then
            echo "API started: http://$HOST:$PORT"
            return
        fi
        sleep 0.2
    done
    echo "API failed to start; see $LOG_FILE" >&2
    exit 1
}

stop_api() {
    if ! is_running; then
        if api_ready; then
            echo "API is running outside this PID file; stop its owning session manually" >&2
            exit 1
        fi
        rm -f "$PID_FILE"
        echo "API is not running"
        return
    fi
    local pid
    pid="$(cat "$PID_FILE")"
    kill "$pid"
    for _ in $(seq 1 30); do
        if ! kill -0 "$pid" 2>/dev/null; then
            rm -f "$PID_FILE"
            echo "API stopped"
            return
        fi
        sleep 0.2
    done
    echo "API process $pid did not stop" >&2
    exit 1
}

case "${1:-}" in
    start)
        start_api
        ;;
    stop)
        stop_api
        ;;
    restart)
        stop_api
        start_api
        ;;
    status)
        if api_ready; then
            if is_running; then
                echo "API running: http://$HOST:$PORT (PID $(cat "$PID_FILE"))"
            else
                echo "API running: http://$HOST:$PORT (external PID)"
            fi
            curl -fsS "http://$HOST:$PORT/api/health"
            echo
        else
            echo "API is not running"
            exit 1
        fi
        ;;
    logs)
        tail -n "${2:-100}" "$LOG_FILE"
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|logs}" >&2
        exit 2
        ;;
esac
