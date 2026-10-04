"""Append-only audit log: every pipeline step writes one JSON line."""
from __future__ import annotations
import json
import time
import uuid

from . import config

RUN_ID = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]


def log(event: str, **data) -> None:
    config.AUDIT.mkdir(parents=True, exist_ok=True)
    line = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "run_id": RUN_ID, "event": event, **data}
    with open(config.AUDIT / "audit.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
