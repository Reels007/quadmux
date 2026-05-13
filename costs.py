"""Per-pane token + cost tracking for QuadMux.

Tails each pane's Claude Code session JSONL at ~/.claude/projects/<encoded-cwd>/.
We pick the session file whose JSON top-level ``cwd`` matches the pane's working
directory and accumulate usage from each line. Costs use the model declared on
each message, with a small lookup table per model family.

Override pricing via env vars (per million tokens, USD):
    QM_PRICE_INPUT, QM_PRICE_OUTPUT, QM_PRICE_CACHE_READ, QM_PRICE_CACHE_WRITE
"""

import json
import os
import time
from typing import Optional, List, Dict

PROJECTS_DIR = os.path.expanduser("~/.claude/projects")


# Per-million-token prices in USD. Family is matched by prefix.
PRICE_TABLE = {
    "claude-opus":   {"input": 15.0, "output": 75.0, "cache_read": 1.5,  "cache_write": 18.75},
    "claude-sonnet": {"input": 3.0,  "output": 15.0, "cache_read": 0.3,  "cache_write": 3.75},
    "claude-haiku":  {"input": 0.8,  "output": 4.0,  "cache_read": 0.08, "cache_write": 1.0},
}
DEFAULT_PRICES = PRICE_TABLE["claude-opus"]


def _override(prices: Dict[str, float]) -> Dict[str, float]:
    return {
        "input":       float(os.environ.get("QM_PRICE_INPUT",       prices["input"])),
        "output":      float(os.environ.get("QM_PRICE_OUTPUT",      prices["output"])),
        "cache_read":  float(os.environ.get("QM_PRICE_CACHE_READ",  prices["cache_read"])),
        "cache_write": float(os.environ.get("QM_PRICE_CACHE_WRITE", prices["cache_write"])),
    }


def prices_for(model: str) -> Dict[str, float]:
    model = (model or "").lower()
    for prefix, p in PRICE_TABLE.items():
        if model.startswith(prefix):
            return _override(p)
    return _override(DEFAULT_PRICES)


def compute_cost(usage: dict, model: str = "") -> float:
    p = prices_for(model)
    inp = usage.get("input_tokens", 0) or 0
    out = usage.get("output_tokens", 0) or 0
    cache_r = usage.get("cache_read_input_tokens", 0) or 0
    cache_w = usage.get("cache_creation_input_tokens", 0) or 0
    return (inp * p["input"]
            + out * p["output"]
            + cache_r * p["cache_read"]
            + cache_w * p["cache_write"]) / 1_000_000.0


def _encoded_cwd(cwd: str) -> str:
    return os.path.abspath(cwd).replace("/", "-")


def _scan_jsonl_files(proj_dir: str, target: str):
    """Yield (mtime, path) for every .jsonl in proj_dir whose first-line cwd matches."""
    if not os.path.isdir(proj_dir):
        return
    for name in os.listdir(proj_dir):
        if not name.endswith(".jsonl"):
            continue
        full = os.path.join(proj_dir, name)
        try:
            with open(full, encoding="utf-8", errors="replace") as f:
                first = f.readline()
        except OSError:
            continue
        if not first.strip():
            continue
        try:
            sess_cwd = json.loads(first).get("cwd")
        except (json.JSONDecodeError, AttributeError):
            sess_cwd = None
        if sess_cwd and os.path.abspath(sess_cwd) != target:
            continue
        if not sess_cwd:
            # File has no cwd in its first line; only accept if we already
            # know we're scanning the right dir (caller's responsibility).
            continue
        try:
            mtime = os.path.getmtime(full)
        except OSError:
            continue
        yield (mtime, full)


def session_files_for_cwd(cwd: str) -> List[str]:
    """Return absolute paths of session JSONLs whose top-level ``cwd`` matches.

    Sorted newest first (by mtime). Robust to Claude's encoding of special
    characters in path names: if the simple ``/`` → ``-`` rewrite misses, we
    fall back to scanning every project subdir.
    """
    if not cwd:
        return []
    target = os.path.abspath(cwd)
    enc = _encoded_cwd(target)
    fast_dir = os.path.join(PROJECTS_DIR, enc)
    files = list(_scan_jsonl_files(fast_dir, target))
    if not files and os.path.isdir(PROJECTS_DIR):
        # Fall back: paths with spaces/special chars get encoded differently.
        for sub in os.listdir(PROJECTS_DIR):
            sub_path = os.path.join(PROJECTS_DIR, sub)
            if sub_path == fast_dir or not os.path.isdir(sub_path):
                continue
            files.extend(_scan_jsonl_files(sub_path, target))
    files.sort(reverse=True)
    return [f[1] for f in files]


def assign_session_files(pane_cwds: List[str]) -> List[Optional[str]]:
    """Pick a session file for each pane. Panes sharing a cwd get distinct
    files (newest first); cwds with fewer files than panes get None for the
    excess panes."""
    by_cwd = {}
    for i, cwd in enumerate(pane_cwds):
        by_cwd.setdefault(cwd or "", []).append(i)
    out: List[Optional[str]] = [None] * len(pane_cwds)
    for cwd, idxs in by_cwd.items():
        files = session_files_for_cwd(cwd)
        for pane_idx, file in zip(idxs, files):
            out[pane_idx] = file
    return out


class CostTracker:
    """Tails a single session JSONL and accumulates usage + cost."""

    def __init__(self, path: Optional[str] = None):
        self.path = path
        self._pos = 0
        self.last_model = ""
        self.tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        self.cost = 0.0
        self.last_update = 0.0

    def attach(self, path: str):
        self.path = path
        self._pos = 0
        self.tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        self.cost = 0.0
        self.last_model = ""

    def poll(self) -> bool:
        """Read any new bytes appended to ``self.path``. Returns True if totals changed."""
        if not self.path or not os.path.exists(self.path):
            return False
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return False
        if size < self._pos:
            # File was rotated or replaced; restart from the beginning.
            self._pos = 0
        if size == self._pos:
            return False
        with open(self.path, "rb") as f:
            f.seek(self._pos)
            data = f.read()
            self._pos = f.tell()
        if not data:
            return False
        changed = False
        leftover = b""
        for raw in data.split(b"\n"):
            if not raw.strip():
                continue
            try:
                line = raw.decode("utf-8")
                obj = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                # Could be a half-written line at EOF; keep for next poll.
                leftover = raw
                continue
            msg = obj.get("message") or {}
            usage = msg.get("usage") if isinstance(msg, dict) else None
            if not isinstance(usage, dict):
                continue
            model = (msg.get("model") if isinstance(msg, dict) else "") or self.last_model
            self.last_model = model or self.last_model
            self.tokens["input"]       += usage.get("input_tokens", 0) or 0
            self.tokens["output"]      += usage.get("output_tokens", 0) or 0
            self.tokens["cache_read"]  += usage.get("cache_read_input_tokens", 0) or 0
            self.tokens["cache_write"] += usage.get("cache_creation_input_tokens", 0) or 0
            self.cost += compute_cost(usage, model)
            changed = True
        if leftover:
            # Rewind so we re-read the partial line next time.
            self._pos -= len(leftover) + 1  # +1 for the newline that wasn't a real terminator
            self._pos = max(self._pos, 0)
        if changed:
            self.last_update = time.time()
        return changed

    def snapshot(self) -> dict:
        return {
            "tokens": dict(self.tokens),
            "total_tokens": sum(self.tokens.values()),
            "cost": round(self.cost, 4),
            "model": self.last_model,
        }
