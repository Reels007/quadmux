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
BOX_RE = re.compile(r'[─│┌┐└┘├┤┬┴┼╭╮╯╰▓░▒█]')

# Claude Code 2.x animates the "working" status line through a set of sparkle
# glyphs (NOT the old braille spinner) and always prints the ASCII string
# "esc to interrupt" on that line the whole time it is thinking or running a
# tool. The ASCII marker is the robust busy signal; the glyphs are a backup
# for when it scrolls just out of the tail window.
SPINNER_CHARS = set("✶✻✽✳✢✦")
BUSY_RE = re.compile(r'esc to interrupt')
# Tool activity markers. 2.x renders a tool call as a "⏺ <verb>" header
# ("⏺ Read(…)", "⏺ Running 1 shell command…", "⏺ Update(…)") followed by a
# "⎿" result/continuation line. The ⎿ marker is emitted for every tool and is
# the most reliable signal; the verb-header list catches the moment the header
# prints before the result line lands.
TOOL_RE = re.compile(
    r'⎿'
    r'|⏺\s*(?:Read|Write|Edit|Bash|Running|Search|Fetch|Update|Create|Delete|'
    r'List|Glob|Grep|Task|Web|Todo|Call|Wait|Add|Remove|Fetching|Searching)'
)
# Prose is NOT enough: the user's typed text and Claude's own replies echo
# through the PTY and can contain "Do you want to proceed" or "1. Yes"
# verbatim (e.g. a conversation ABOUT permission prompts). Only the selected
# menu row a real dialog renders ("❯ 1. Yes") or a y/n marker counts.
PERMISSION_RE = re.compile(
    r'(❯\s*1\.\s*Yes\b'
    # y/n markers only count at end of line (a real prompt awaits input there);
    # "(y/n)" quoted mid-sentence in chat prose must not match.
    r'|[\[\(]y/n[\]\)]\s*[:>]?\s*$'
    r'|\bAllow\b.{0,120}?\bDeny\b)',
    re.IGNORECASE | re.DOTALL | re.MULTILINE,
)
ERROR_RE = re.compile(r'(?:^|\s)(Error:|Traceback|✗\s|failed:)', re.IGNORECASE)
PROMPT_TAIL_RE = re.compile(r'[❯>]\s*$')

IDLE_AFTER_SECONDS = 2.0  # decay to idle after this much quiet time with a visible prompt

TAIL_BUFFER_BYTES = 8192  # raw PTY output retained per shell across chunks

# Keystrokes that resolve a Claude Code permission dialog when typed at the
# pane: option digits, y/n (legacy prompts), Enter (accept highlighted), Esc
# (cancel). A bare ESC counts; CSI sequences (arrows, page keys) do not.
_ANSWER_CHARS = set("123456789yYnN")


def is_dialog_answer_key(data: str) -> bool:
    if data in ("\r", "\n", "\x1b"):
        return True
    return len(data) == 1 and data in _ANSWER_CHARS


def extract_permission_question(text: str) -> str:
    """Pull the question line from a Claude Code permission prompt block.

    Strips ANSI + box-drawing, then scans the tail in priority order:
        1. lines containing 'do you want'
        2. lines containing 'allow' / 'approve' / 'permission'
        3. lines containing '[y/n]' / '(y/n)'
    Returns up to ~140 chars, or '' if nothing usable.
    """
    # Replace ANSI with a space (not ''): cursor-positioning sequences often
    # stand in for the whitespace between words, so bare stripping glues words
    # together ("fromthetargetdirectory").
    clean = BOX_RE.sub(' ', ANSI_RE.sub(' ', text))
    # Terminal redraws interleave \r segments mid-line; treat them as line
    # breaks so fragments don't concatenate into garbage.
    clean = clean.replace('\r', '\n')
    lines = [re.sub(r'[ \t]{2,}', ' ', ln.strip()) for ln in clean.split('\n')]
    tail = [ln for ln in lines if ln][-30:]

    for ln in reversed(tail):
        if 'do you want' in ln.lower():
            return ln[:140]
    for ln in reversed(tail):
        low = ln.lower()
        if 'allow' in low or 'approve' in low or 'permission' in low:
            return ln[:140]
    for ln in reversed(tail):
        low = ln.lower()
        if '[y/n]' in low or '(y/n)' in low:
            return ln[:140]
    return ''


def extract_dialog_block(text: str) -> str:
    """Return just the permission dialog block from raw PTY output.

    Claude Code draws the dialog as the last rounded box on screen (tool name,
    command, question, options). Classifying only this block stops red/green
    patterns matching stray conversation text in the scrollback (e.g. "send
    the email" in chat prose forcing every prompt into the red band).
    """
    clean = ANSI_RE.sub(' ', text).replace('\r', '\n')
    start = clean.rfind('╭')
    block = clean[start:] if start != -1 else clean[-800:]
    block = BOX_RE.sub(' ', block)
    return re.sub(r'[ \t]{2,}', ' ', block)[:1200]


