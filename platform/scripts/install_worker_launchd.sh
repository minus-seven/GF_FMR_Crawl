#!/usr/bin/env bash
set -euo pipefail

# Install the formal Worker as a per-user macOS launchd agent. Run this script
# from a normal Terminal session, not from an agent/sandbox process.
PLATFORM_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT_ROOT="$(cd "$PLATFORM_ROOT/.." && pwd)"
STATE_ROOT="${FACTSHEET_STATE_ROOT:-$PROJECT_ROOT/../../fund-research-state}"
FACTSHEET_VENV_ROOT="${FACTSHEET_VENV_ROOT:-$STATE_ROOT/dependencies/venv}"
PLATFORM_RUNTIME_ROOT="${PLATFORM_RUNTIME_ROOT:-$STATE_ROOT/platform-runtime}"
PYTHON_EXE="${PLATFORM_PYTHON_EXE:-$FACTSHEET_VENV_ROOT/bin/python}"
LABEL="${PLATFORM_WORKER_LAUNCHD_LABEL:-com.gf.fmr.worker}"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$PLIST_DIR/$LABEL.plist"
LOG_DIR="$PLATFORM_RUNTIME_ROOT"
DATABASE_URL="${PLATFORM_DATABASE_URL:-postgresql+psycopg://$(id -un)@127.0.0.1:${PLATFORM_POSTGRES_PORT:-55432}/fund_research}"
BROWSER_PATH="${PLAYWRIGHT_BROWSERS_PATH:-${FACTSHEET_RUNTIME_ROOT:-$STATE_ROOT/runtime}/ms-playwright}"

if [[ ! -x "$PYTHON_EXE" ]]; then
    echo "[worker] Python runtime not found: $PYTHON_EXE" >&2
    exit 2
fi
mkdir -p "$PLIST_DIR" "$LOG_DIR"

"$PYTHON_EXE" - "$PLIST_PATH" "$PROJECT_ROOT" "$PLATFORM_ROOT" "$PYTHON_EXE" "$DATABASE_URL" "$BROWSER_PATH" "$LABEL" "$STATE_ROOT" "$LOG_DIR" <<'PY'
import plistlib
import sys
from pathlib import Path

plist_path, project_root, platform_root, python_exe, database_url, browser_path, label, state_root, log_dir = sys.argv[1:]
payload = {
    "Label": label,
    "ProgramArguments": [
        python_exe,
        "-m",
        "app.worker.main",
        "--browser-preflight",
        "--worker-id",
        f"worker-launchd-{label}",
    ],
    "WorkingDirectory": project_root,
    "EnvironmentVariables": {
        "PYTHONPATH": str(Path(platform_root) / "api"),
        "PLATFORM_DATABASE_URL": database_url,
        "FACTSHEET_PROJECT_ROOT": project_root,
        "FACTSHEET_STATE_ROOT": state_root,
        "FACTSHEET_DATA_ROOT": str(Path(state_root) / "data"),
        "FACTSHEET_RUNTIME_ROOT": str(Path(state_root) / "runtime"),
        "PLATFORM_RUNTIME_ROOT": str(Path(state_root) / "platform-runtime"),
        "PLAYWRIGHT_BROWSERS_PATH": browser_path,
        "PLATFORM_BROWSER_PREFLIGHT": "required",
        "PYTHONUNBUFFERED": "1",
    },
    "RunAtLoad": True,
    "KeepAlive": True,
    "ProcessType": "Background",
    "ThrottleInterval": 10,
    "StandardOutPath": str(Path(log_dir) / "worker-launchd.log"),
    "StandardErrorPath": str(Path(log_dir) / "worker-launchd.err.log"),
}
Path(plist_path).write_bytes(plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=False))
PY

USER_ID="$(id -u)"
launchctl bootout "gui/$USER_ID" "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$USER_ID" "$PLIST_PATH"
launchctl enable "gui/$USER_ID/$LABEL" >/dev/null 2>&1 || true
echo "[OK] launchd Worker installed and loaded: $LABEL"
echo "     plist: $PLIST_PATH"
echo "     logs:  $LOG_DIR/worker-launchd.log"

