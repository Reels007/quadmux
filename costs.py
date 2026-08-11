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


# Per-million-token prices in USD. Family is matched by LONGEST prefix, so a
# pinned id ("claude-opus-5") wins over its family fallback ("claude-opus").
# Cache writes are billed by TTL: 1.25x base input for the 5-minute cache,
# 2x for the 1-hour cache. Claude Code sessions normally use the 1h TTL, so
# "cache_write" (the flat fallback used when the JSONL doesn't break the
# creation tokens down by TTL) is set to the 1h rate.
PRICE_TABLE = {
    # Opus 5 / 4.8 are $5 / $25 - a third of the old Opus 3-era $15 / $75.
    "claude-opus-5":   {"input": 5.0,  "output": 25.0, "cache_read": 0.5,
                        "cache_write": 10.0, "cache_write_5m": 6.25},
    "claude-opus-4-8": {"input": 5.0,  "output": 25.0, "cache_read": 0.5,
                        "cache_write": 10.0, "cache_write_5m": 6.25},
    "claude-opus":     {"input": 5.0,  "output": 25.0, "cache_read": 0.5,
                        "cache_write": 10.0, "cache_write_5m": 6.25},
    "claude-fable":    {"input": 10.0, "output": 50.0, "cache_read": 1.0,
                        "cache_write": 20.0, "cache_write_5m": 12.5},
    "claude-sonnet":   {"input": 3.0,  "output": 15.0, "cache_read": 0.3,
                        "cache_write": 6.0,  "cache_write_5m": 3.75},
    "claude-haiku":    {"input": 1.0,  "output": 5.0,  "cache_read": 0.1,
                        "cache_write": 2.0,  "cache_write_5m": 1.25},
}
DEFAULT_PRICES = PRICE_TABLE["claude-opus"]


def _override(prices: Dict[str, float]) -> Dict[str, float]:
    write = float(os.environ.get("QM_PRICE_CACHE_WRITE", prices["cache_write"]))
    return {
        "input":       float(os.environ.get("QM_PRICE_INPUT",       prices["input"])),
        "output":      float(os.environ.get("QM_PRICE_OUTPUT",      prices["output"])),
        "cache_read":  float(os.environ.get("QM_PRICE_CACHE_READ",  prices["cache_read"])),
        "cache_write": write,
        # A QM_PRICE_CACHE_WRITE override replaces both TTL rates, so a single
        # env var still fully controls cache-write pricing.
        "cache_write_5m": write if "QM_PRICE_CACHE_WRITE" in os.environ
        else float(prices.get("cache_write_5m", prices["cache_write"])),
    }


def prices_for(model: str) -> Dict[str, float]:
    model = (model or "").lower()
    best = None
    for prefix, p in PRICE_TABLE.items():
        if model.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, p)
    return _override(best[1] if best else DEFAULT_PRICES)


def compute_cost(usage: dict, model: str = "") -> float:
    p = prices_for(model)
    inp = usage.get("input_tokens", 0) or 0
    out = usage.get("output_tokens", 0) or 0
    cache_r = usage.get("cache_read_input_tokens", 0) or 0
    cache_w = usage.get("cache_creation_input_tokens", 0) or 0

    # Split the creation tokens by TTL when the message reports the breakdown;
    # anything unaccounted for falls back to the 1h rate.
    breakdown = usage.get("cache_creation")
    w_5m = 0
    if isinstance(breakdown, dict):
        w_5m = breakdown.get("ephemeral_5m_input_tokens", 0) or 0
        w_5m = min(w_5m, cache_w)
    w_1h = cache_w - w_5m

    return (inp * p["input"]
            + out * p["output"]
            + cache_r * p["cache_read"]
            + w_1h * p["cache_write"]
            + w_5m * p["cache_write_5m"]) / 1_000_000.0


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


# Context window every Claude Code pane runs with. 200k unless the model
# family is known to ship a bigger window (Fable panes have produced 892k
# prompts in real session files, so 200k is provably wrong there). A pane
# whose observed prompt exceeds its nominal limit gets bumped to the big
# window rather than pinning the meter at 100%: a >limit reading is proof
# the nominal number is wrong, and overshooting the denominator only ever
# UNDER-states pressure. Override per deployment with QM_CONTEXT_LIMIT.
DEFAULT_CONTEXT_LIMIT = 200_000
BIG_CONTEXT_LIMIT = 1_000_000
MODEL_CONTEXT_LIMITS = {
    "claude-fable": BIG_CONTEXT_LIMIT,
}