def _clean(text: str) -> str:
    return ANSI_RE.sub('', text)


def detect_state(text: str, prior_state: str) -> str | None:
    """Return new state if a transition is detected, else None.

    `StatusBus.update` passes a rolling per-shell buffer, not a single chunk:
    dialogs render across multiple PTY reads and the "❯ 1. Yes" row can be
    split mid-pattern at a chunk boundary. The caller should also call
    `tick()` periodically to decay to idle.
    """
    clean = _clean(text)
    tail = clean[-400:]

    # Permission dialogs are tall: the option rows, bottom border and hint
    # line rendered AFTER the "❯ 1. Yes" row can exceed 400 chars on wide
    # panes, so permissions get a wider window than the activity signals.
    if PERMISSION_RE.search(clean[-1200:]):
        return "awaiting_permission"

    # Is Claude actively working? "esc to interrupt" (or a sparkle glyph in the
    # live bottom region) is present the entire time it thinks or runs a tool,
    # and vanishes the instant it returns to the prompt. Gating tool_running /
    # thinking behind this is what stops a *completed* tool's "⎿" line, still
    # sitting in the transcript at idle, from pinning the badge to a busy state.
    busy = BUSY_RE.search(tail) is not None or any(c in tail[-200:] for c in SPINNER_CHARS)
    if busy:
        # Running a tool vs pure reasoning: a live tool call shows its header /
        # "⎿" marker in the bottom region. Look only at the last ~300 chars so
        # an earlier tool result higher up in this turn doesn't win over the
        # current thinking frame.
        if TOOL_RE.search(tail[-300:]):
            return "tool_running"
        return "thinking"

    if ERROR_RE.search(tail):
        return "errored"
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
        self.permissions = [None] * num_shells  # current open request per shell: dict or None
        # Rolling raw-output tail per shell so detection sees across chunk
        # boundaries (also lets ANSI sequences split mid-escape re-join).
        self._tails = [""] * num_shells
        self._next_request_id = 1
        self.subscribers = []  # list of async callables: (event_dict) -> coroutine
        os.makedirs(os.path.dirname(BUS_LOG), exist_ok=True)

    def open_permission(self, shell_idx: int, question: str) -> dict:
        """Record a new pending permission request and return it."""
        req = {
            "id": self._next_request_id,
            "shell": shell_idx,
            "question": question or "Permission requested",
            "ts": time.time(),
        }
        self._next_request_id += 1
        self.permissions[shell_idx] = req
        self._log({"type": "permission_request", **req})
        return req

    def close_permission(self, shell_idx: int, reason: str = "resolved"):
        """Close any open permission request for this shell. Returns the closed
        request dict, or None if none was open."""
        if not (0 <= shell_idx < self.num_shells):
            return None
        req = self.permissions[shell_idx]
        if req is None:
            return None
        self.permissions[shell_idx] = None
        # Drop buffered output: the closed dialog's "❯ 1. Yes" text must not
        # re-match on the next chunk and open a phantom request.
        self._tails[shell_idx] = ""
        self._log({"type": "permission_resolved", "id": req["id"],
                   "shell": shell_idx, "reason": reason, "ts": time.time()})
        return req

    def resolve_dialog(self, shell_idx: int, reason: str = "resolved"):
        """A permission dialog was answered (auto-approve, tray, or a key
        typed at the pane). Close any open request AND leave
        awaiting_permission, so the next dialog produces a fresh transition.

        The output stream cannot be relied on to exit awaiting_permission
        (nothing Claude Code prints after a dialog is guaranteed to match an
        activity pattern), and without an exit only the FIRST dialog per pane
        ever opened a request - every later one sat unanswered.

        Returns (closed_request_or_None, new_state_or_None).
        """
        closed = self.close_permission(shell_idx, reason=reason)
        new_state = None
        if 0 <= shell_idx < self.num_shells:
            # Clear even when no request was open (answer keys typed at a
            # pane whose dialog was never detected still land here).
            self._tails[shell_idx] = ""
        if (0 <= shell_idx < self.num_shells
                and self.states[shell_idx] == "awaiting_permission"):
            now = time.time()
            self.states[shell_idx] = "unknown"
            self.last_activity[shell_idx] = now
            self._log({"type": "state", "shell": shell_idx,
                       "from": "awaiting_permission", "to": "unknown",
                       "ts": now, "reason": reason})
            new_state = "unknown"
        return closed, new_state

    def open_permissions(self) -> list:
        return [p for p in self.permissions if p is not None]

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
        buf = (self._tails[shell_idx] + text)[-TAIL_BUFFER_BYTES:]
        self._tails[shell_idx] = buf
        new_state = detect_state(buf, prior)
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
        self._tails[shell_idx] = ""  # respawned pane must not inherit old output
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
