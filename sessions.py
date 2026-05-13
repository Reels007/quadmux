"""Per-run session archive and replay support for QuadMux.

Each QuadMux launch gets its own subdirectory under ``~/.quadmux/sessions/<id>/``
where ``<id>`` is a timestamp like ``20260513-104530``. Inside that dir:

    shell_N.json   - autosaved PTY buffer for pane N
    meta.json      - {started_at, ended_at, pane_count, preset, repo, pane_meta}

Past sessions stay on disk for replay. On startup, QuadMux loads buffers from
the most recent previous session so the user has continuity across restarts.
"""

import json
import os
import time
from typing import List, Optional

import status_bus

SESSIONS_ROOT = os.path.expanduser("~/.quadmux/sessions")
META_FILENAME = "meta.json"


def make_session_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def session_path(session_id: str) -> str:
    return os.path.join(SESSIONS_ROOT, session_id)


def list_session_dirs() -> List[str]:
    """All session subdirs (newest first by name)."""
    if not os.path.isdir(SESSIONS_ROOT):
        return []
    out = []
    for name in os.listdir(SESSIONS_ROOT):
        full = os.path.join(SESSIONS_ROOT, name)
        if os.path.isdir(full):
            out.append(full)
    out.sort(reverse=True)
    return out


def previous_session_dir(skip_id: Optional[str] = None) -> Optional[str]:
    """Most recent session dir, optionally skipping the active one."""
    for d in list_session_dirs():
        if skip_id and os.path.basename(d) == skip_id:
            continue
        return d
    return None


def write_meta(session_dir: str, meta: dict) -> None:
    os.makedirs(session_dir, exist_ok=True)
    path = os.path.join(session_dir, META_FILENAME)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(meta, f, indent=2)
    os.replace(tmp, path)


def read_meta(session_dir: str) -> dict:
    p = os.path.join(session_dir, META_FILENAME)
    if not os.path.exists(p):
        return {}
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def update_meta(session_dir: str, **fields) -> None:
    meta = read_meta(session_dir)
    meta.update(fields)
    write_meta(session_dir, meta)


def list_sessions() -> List[dict]:
    out = []
    for d in list_session_dirs():
        meta = read_meta(d)
        out.append({
            "id": os.path.basename(d),
            "started_at": meta.get("started_at"),
            "ended_at": meta.get("ended_at"),
            "pane_count": meta.get("pane_count", 0),
            "preset": meta.get("preset"),
            "repo": meta.get("repo"),
            "pane_meta": meta.get("pane_meta", []),
        })
    return out


def load_buffers(session_dir: str, num_shells: int) -> List[list]:
    """Load shell buffers from a session dir. Returns empty lists for missing panes."""
    out: List[list] = [[] for _ in range(num_shells)]
    if not os.path.isdir(session_dir):
        return out
    for i in range(num_shells):
        p = os.path.join(session_dir, f"shell_{i}.json")
        if not os.path.exists(p):
            continue
        try:
            with open(p) as f:
                data = json.load(f)
            buf = data.get("buffer", [])
            if isinstance(buf, list):
                out[i] = buf
        except (OSError, json.JSONDecodeError, ValueError):
            pass
    return out


def filter_bus_events(started_at: Optional[float],
                      ended_at: Optional[float] = None) -> List[dict]:
    """Return bus events that fall within [started_at, ended_at]. Newest first."""
    if not os.path.exists(status_bus.BUS_LOG):
        return []
    end = ended_at if ended_at is not None else time.time() + 1
    out: List[dict] = []
    try:
        with open(status_bus.BUS_LOG, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = obj.get("ts", 0)
                if started_at is not None and ts < started_at:
                    continue
                if ts > end:
                    continue
                out.append(obj)
    except OSError:
        return []
    return list(reversed(out))


def load_replay(session_id: str, num_shells_fallback: int = 4) -> dict:
    """Return a serializable replay payload for a given session id."""
    d = session_path(session_id)
    if not os.path.isdir(d):
        return {"id": session_id, "found": False, "buffers": [], "events": [], "meta": {}}
    meta = read_meta(d)
    pane_count = meta.get("pane_count") or num_shells_fallback
    buffers = load_buffers(d, pane_count)
    events = filter_bus_events(meta.get("started_at"), meta.get("ended_at"))
    return {
        "id": session_id,
        "found": True,
        "meta": meta,
        "buffers": buffers,
        "events": events,
    }
