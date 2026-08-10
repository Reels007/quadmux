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


CWD_SCAN_LINES = 20  # Claude writes header records (last-prompt, mode, ...) before any cwd


def _scan_jsonl_files(proj_dir: str, target: str):
    """Yield (mtime, path) for every .jsonl in proj_dir whose session cwd matches."""
    if not os.path.isdir(proj_dir):
        return
    for name in os.listdir(proj_dir):
        if not name.endswith(".jsonl"):
            continue
        full = os.path.join(proj_dir, name)
        sess_cwd = None
        try:
            with open(full, encoding="utf-8", errors="replace") as f:
                for _ in range(CWD_SCAN_LINES):
                    line = f.readline()
                    if not line:
                        break
                    try:
                        val = json.loads(line).get("cwd")
                    except (json.JSONDecodeError, AttributeError):
                        continue
                    if val:
                        sess_cwd = val
                        break
        except OSError:
            continue
        if not sess_cwd or os.path.abspath(sess_cwd) != target:
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


def session_file_for_id(cwd: str, session_id: str) -> Optional[str]:
    """Path of <session_id>.jsonl for the given cwd, or None if not written yet."""
    if not session_id:
        return None
    name = f"{session_id}.jsonl"
    if cwd:
        fast = os.path.join(PROJECTS_DIR, _encoded_cwd(cwd), name)
        if os.path.exists(fast):
            return fast
    if os.path.isdir(PROJECTS_DIR):
        # Encoded-path mismatch fallback: scan every project subdir.
        for sub in os.listdir(PROJECTS_DIR):
            full = os.path.join(PROJECTS_DIR, sub, name)
            if os.path.exists(full):
                return full
    return None


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


# Context window every Claude Code pane runs with. All current families are
# 200k by default (the 1M Sonnet window needs an explicit beta variant Claude
# Code doesn't use), so one number covers every pane; override for a pane set
# running something else with QM_CONTEXT_LIMIT.
DEFAULT_CONTEXT_LIMIT = 200_000


def context_limit_for(model: str) -> int:
    """Context window size in tokens for ``model``."""
    try:
        override = int(os.environ.get("QM_CONTEXT_LIMIT", "") or 0)
    except ValueError:
        override = 0
    return override if override > 0 else DEFAULT_CONTEXT_LIMIT


def context_used(usage: dict) -> int:
    """Prompt size of one assistant message: everything on the input side.

    This is what the pane's context meter reports. It excludes the message's
    own output tokens, which only enter the context on the next turn, so the
    figure lags a live reply by one turn.
    """
    return ((usage.get("input_tokens", 0) or 0)
            + (usage.get("cache_read_input_tokens", 0) or 0)
            + (usage.get("cache_creation_input_tokens", 0) or 0))


TASK_MAX_CHARS = 48


def _task_from_obj(obj: dict) -> Optional[str]:
    """Short task description from a session-JSONL user line, or None.

    Takes the user's typed prompt (not tool results, harness reminders, or
    command stdout) and truncates it to a pane-title-sized summary.
    """
    if obj.get("type") != "user" or obj.get("isMeta"):
        return None
    msg = obj.get("message")
    if not isinstance(msg, dict) or msg.get("role") != "user":
        return None
    content = msg.get("content")
    if isinstance(content, list):
        if any(isinstance(c, dict) and c.get("type") == "tool_result" for c in content):
            return None
        content = " ".join(c.get("text", "") for c in content
                           if isinstance(c, dict) and c.get("type") == "text")
    if not isinstance(content, str):
        return None
    text = content.strip()
    if (not text or text.startswith("<") or text.startswith("Caveat:")
            or "<command-name>" in text or "<task-notification>" in text):
        return None
    line = next((l.strip() for l in text.splitlines() if l.strip()), "")
    line = " ".join(line.split())
    if not line:
        return None
    if len(line) > TASK_MAX_CHARS:
        line = line[:TASK_MAX_CHARS - 3].rstrip() + "..."
    return line


class CostTracker:
    """Tails a single session JSONL and accumulates usage + cost."""

    def __init__(self, path: Optional[str] = None):
        self.path = path
        self._pos = 0
        self.last_model = ""
        self.tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        self.cost = 0.0
        self.last_update = 0.0
        self.task = ""
        # Prompt size of the newest main-thread reply, not a running total:
        # it falls back down after a /compact or /clear.
        self.context_tokens = 0

    def attach(self, path: str):
        self.path = path
        self._pos = 0
        self.tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        self.cost = 0.0
        self.last_model = ""
        self.task = ""
        self.context_tokens = 0

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
            task = _task_from_obj(obj)
            if task:
                self.task = task
                changed = True
            msg = obj.get("message") or {}
            usage = msg.get("usage") if isinstance(msg, dict) else None
            if not isinstance(usage, dict):
                continue
            model = (msg.get("model") if isinstance(msg, dict) else "") or self.last_model
            self.last_model = model or self.last_model
            # Context meter tracks the main thread only: subagent replies
            # (isSidechain) carry their own separate context.
            if obj.get("type") == "assistant" and not obj.get("isSidechain"):
                ctx = context_used(usage)
                if ctx:
                    self.context_tokens = ctx
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
        limit = context_limit_for(self.last_model)
        return {
            "tokens": dict(self.tokens),
            "total_tokens": sum(self.tokens.values()),
            "cost": round(self.cost, 4),
            "model": self.last_model,
            "task": self.task,
            "context_tokens": self.context_tokens,
            "context_limit": limit,
            "context_pct": round(100.0 * self.context_tokens / limit, 1) if limit else 0.0,
        }
