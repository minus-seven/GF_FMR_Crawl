#!/usr/bin/env bash
set -euo pipefail

PLATFORM_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT_ROOT="$(cd "$PLATFORM_ROOT/.." && pwd)"
STATE_ROOT="${FACTSHEET_STATE_ROOT:-$PROJECT_ROOT/../../fund-research-state}"
PLATFORM_RUNTIME_ROOT="${PLATFORM_RUNTIME_ROOT:-$STATE_ROOT/platform-runtime}"
FACTSHEET_VENV_ROOT="${FACTSHEET_VENV_ROOT:-$STATE_ROOT/dependencies/venv}"
PID_FILE="$PLATFORM_RUNTIME_ROOT/worker.pid"
LOG_FILE="$PLATFORM_RUNTIME_ROOT/worker.log"
NATIVE_RUNNER="$PLATFORM_ROOT/scripts/run_worker_native.sh"
DATABASE_URL="${PLATFORM_DATABASE_URL:-postgresql+psycopg://$(id -un)@127.0.0.1:${PLATFORM_POSTGRES_PORT:-55432}/fund_research}"

is_running() {
    local pid command candidate
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
        # kill -0 只能说明 PID 被占用，不能说明它仍是本项目 worker；
        # 防止重启后 PID 复用导致 start 被错误跳过。
        command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
        if [[ "$command" == *"-m app.worker.main"* ]]; then
            return 0
        fi
    fi

    # PID 文件可能在异常退出、系统重启或手工启动 worker 后失效。
    # 回退扫描同一项目的 worker，避免重复启动和“队列无人消费”。
    candidate="$(ps -ax -o pid=,command= 2>/dev/null \
        | awk '$0 ~ /-m app\.worker\.main/ {print $1; exit}')"
    if [[ "$candidate" =~ ^[0-9]+$ ]]; then
        printf '%s\n' "$candidate" >"$PID_FILE"
        return 0
    fi
    return 1
}

start_worker() {
    if is_running; then
        echo "Worker already running (PID $(cat "$PID_FILE"))"
        return
    fi
    rm -f "$PID_FILE"
    mkdir -p "$PLATFORM_RUNTIME_ROOT"
    PLATFORM_DATABASE_URL="$DATABASE_URL" \
        nohup "$NATIVE_RUNNER" \
        >>"$LOG_FILE" 2>&1 &
    echo $! >"$PID_FILE"
    for _ in $(seq 1 30); do
        if grep -Fq "Native browser preflight passed" "$LOG_FILE" 2>/dev/null; then
            echo "Worker started (PID $(cat "$PID_FILE"))"
            return
        fi
        if grep -Fq "Native browser preflight failed" "$LOG_FILE" 2>/dev/null; then
            echo "Worker browser preflight failed; see $LOG_FILE" >&2
            exit 1
        fi
        if ! is_running; then
            echo "Worker failed to start; see $LOG_FILE" >&2
            exit 1
        fi
        sleep 0.5
    done
    echo "Worker started but preflight is still pending (PID $(cat "$PID_FILE")); see $LOG_FILE"
}

stop_worker() {
    if ! is_running; then
        rm -f "$PID_FILE"
        echo "Worker is not running"
        return
    fi
    local pid
    pid="$(cat "$PID_FILE")"
    kill "$pid"
    for _ in $(seq 1 30); do
        if ! kill -0 "$pid" 2>/dev/null; then
            rm -f "$PID_FILE"
            echo "Worker stopped"
            return
        fi
        sleep 0.2
    done
    echo "Worker process $pid did not stop" >&2
    exit 1
}

run_once() {
    PLATFORM_DATABASE_URL="$DATABASE_URL" \
        "$NATIVE_RUNNER" --once --worker-id "worker-once-$$"
}

run_foreground() {
    PLATFORM_DATABASE_URL="$DATABASE_URL" \
        "$NATIVE_RUNNER" "$@"
}

browser_check() {
    PYTHONPATH="$PLATFORM_ROOT/api" \
        FACTSHEET_PROJECT_ROOT="$PROJECT_ROOT" \
        PLATFORM_BROWSER_PREFLIGHT="required" \
        "$FACTSHEET_VENV_ROOT/bin/python" - <<'PY'
from app.worker.browser_preflight import check_browser
try:
    result = check_browser()
except RuntimeError as exc:
    print(exc, file=__import__("sys").stderr)
    raise SystemExit(78)
print(f"Native browser OK: {result.browser} ({result.version})")
PY
}

case "${1:-}" in
    start)
        start_worker
        ;;
    stop)
        stop_worker
        ;;
    restart)
        stop_worker
        start_worker
        ;;
    status)
        if is_running; then
            echo "Worker running (PID $(cat "$PID_FILE"))"
        else
            echo "Worker is not running"
            exit 1
        fi
        ;;
    once)
        run_once
        ;;
    foreground)
        shift
        run_foreground "$@"
        ;;
    browser-check)
        browser_check
        ;;
    logs)
        tail -n "${2:-100}" "$LOG_FILE"
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|once|foreground|browser-check|logs}" >&2
        exit 2
        ;;
esac