def context_limit_for(model: str, seen_tokens: int = 0) -> int:
    """Context window size in tokens for ``model``.

    ``seen_tokens`` is the largest prompt actually observed; a value above
    the nominal limit promotes the pane to the big window.
    """
    try:
        override = int(os.environ.get("QM_CONTEXT_LIMIT", "") or 0)
    except ValueError:
        override = 0
    if override > 0:
        return override
    model = (model or "").lower()
    limit = DEFAULT_CONTEXT_LIMIT
    for prefix, val in MODEL_CONTEXT_LIMITS.items():
        if model.startswith(prefix):
            limit = val
            break
    if seen_tokens > limit:
        limit = BIG_CONTEXT_LIMIT
    return limit


def context_used(usage: dict) -> int:
    """Prompt size of one assistant message: everything on the input side.

    This is what the pane's context meter reports. It excludes the message's
    own output tokens, which only enter the context on the next turn, so the
    figure lags a live reply by one turn.
    """
    return ((usage.get("input_tokens", 0) or 0)
            + (usage.get("cache_read_input_tokens", 0) or 0)
            + (usage.get("cache_creation_input_tokens", 0) or 0))


def _sid_from_path(path: Optional[str]) -> str:
    """Session id encoded in a JSONL filename ('' if no path)."""
    if not path:
        return ""
    name = os.path.basename(path)
    return name[:-6] if name.endswith(".jsonl") else name


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
        # Every session id this pane has lived through. `claude --resume` and
        # /clear both roll the conversation into a NEW <sid>.jsonl; the chain
        # is what lets find_rebinds() recognise a fork of any earlier life.
        self.session_ids = [s for s in [_sid_from_path(path)] if s]
        self.attached_at = time.time() if path else 0.0
        # Wall time we last saw a main-thread assistant reply land in the
        # attached file. A file that stops producing these is a dead fork
        # parent, however fresh its mtime looks (Claude keeps appending
        # untimestamped ai-title/last-prompt metadata to abandoned files).
        self.last_assistant_time = 0.0

    def attach(self, path: str, from_end: bool = False, keep_totals: bool = False):
        """Point the tracker at ``path``.

        ``from_end`` skips content already in the file: forked session files
        start with a full copy of the parent history, whose replayed usage
        lines would double-count cost and resurrect a stale context reading.
        ``keep_totals`` preserves the pane's cumulative cost across a
        re-attach, since a fork or /clear is the same pane's spend.
        """
        self.path = path
        self._pos = os.path.getsize(path) if from_end and os.path.exists(path) else 0
        if keep_totals:
            sid = _sid_from_path(path)
            if sid:
                self.session_ids.append(sid)
        else:
            self.tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
            self.cost = 0.0
            self.last_model = ""
            self.task = ""
            self.session_ids = [s for s in [_sid_from_path(path)] if s]
        self.context_tokens = 0
        self.attached_at = time.time()
        self.last_assistant_time = 0.0

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
                    self.last_assistant_time = time.time()
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
        limit = context_limit_for(self.last_model, self.context_tokens)
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


# ---------------------------------------------------------------------------
# Session re-binding: following a pane's conversation across file rolls.
#
# A pane's session JSONL is NOT stable for the life of the pane. Two things
# roll it to a brand-new <new-sid>.jsonl in the same project dir:
#   * `claude --resume <sid>` forks the resumed conversation immediately;
#   * /clear starts a fresh session.
# The abandoned file keeps getting untimestamped metadata appends (ai-title,
# last-prompt), so mtime alone cannot tell it is dead. A tracker left on it
# freezes at the last pre-roll context reading (seen live: a pane pinned at
# 100% off an 892k line from two weeks earlier).
#
# Fork files are recognised by content: their copied history retains the
# parent's session id on every replayed line. /clear files carry no parent
# marker, but their first user line embeds the /clear command stdout, and the
# pane that issued it typed something seconds before the file was born, so
# recency of pane input disambiguates panes sharing a cwd.
# ---------------------------------------------------------------------------

REBIND_HEAD_BYTES = 262_144   # enough to cover copied-history lines with the parent sid
REBIND_SCAN_WINDOW = 1_800.0  # only consider files born in the last 30 min
REBIND_SETTLE_SECS = 2.0      # let a new file finish its history copy before judging
REBIND_GRACE_SECS = 60.0      # unmatched young files get re-judged, not written off:
                              # a fork can appear before its pane's initial attach
