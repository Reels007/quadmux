"""Read access for the status-bus event log."""

import json
import os
from typing import List, Optional

from status_bus import BUS_LOG


def recent_events(limit: int = 200,
                  shell: Optional[int] = None,
                  event_types: Optional[List[str]] = None) -> List[dict]:
    """Return the most recent events from the bus log, newest first.

    Streams the log line-by-line (no offset arithmetic) and keeps the last
    ``limit`` matching entries, so the cost is bounded by the filter window
    rather than the total log size.
    """
    if not os.path.exists(BUS_LOG):
        return []
    kept: List[dict] = []
    try:
        with open(BUS_LOG, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event_types and obj.get("type") not in event_types:
                    continue
                if shell is not None and obj.get("shell") != shell:
                    continue
                kept.append(obj)
                if len(kept) > limit * 2:
                    kept = kept[-limit:]
    except OSError:
        return []
    return list(reversed(kept[-limit:]))
