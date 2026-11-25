"""Thread-safe machine-readable progress events."""

from __future__ import annotations

import json
import sys
import threading
from typing import Any


_PRINT_LOCK = threading.Lock()


def emit_progress_event(payload: dict[str, Any]) -> None:
    line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    with _PRINT_LOCK:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