CLEAR_INPUT_WINDOW = 90.0     # pane must have typed within this window before a /clear file
CLEAR_MARKER = b"<command-name>/clear</command-name>"


def _head_bytes(path: str, limit: int = REBIND_HEAD_BYTES) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read(limit)
    except OSError:
        return b""


CLEAR_MARKER_SCAN_LINES = 40  # a genuine /clear caveat sits in the first few lines


def _sid_field_patterns(sid: str):
    """Byte patterns of ``sid`` appearing as a structural sessionId field.

    Matching the bare uuid is not safe: a conversation that merely DISCUSSES
    another session (pasted logs, debugging output) embeds the uuid as text.
    Text lives inside JSON strings where quotes are backslash-escaped, so an
    unescaped "sessionId":"<sid>" can only be a real top-level field.
    """
    s = sid.encode()
    return (b'"sessionId":"' + s + b'"', b'"sessionId": "' + s + b'"')


def _is_clear_head(head: bytes) -> bool:
    """True if ``head`` opens a session file started by a /clear.

    Only the first lines count: the caveat line of a genuine /clear session
    is written at the very top, while a marker deeper in the file is just
    conversation that happens to quote one.
    """
    return CLEAR_MARKER in b"\n".join(head.split(b"\n")[:CLEAR_MARKER_SCAN_LINES])


def is_clear_session(path: str) -> bool:
    """True if ``path`` is a session file started by a /clear."""
    return _is_clear_head(_head_bytes(path))


def find_rebinds(trackers, pane_last_input, known_paths, now=None):
    """Decide which panes should follow their conversation to a new file.

    ``trackers`` is the per-pane CostTracker list (None entries allowed),
    ``pane_last_input`` the wall time each pane last received keyboard input,
    ``known_paths`` a set of already-judged files, mutated here so each new
    file is judged exactly once. Returns [(pane_idx, path, from_end)].
    """
    now = now if now is not None else time.time()
    attached = {t.path for t in trackers if t and t.path}
    proj_dirs = {os.path.dirname(p) for p in attached}
    rebinds = {}
    candidates = []
    for d in proj_dirs:
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for name in names:
            if not name.endswith(".jsonl"):
                continue
            full = os.path.join(d, name)
            if full in known_paths or full in attached:
                continue
            try:
                mtime = os.path.getmtime(full)
            except OSError:
                continue
            if now - mtime > REBIND_SCAN_WINDOW:
                known_paths.add(full)  # pre-dates the window: never ours
                continue
            if now - mtime < REBIND_SETTLE_SECS:
                continue  # still being written; re-judge next scan
            candidates.append((mtime, full))
    # Oldest first, so a fork followed by a /clear lands on the /clear file.
    for mtime, full in sorted(candidates):
        head = _head_bytes(full)
        resolved = False
        # Fork of a pane we track? Copied history retains the parent sid.
        # Checked before the /clear marker: a fork of a post-clear session
        # contains BOTH markers, and lineage is the stronger signal.
        for i, t in enumerate(trackers):
            if t and t.path and any(p in head for s in t.session_ids
                                    for p in _sid_field_patterns(s)):
                rebinds[i] = (full, True)
                resolved = True
                break
        if not resolved and _is_clear_head(head):
            # A /clear start: give it to the pane that typed just before the
            # file was born and whose current file has produced nothing since.
            try:
                born = os.path.getctime(full)
            except OSError:
                born = mtime
            best = None
            for i, t in enumerate(trackers):
                if not t or not t.path:
                    continue
                typed = pane_last_input[i] if i < len(pane_last_input) else 0.0
                if born - CLEAR_INPUT_WINDOW <= typed <= born + REBIND_SETTLE_SECS \
                        and t.last_assistant_time < born:
                    if best is None or typed > pane_last_input[best]:
                        best = i
            if best is not None:
                rebinds[best] = (full, False)
                resolved = True
        # Unmatched young files stay unjudged: their pane may not have its
        # initial attach yet. Old unmatched files are someone else's session.
        if resolved or now - mtime >= REBIND_GRACE_SECS:
            known_paths.add(full)
    return [(i, p, from_end) for i, (p, from_end) in rebinds.items()]
