"""account_check - report which Claude account QuadMux is about to run as.

All four panes inherit a single credential from ``~/.claude``: the server sets no
per-pane ``CLAUDE_CONFIG_DIR``, and it pops ``ANTHROPIC_API_KEY`` before spawning,
so a pane can never fall back to API-org credits. That makes "which account am I
on" a per-startup fact worth surfacing rather than a per-pane one.

Two sources, deliberately kept apart:

* ``~/.claude.json`` is LIVE. It gives the account identity (email, org, seat tier).
* ``~/.quadmux/account_notes.json`` is a DATED note file that Sean maintains by hand.
  Credit balances live only in the Claude Console, are not readable from this
  machine, and so are always reported with the date they were verified. They are
  never presented as live.
"""

import json
import os

CLAUDE_CONFIG = os.path.expanduser("~/.claude.json")
NOTES_FILE = os.path.expanduser("~/.quadmux/account_notes.json")

# Seat tiers Claude Code reports, mapped to the labels Sean uses.
TIER_LABELS = {
    "team_standard": "Team",
    "team": "Team",
    "max_20x": "Max 20x",
    "max_5x": "Max 5x",
    "max": "Max",
    "pro": "Pro",
    "free": "Free",
    "enterprise": "Enterprise",
}


def read_account(config_path=None):
    """Read the live account identity. Never raises: a bad read returns ok=False."""
    path = config_path or CLAUDE_CONFIG
    blank = {"ok": False, "email": None, "org": None, "tier": None,
             "extra_usage": None, "error": None}

    if not os.path.isfile(path):
        blank["error"] = "not found: " + path
        return blank
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (ValueError, OSError) as exc:
        blank["error"] = "unreadable: " + str(exc)
        return blank

    acct = data.get("oauthAccount")
    if not isinstance(acct, dict) or not acct.get("emailAddress"):
        blank["error"] = "no oauthAccount block, is Claude Code logged in?"
        return blank

    seat = acct.get("seatTier") or ""
    return {
        "ok": True,
        "email": acct.get("emailAddress"),
        "org": acct.get("organizationName"),
        "tier": TIER_LABELS.get(seat, seat or None),
        "extra_usage": acct.get("hasExtraUsageEnabled"),
        "error": None,
    }


def read_notes(notes_path=None, email=None):
    """Return the hand-maintained note for ``email``, or {} if there is none."""
    path = notes_path or NOTES_FILE
    if not email or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (ValueError, OSError):
        return {}
    entry = (data.get("accounts") or {}).get(email)
    return entry if isinstance(entry, dict) else {}


def summary(config_path=None, notes_path=None):
    """Everything the banner and the /api/account route need, in one dict."""
    acct = read_account(config_path)
    note = read_notes(notes_path, acct.get("email"))
    return {
        "ok": acct["ok"],
        "email": acct["email"],
        "org": acct["org"],
        "tier": acct["tier"],
        "extra_usage": acct["extra_usage"],
        "error": acct["error"],
        "credits": note.get("credits"),
        "seats": note.get("seats"),
        "note": note.get("note"),
        # Balances come from a dated note, never from the machine. Keep this
        # False so no caller can present the figure as current.
        "credits_live": False,
        "credits_verified": note.get("verified"),
    }


def banner_lines(config_path=None, notes_path=None):
    """Plain-text lines for the server log. No dashes, per Sean's text rule."""
    s = summary(config_path, notes_path)
    if not s["ok"]:
        return ["Claude account: UNKNOWN (" + (s["error"] or "no detail") + ")",
                "  Run 'claude' once to log in, or check ~/.claude.json"]

    head = "Claude account: " + s["email"]
    bits = [b for b in (s["tier"], s["org"]) if b]
    if bits:
        head += "  (" + ", ".join(bits) + ")"
    lines = [head, "  All 4 panes use this one login. Switch with /login inside a pane."]

    if s["credits"]:
        detail = "  Credits " + s["credits"]
        if s["seats"]:
            detail += ", seats " + s["seats"]
        if s["credits_verified"]:
            detail += " (as verified " + s["credits_verified"] + ", not live)"
        lines.append(detail)
    if s["note"]:
        lines.append("  " + s["note"])
    return lines


def print_banner(config_path=None, notes_path=None):
    for line in banner_lines(config_path, notes_path):
        print(line, flush=True)
