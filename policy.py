"""Permission policy engine for QuadMux.

Classifies Claude Code permission prompts into three bands:

    green - read-only operations. Auto-approved silently (logged to bus).
    amber - reversible writes / everything not green or red. Auto-approved
            with a visible notice (toast + activity feed).
    red   - destructive, publishing, or outward-facing operations
            (delete, push, send email/message, etc.). Always asks the user
            via the permission tray.

Bands are decided by regex pattern lists. Built-in patterns can be extended
(or exempted) via ~/.quadmux/policy.json:

    {
      "enabled": true,
      "settle_seconds": 0.5,
      "extra_red":   ["my-deploy-script"],
      "extra_green": ["poetry show"],
      "never_red":   ["rm -rf /tmp/scratch"]
    }

`never_red` patterns exempt a matching prompt from the red band (it falls
through to green/amber matching). All patterns are case-insensitive regex.
"""

import json
import os
import re

POLICY_PATH = os.path.expanduser("~/.quadmux/policy.json")

DEFAULT_CONFIG = {
    "enabled": True,
    "settle_seconds": 0.5,
    "mode": "standard",  # "standard" = full red list; "minimal" = only sends stay red
    "extra_red": [],
    "extra_green": [],
    "never_red": [],
}

# --- Red: destructive / irreversible / outward-facing. Always ask. ---
RED_PATTERNS = [
    # recursive / forced deletion
    r"rm\s+-[a-z]*[rf]",
    r"\bfind\b.*\s-delete\b",
    r"\bshred\b",
    r"crontab\s+-r",
    # data destruction
    r"drop\s+(table|database)",
    r"truncate\s+table",
    # privilege escalation
    r"\bsudo\b",
    # git publishing / history rewriting
    r"git\s+push",
    r"git\s+reset\s+--hard",
    r"gh\s+pr\s+merge",
    r"gh\s+release",
    # package publishing
    r"(npm|yarn|pnpm)\s+publish",
    # disk / device / system destruction
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r">\s*/dev/",
    r"format\s+c:",
    r"\bdeltree\b",
    r"\b(shutdown|reboot)\b",
    r"launchctl\s+unload",
    r"iptables\s+-f",
    # infra / cloud destruction
    r"kubectl\s+delete",
    r"terraform\s+destroy",
    r"docker\s+system\s+prune",
    r"aws\s+s3\s+(rm|rb)",
    r"heroku\s+apps:destroy",
    # dangerous permissions / pipe-to-shell
    r"chmod\s+777",
    r"chmod\s+-r\b",
    r"\|\s*(sh|bash|zsh)\b",
]

# Outward-facing sends (Sean's no-unauthorized-send guardrail). These stay
# red even in "minimal" mode: an auto-approved send prompt would defeat the
# PreToolUse send-guardrail hook, whose confirmation renders as a permission
# dialog the policy would otherwise answer itself.
SEND_RED_PATTERNS = [
    r"send_email",
    r"gmail.*\bsend\b",
    r"\bsend\b.*\bemail\b",
    r"whatsapp.*send_(message|file|audio)",
    r"send_message",
    r"osascript.*\bsend\b",
]

# --- Green: read-only. Auto-approve silently. ---
# Shell commands must be anchored to where the command actually starts in a
# Claude Code prompt: "Bash(cmd ...)" or "run: cmd" / "run cmd". A bare word
# like "cat" anywhere in the prompt text must NOT make it green.
_RO_CMDS = (r"(ls|cat|head|tail|wc|du|df|ps|pwd|stat|which|whoami|uname|"
            r"grep|rg|ag|mdfind|tree|find(?!.*\s-delete\b)|"
            r"git\s+(status|log|diff|show|branch|remote|stash\s+list))")
GREEN_PATTERNS = [
    # read-only Claude Code tools
    r"\ballow\s+(read|glob|grep|fetch|websearch|webfetch|search)\b",
    r"\b(read|glob|grep|webfetch|websearch)\s*\(",
    # read-only shell commands, anchored to the command position
    r"bash\s*\(\s*" + _RO_CMDS + r"\b",
    r"\brun:?\s+['\"]?" + _RO_CMDS + r"\b",
]


def _load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(POLICY_PATH) as f:
            user = json.load(f)
        if isinstance(user, dict):
            cfg.update({k: user[k] for k in DEFAULT_CONFIG if k in user})
    except (OSError, json.JSONDecodeError):
        pass
    return cfg


def _write_default_config():
    """Create ~/.quadmux/policy.json with defaults if it doesn't exist."""
    if os.path.exists(POLICY_PATH):
        return
    try:
        os.makedirs(os.path.dirname(POLICY_PATH), exist_ok=True)
        with open(POLICY_PATH, "w") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
    except OSError:
        pass


class PolicyEngine:
    """Loads config at init; re-reads policy.json automatically whenever the
    file's mtime changes, so tuning never needs a server restart."""

    def __init__(self):
        _write_default_config()
        self.reload()

    def reload(self):
        cfg = _load_config()
        self.enabled = bool(cfg.get("enabled", True))
        self.settle_seconds = float(cfg.get("settle_seconds", 0.5))
        self.mode = str(cfg.get("mode", "standard")).lower()
        flags = re.IGNORECASE
        base_red = SEND_RED_PATTERNS if self.mode == "minimal" else RED_PATTERNS + SEND_RED_PATTERNS
        self._red = [re.compile(p, flags) for p in base_red + list(cfg.get("extra_red", []))]
        self._green = [re.compile(p, flags) for p in GREEN_PATTERNS + list(cfg.get("extra_green", []))]
        self._never_red = [re.compile(p, flags) for p in cfg.get("never_red", [])]
        try:
            self._mtime = os.path.getmtime(POLICY_PATH)
        except OSError:
            self._mtime = None

    def _maybe_reload(self):
        try:
            mtime = os.path.getmtime(POLICY_PATH)
        except OSError:
            return
        if mtime != self._mtime:
            self.reload()

    def classify(self, context: str, question: str = "") -> tuple:
        """Return (band, rule) for a permission prompt.

        `context` is the cleaned recent PTY output around the prompt;
        `question` is the extracted one-line question (may be empty).
        Red wins over green; anything else is amber.
        """
        self._maybe_reload()
        text = (context or "") + "\n" + (question or "")
        for rx in self._red:
            if rx.search(text):
                if any(n.search(text) for n in self._never_red):
                    break
                return ("red", rx.pattern)
        for rx in self._green:
            if rx.search(text):
                return ("green", rx.pattern)
        return ("amber", "default")
