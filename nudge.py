"""Auto-compact nudge for QuadMux panes.

Context size is the dominant model-side cost and it compounds: every request
re-prefills the whole window. Sean's rule is "/compact at 150k after writing
open state to a scratch file", but adherence is the gap (context p90 was 412k
against a 150k rule on 4 Sep 2026). This makes the rule mechanical: when a
pane is IDLE (Claude waiting for input) and its context is at or above
``auto_compact_tokens``, the server types the nudge message into
the pane as if Sean had. Claude then does the scratch-file + /compact itself.

Config lives in ~/.quadmux/policy.json (hot-reloaded on mtime):
    "auto_compact_tokens": 0      -> off (default). 150000 = Sean's 150k rule.
    (Tokens, not percent: Fable panes carry a 1M window in the meter, so a
    percentage would fire at a different size per model.)
    "auto_compact_message": "..." -> optional override of the typed text.

One nudge per crossing: after a nudge the pane is not nudged again until its
context has dropped back under the threshold (i.e. the compact happened) and
climbed over it once more. A pane that ignores the nudge is left alone.
"""

import json
import os
import time

POLICY_PATH = os.path.expanduser("~/.quadmux/policy.json")

DEFAULT_MESSAGE = (
    "[QuadMux auto-compact] Context is at {k}k tokens. Write your open "
    "state (task, decisions, next steps, file paths) to a scratch file now, "
    "then run /compact. Do not /clear."
)


class CompactNudger:
    def __init__(self, num_shells: int, policy_path: str = POLICY_PATH):
        self.policy_path = policy_path
        self._mtime = None
        self.threshold_tokens = 0
        self.message = DEFAULT_MESSAGE
        # True once a pane has been nudged for the current crossing.
        self._armed = [False] * num_shells
        self.last_nudge = [0.0] * num_shells
        self.reload()

    def reload(self):
        cfg = {}
        try:
            with open(self.policy_path) as f:
                cfg = json.load(f)
            self._mtime = os.path.getmtime(self.policy_path)
        except (OSError, json.JSONDecodeError):
            self._mtime = None
        if not isinstance(cfg, dict):
            cfg = {}
        try:
            self.threshold_tokens = int(cfg.get("auto_compact_tokens", 0) or 0)
        except (TypeError, ValueError):
            self.threshold_tokens = 0
        msg = cfg.get("auto_compact_message")
        self.message = msg if isinstance(msg, str) and msg.strip() else DEFAULT_MESSAGE

    def _maybe_reload(self):
        try:
            mtime = os.path.getmtime(self.policy_path)
        except OSError:
            return
        if mtime != self._mtime:
            self.reload()

    @property
    def enabled(self) -> bool:
        return self.threshold_tokens > 0

    def check(self, idx: int, context_tokens: int, state: str, now=None):
        """Return the text to type into pane ``idx`` (without trailing CR),
        or None. Call once per cost poll."""
        self._maybe_reload()
        if not self.enabled or idx >= len(self._armed):
            return None
        if context_tokens < self.threshold_tokens:
            # Back under the line: the compact happened (or a /clear). Re-arm.
            self._armed[idx] = False
            return None
        if self._armed[idx] or state != "idle":
            return None
        self._armed[idx] = True
        self.last_nudge[idx] = now if now is not None else time.time()
        return self.message.format(k=int(round(context_tokens / 1000.0)))
