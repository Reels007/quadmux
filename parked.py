"""Parked-tasks store for QuadMux.

Lightweight CRUD over a JSON file at ~/.quadmux/parked.json. Each task is:

    {
        "id": int,
        "title": str,
        "note": str,
        "status": "parked" | "blocked" | "in-progress" | "done",
        "pane": int | None,   # optional, 0-indexed
        "created": float,
        "updated": float,
    }

Tasks live alongside the bus log so they survive server restarts. This
module is sync (no asyncio) and uses a file lock via os.replace to keep
writes atomic.
"""

import json
import os
import threading
import time
from typing import Optional, List

PARKED_PATH = os.path.expanduser("~/.quadmux/parked.json")
VALID_STATUSES = {"parked", "blocked", "in-progress", "done"}

_lock = threading.Lock()


def _load_raw() -> dict:
    if not os.path.exists(PARKED_PATH):
        return {"next_id": 1, "tasks": []}
    try:
        with open(PARKED_PATH) as f:
            data = json.load(f)
        if not isinstance(data, dict) or "tasks" not in data:
            return {"next_id": 1, "tasks": []}
        return data
    except (OSError, json.JSONDecodeError):
        return {"next_id": 1, "tasks": []}


def _save_raw(data: dict) -> None:
    os.makedirs(os.path.dirname(PARKED_PATH), exist_ok=True)
    tmp = PARKED_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, PARKED_PATH)


def list_tasks() -> List[dict]:
    with _lock:
        return list(_load_raw().get("tasks", []))


def add_task(title: str, note: str = "", status: str = "parked",
             pane: Optional[int] = None) -> dict:
    title = (title or "").strip()
    if not title:
        raise ValueError("title required")
    if status not in VALID_STATUSES:
        status = "parked"
    now = time.time()
    with _lock:
        data = _load_raw()
        task = {
            "id": data.get("next_id", 1),
            "title": title[:200],
            "note": (note or "").strip()[:2000],
            "status": status,
            "pane": pane if isinstance(pane, int) else None,
            "created": now,
            "updated": now,
        }
        data["next_id"] = task["id"] + 1
        data.setdefault("tasks", []).append(task)
        _save_raw(data)
    return task


def update_task(task_id: int, **fields) -> Optional[dict]:
    allowed = {"title", "note", "status", "pane"}
    with _lock:
        data = _load_raw()
        for t in data.get("tasks", []):
            if t["id"] == task_id:
                for k, v in fields.items():
                    if k not in allowed:
                        continue
                    if k == "status" and v not in VALID_STATUSES:
                        continue
                    if k == "pane" and v is not None and not isinstance(v, int):
                        continue
                    if k == "title":
                        v = (v or "").strip()[:200]
                        if not v:
                            continue
                    if k == "note":
                        v = (v or "").strip()[:2000]
                    t[k] = v
                t["updated"] = time.time()
                _save_raw(data)
                return dict(t)
    return None


def delete_task(task_id: int) -> bool:
    with _lock:
        data = _load_raw()
        before = len(data.get("tasks", []))
        data["tasks"] = [t for t in data.get("tasks", []) if t["id"] != task_id]
        if len(data["tasks"]) == before:
            return False
        _save_raw(data)
        return True
