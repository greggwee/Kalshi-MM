"""Append-only JSONL event journal for a trading session.

Every decision the engine makes lands here (one JSON object per line, each
with `type` and `ts`), so the diagnostics report can be rebuilt offline from
the journal alone. Event types written by the session engine:

    session_start, market_meta, cycle, tick, order, fill, trade, markout,
    position_mismatch, risk, cycle_error, api_error, api_stats, session_end
"""
import json
import os
import threading
import time
from typing import Iterator, Optional


class Journal:
    def __init__(self, path: Optional[str]):
        self.path = path
        self._lock = threading.Lock()
        self._fh = None
        if path:
            dirname = os.path.dirname(path)
            if dirname:
                os.makedirs(dirname, exist_ok=True)
            self._fh = open(path, "a", buffering=1)  # line-buffered: survives crashes

    def write(self, event_type: str, **fields) -> dict:
        event = {"type": event_type, "ts": fields.pop("ts", None) or time.time(), **fields}
        if self._fh is not None:
            line = json.dumps(event, default=str, separators=(",", ":"))
            with self._lock:
                self._fh.write(line + "\n")
        return event

    def close(self):
        if self._fh is not None:
            self._fh.close()
            self._fh = None


def read_journal(path: str) -> Iterator[dict]:
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue  # a torn final line after a hard kill
