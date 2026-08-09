#!/usr/bin/env python3
from __future__ import annotations
import json
import os
import time
from pathlib import Path

path = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "kittyproto/notification-history.json"
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    data = []

for item in data[-25:][::-1]:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(item.get("timestamp", 0))))
    app = item.get("app_name") or "unknown"
    summary = item.get("summary") or ""
    print(f"{stamp}  {app}: {summary}")
