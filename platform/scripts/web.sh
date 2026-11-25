#!/usr/bin/env bash
set -euo pipefail

PLATFORM_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WEB_ROOT="$PLATFORM_ROOT/web"
PROJECT_ROOT="$(cd "$PLATFORM_ROOT/.." && pwd)"
STATE_ROOT="${FACTSHEET_STATE_ROOT:-$PROJECT_ROOT/../../fund-research-state}"
PLATFORM_RUNTIME_ROOT="${PLATFORM_RUNTIME_ROOT:-$STATE_ROOT/platform-runtime}"
WEB_NODE_MODULES_ROOT="${WEB_NODE_MODULES_ROOT:-$STATE_ROOT/dependencies/web/node_modules}"
PID_FILE="$PLATFORM_RUNTIME_ROOT/web.pid"
LOG_FILE="$PLATFORM_RUNTIME_ROOT/web.log"
HOST="${PLATFORM_WEB_HOST:-127.0.0.1}"
PORT="${PLATFORM_WEB_PORT:-5173}"

CODEX_RUNTIME_ROOT="${CODEX_RUNTIME_ROOT:-${HOME:-}/.cache/codex-runtimes/codex-primary-runtime/dependencies}"
CODEX_NODE_BIN="$CODEX_RUNTIME_ROOT/node/bin"
CODEX_TOOL_BIN="$CODEX_RUNTIME_ROOT/bin/fallback"
if [[ -d "$CODEX_NODE_BIN" ]]; then
    export PATH="$CODEX_NODE_BIN:$CODEX_TOOL_BIN:$PATH"
fi

is_running() {
    [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

web_ready() {
    curl --noproxy '*' -fsS "http://$HOST:$PORT/" >/dev/null 2>&1
}

require_runtime() {
    if ! command -v node >/dev/null 2>&1; then
        echo "Node.js is required; install Node.js 20+ or set CODEX_RUNTIME_ROOT" >&2
        exit 1
    fi
    if [[ ! -f "$WEB_NODE_MODULES_ROOT/vite/bin/vite.js" && -f "$WEB_ROOT/node_modules/vite/bin/vite.js" ]]; then
        WEB_NODE_MODULES_ROOT="$WEB_ROOT/node_modules"
    fi
    if [[ ! -f "$WEB_NODE_MODULES_ROOT/vite/bin/vite.js" ]]; then
        echo "Web dependencies are missing; run: (cd platform/web && pnpm install --frozen-lockfile)" >&2
        exit 1
    fi
}

require_pnpm() {
    if ! command -v pnpm >/dev/null 2>&1; then
        echo "pnpm is required for build/test; install pnpm or set CODEX_RUNTIME_ROOT" >&2
        exit 1
    fi
}

start_web() {
    if web_ready; then
        echo "Web already running on http://$HOST:$PORT"
        return
    fi
    require_runtime
    mkdir -p "$PLATFORM_RUNTIME_ROOT"
    nohup node "$WEB_NODE_MODULES_ROOT/vite/bin/vite.js" "$WEB_ROOT" \
        --host "$HOST" \
        --port "$PORT" \
        >"$LOG_FILE" 2>&1 &
    echo $! >"$PID_FILE"
    for _ in $(seq 1 50); do
        if web_ready; then
            echo "Web started: http://$HOST:$PORT"
            return
        fi
        sleep 0.2
    done
    echo "Web failed to start; see $LOG_FILE" >&2
    exit 1
}

stop_web() {
    if ! is_running; then
        if web_ready; then
            echo "Web is running outside this PID file; stop its owning session manually" >&2
            exit 1
        fi
        rm -f "$PID_FILE"
        echo "Web is not running"
        return
    fi
    local pid
    pid="$(cat "$PID_FILE")"
    kill "$pid"
    for _ in $(seq 1 30); do
        if ! kill -0 "$pid" 2>/dev/null; then
            rm -f "$PID_FILE"
            echo "Web stopped"
            return
        fi
        sleep 0.2
    done
    echo "Web process $pid did not stop" >&2
    exit 1
}

run_pnpm() {
    require_pnpm
    (
        cd "$WEB_ROOT"
        pnpm "$@"
    )
}

case "${1:-}" in
    start)
        start_web
        ;;
    stop)
        stop_web
        ;;
    restart)
        stop_web
        start_web
        ;;
    status)
        if web_ready; then
            if is_running; then
                echo "Web running: http://$HOST:$PORT (PID $(cat "$PID_FILE"))"
            else
                echo "Web running: http://$HOST:$PORT (external PID)"
            fi
        else
            echo "Web is not running"
            exit 1
        fi
        ;;
    logs)
        tail -n "${2:-100}" "$LOG_FILE"
        ;;
    build)
        run_pnpm build
        ;;
    test)
        run_pnpm test
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|logs|build|test}" >&2
        exit 2
        ;;
esac
