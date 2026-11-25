#!/usr/bin/env bash
set -euo pipefail

# Formal Worker entrypoint. Run this from a native Terminal session or launchd;
# do not start it from an agent/sandbox process.
PLATFORM_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT_ROOT="$(cd "$PLATFORM_ROOT/.." && pwd)"
STATE_ROOT="${FACTSHEET_STATE_ROOT:-$PROJECT_ROOT/../../fund-research-state}"
PYTHON_EXE="${PLATFORM_PYTHON_EXE:-${FACTSHEET_VENV_ROOT:-$STATE_ROOT/dependencies/venv}/bin/python}"
DATABASE_URL="${PLATFORM_DATABASE_URL:-postgresql+psycopg://$(id -un)@127.0.0.1:${PLATFORM_POSTGRES_PORT:-55432}/fund_research}"

if [[ ! -x "$PYTHON_EXE" ]]; then
    echo "[worker] Python runtime not found: $PYTHON_EXE" >&2
    exit 2
fi

export PYTHONPATH="$PLATFORM_ROOT/api${PYTHONPATH:+:$PYTHONPATH}"
export PLATFORM_DATABASE_URL="$DATABASE_URL"
export FACTSHEET_PROJECT_ROOT="$PROJECT_ROOT"
export PLATFORM_BROWSER_PREFLIGHT="required"
export PYTHONUNBUFFERED=1

RUNTIME_ROOT="${FACTSHEET_RUNTIME_ROOT:-$STATE_ROOT/runtime}"
if [[ -z "${PLAYWRIGHT_BROWSERS_PATH:-}" && -d "$RUNTIME_ROOT/ms-playwright" ]]; then
    export PLAYWRIGHT_BROWSERS_PATH="$RUNTIME_ROOT/ms-playwright"
fi

exec "$PYTHON_EXE" -m app.worker.main \
    --browser-preflight \
    --worker-id "${PLATFORM_WORKER_ID:-worker-native-$(hostname)-$$}" \
    "$@"

