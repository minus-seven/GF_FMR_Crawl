#!/usr/bin/env bash
set -euo pipefail

LABEL="${PLATFORM_WORKER_LAUNCHD_LABEL:-com.gf.fmr.worker}"
PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"
USER_ID="$(id -u)"
launchctl bootout "gui/$USER_ID" "$PLIST_PATH" >/dev/null 2>&1 || true
rm -f "$PLIST_PATH"
echo "[OK] launchd Worker removed: $LABEL"

