"""Status bus for QuadMux v2.

Tracks per-pane Claude Code state by pattern-matching PTY output. Emits state
change events to subscribers and appends to ~/.quadmux/bus.jsonl.

States:
    idle                - prompt visible, no recent activity
    thinking            - Claude is generating a response
    tool_running        - Claude invoked a tool (Read/Bash/Edit/etc.)
    awaiting_permission - Claude is waiting for y/n approval
    errored             - last activity was an error
    unknown             - default before any signal is seen
"""

import json
import os
import re
import time

BUS_LOG = os.path.expanduser("~/.quadmux/bus.jsonl")
BUS_LOG_MAX_BYTES = 50 * 1024 * 1024  # 50 MB cap

ANSI_RE = re.compile(r'\x1b(?:\[[0-9;]*[a-zA-Z]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[()][0-2AB]|[>=<78DEHM])')

SPINNER_CHARS = set("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")
TOOL_RE = re.compile(r'●\s*(Read|Write|Edit|Bash|Glob|Grep|Task|WebFetch|WebSearch|TodoWrite)\s*\(')
PERMISSION_RE = re.compile(
    r'(Do you want to|\[y/n\]|\(y/n\)|\bAllow\b.*\bDeny\b|❯.*\b1\.\s*Yes\b)',
    re.IGNORECASE,
)
ERROR_RE = re.compile(r'(?:^|\s)(Error:|Traceback|✗\s|failed:)', re.IGNORECASE)
PROMPT_TAIL_RE = re.compile(r'[❯>]\s*$')

IDLE_AFTER_SECONDS = 2.0  # decay to idle after this much quiet time with a visible prompt


def _clean(text: str) -> str:
    return ANSI_RE.sub('', text)


def detect_state(text: str, prior_state: str) -> str | None:
    """Return new state if a transition is detected, else None.

    Operates on a single chunk of PTY output. The caller should also call
    `tick()` periodically to decay to idle.
    """
    clean = _clean(text)
    tail = clean[-400:]

    if PERMISSION_RE.search(tail):
        return "awaiting_permission"
    if ERROR_RE.search(tail):
        return "errored"
    if TOOL_RE.search(tail):
        return "tool_running"
    if any(c in tail for c in SPINNER_CHARS):
        return "thinking"
    if PROMPT_TAIL_RE.search(tail.rstrip()):
        # Prompt visible. Only flip to idle from "thinking"/"tool_running" if
        # there's no spinner. Otherwise leave the prior state; tick() handles
        # the eventual decay.
        if prior_state in ("thinking", "tool_running", "unknown"):
            return "idle"
    return None


class StatusBus:
    """Tracks state per shell and notifies subscribers on changes."""

    def __init__(self, num_shells: int):
        self.num_shells = num_shells
        self.states = ["unknown"] * num_shells
        self.last_activity = [0.0] * num_shells
        self.last_prompt_seen = [0.0] * num_shells
        self.subscribers = []  # list of async callables: (event_dict) -> coroutine
        os.makedirs(os.path.dirname(BUS_LOG), exist_ok=True)

    def subscribe(self, fn):
        self.subscribers.append(fn)

    def snapshot(self):
        return [
            {"shell": i, "state": self.states[i], "ts": self.last_activity[i]}
            for i in range(self.num_shells)
        ]

    def update(self, shell_idx: int, text: str):
        """Feed a chunk of PTY output. Returns the new state if it changed, else None."""
        if not (0 <= shell_idx < self.num_shells):
            return None
        prior = self.states[shell_idx]
        new_state = detect_state(text, prior)
        now = time.time()

        clean_tail = _clean(text)[-200:].rstrip()
        if PROMPT_TAIL_RE.search(clean_tail):
            self.last_prompt_seen[shell_idx] = now

        if new_state and new_state != prior:
            self.states[shell_idx] = new_state
            self.last_activity[shell_idx] = now
            self._log({"type": "state", "shell": shell_idx,
                       "from": prior, "to": new_state, "ts": now})
            return new_state
        # Update activity timestamp on any meaningful signal even if state unchanged
        if new_state:
            self.last_activity[shell_idx] = now
        return None

    def tick(self):
        """Decay stale 'thinking'/'tool_running' states to 'idle' if a prompt
        was seen and there's been no activity for IDLE_AFTER_SECONDS. Returns
        a list of state changes that occurred this tick."""
        now = time.time()
        changes = []
        for i in range(self.num_shells):
            state = self.states[i]
            if state in ("thinking", "tool_running"):
                quiet = now - self.last_activity[i]
                had_prompt = self.last_prompt_seen[i] >= self.last_activity[i]
                if quiet >= IDLE_AFTER_SECONDS and had_prompt:
                    self.states[i] = "idle"
                    self.last_activity[i] = now
                    evt = {"type": "state", "shell": i, "from": state,
                           "to": "idle", "ts": now, "reason": "decay"}
                    self._log(evt)
                    changes.append(evt)
        return changes

    def mark_dead(self, shell_idx: int):
        """Mark a shell as errored because its child process died."""
        if not (0 <= shell_idx < self.num_shells):
            return
        prior = self.states[shell_idx]
        if prior == "errored":
            return
        now = time.time()
        self.states[shell_idx] = "errored"
        self.last_activity[shell_idx] = now
        self._log({"type": "state", "shell": shell_idx, "from": prior,
                   "to": "errored", "ts": now, "reason": "dead"})

    def _log(self, event: dict):
        try:
            if os.path.exists(BUS_LOG) and os.path.getsize(BUS_LOG) > BUS_LOG_MAX_BYTES:
                rotated = BUS_LOG + ".1"
                try:
                    os.replace(BUS_LOG, rotated)
                except OSError:
                    pass
            with open(BUS_LOG, "a") as f:
                f.write(json.dumps(event) + "\n")
        except OSError:
            pass
