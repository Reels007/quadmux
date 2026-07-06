"""Tests for the QuadMux v2 status bus and permission tracking."""

import os
import time

import pytest

import status_bus
from status_bus import StatusBus, detect_state, extract_permission_question


# --- detect_state ---

def test_detect_thinking_from_spinner():
    assert detect_state("⠋ Working...", "unknown") == "thinking"

def test_detect_tool_running():
    assert detect_state("● Read(/etc/hosts)", "unknown") == "tool_running"
    assert detect_state("● Bash(npm test)", "idle") == "tool_running"

def test_detect_permission():
    assert detect_state("Do you want to proceed? (y/n)", "thinking") == "awaiting_permission"
    assert detect_state("Allow this action? [y/n]", "idle") == "awaiting_permission"

def test_detect_errored():
    assert detect_state("Error: connection refused", "unknown") == "errored"
    assert detect_state("Traceback (most recent call last):", "idle") == "errored"

def test_detect_idle_from_prompt():
    assert detect_state("done\n❯ ", "thinking") == "idle"

def test_detect_no_change_returns_none():
    # Prompt visible but already idle: no transition needed
    assert detect_state("output\n❯ ", "idle") is None

def test_ansi_codes_stripped():
    # Spinner inside ANSI escape should still trigger thinking
    assert detect_state("\x1b[33m⠋\x1b[0m thinking", "unknown") == "thinking"


# --- StatusBus ---

def test_bus_initial_state():
    bus = StatusBus(4)
    snap = bus.snapshot()
    assert len(snap) == 4
    assert all(s["state"] == "unknown" for s in snap)

def test_bus_update_changes_state():
    bus = StatusBus(2)
    assert bus.update(0, "⠋ working") == "thinking"
    assert bus.states[0] == "thinking"
    # Same state: no change reported
    assert bus.update(0, "⠙ still working") is None

def test_bus_update_invalid_shell():
    bus = StatusBus(2)
    assert bus.update(5, "anything") is None
    assert bus.update(-1, "anything") is None

def test_bus_tick_decays_to_idle():
    bus = StatusBus(1)
    bus.update(0, "⠋ working")
    # Force activity timestamp into the past and ensure a prompt was seen later
    bus.last_activity[0] = time.time() - 5.0
    bus.last_prompt_seen[0] = time.time()
    changes = bus.tick()
    assert len(changes) == 1
    assert changes[0]["to"] == "idle"
    assert bus.states[0] == "idle"

def test_bus_tick_does_not_decay_without_prompt():
    bus = StatusBus(1)
    bus.update(0, "⠋ working")
    bus.last_activity[0] = time.time() - 5.0
    # No prompt seen recently
    changes = bus.tick()
    assert changes == []
    assert bus.states[0] == "thinking"

def test_bus_mark_dead():
    bus = StatusBus(2)
    bus.mark_dead(0)
    assert bus.states[0] == "errored"


# --- Permission tracking (phase 2) ---

def test_extract_permission_question_basic():
    q = extract_permission_question("Do you want to allow Bash(ls)? (y/n)")
    assert "Do you want to allow Bash(ls)" in q

def test_extract_permission_question_strips_box_drawing():
    text = "┌──────────┐\n│ Do you want to write to /tmp/foo? │\n└──────────┘\n[y/n]"
    q = extract_permission_question(text)
    assert "Do you want to write to /tmp/foo" in q
    assert "│" not in q
    assert "┌" not in q

def test_extract_permission_question_returns_empty_for_no_match():
    assert extract_permission_question("just some output\n") == ""

def test_open_and_close_permission():
    bus = StatusBus(2)
    req = bus.open_permission(0, "Do you want to do X? (y/n)")
    assert req["shell"] == 0
    assert req["id"] == 1
    assert bus.open_permissions() == [req]

    closed = bus.close_permission(0, reason="allow")
    assert closed["id"] == 1
    assert bus.open_permissions() == []

def test_permission_ids_are_unique():
    bus = StatusBus(2)
    r1 = bus.open_permission(0, "first")
    bus.close_permission(0)
    r2 = bus.open_permission(0, "second")
    assert r1["id"] != r2["id"]

def test_close_permission_when_none_open_returns_none():
    bus = StatusBus(1)
    assert bus.close_permission(0) is None


# --- Bus log persistence ---

def test_bus_log_appended(tmp_path, monkeypatch):
    log = tmp_path / "bus.jsonl"
    monkeypatch.setattr(status_bus, "BUS_LOG", str(log))
    bus = StatusBus(1)
    bus.update(0, "⠋ working")
    bus.open_permission(0, "test?")
    bus.close_permission(0, reason="allow")
    contents = log.read_text().strip().split("\n")
    types = [line for line in contents if line]
    # Expect: state change to thinking, permission_request, permission_resolved
    assert any('"to": "thinking"' in t for t in types)
    assert any('"permission_request"' in t for t in types)
    assert any('"permission_resolved"' in t for t in types)


# --- Permission detection hardening (5 Jul 2026) ---

NUMBERED_DIALOG = (
    "╭─ Bash command ─────────────╮\n"
    "│ git status                 │\n"
    "╰────────────────────────────╯\n"
    "Do you want to proceed?\n"
    "❯ 1. Yes\n"
    "  2. Yes, and don't ask again for git status\n"
    "  3. No, and tell Claude what to do differently\n"
)

def test_detect_permission_numbered_menu():
    assert detect_state(NUMBERED_DIALOG, "tool_running") == "awaiting_permission"

def test_detect_trust_prompt():
    text = "Do you trust the files in this folder?\n❯ 1. Yes, proceed\n  2. No, exit\n"
    assert detect_state(text, "unknown") == "awaiting_permission"

def test_echoed_user_text_is_not_permission():
    # The user's own typed message echoes through the PTY; a bare
    # "do you want to proceed" phrase must not open a permission request.
    text = '❯ why am i getting "do you want to proceed" messages in the new quadmux?\n'
    assert detect_state(text, "idle") != "awaiting_permission"

def test_extract_question_keeps_spaces_across_ansi_positioning():
    # Cursor-positioning sequences stand in for whitespace; stripping them to
    # '' used to produce "fromthetargetdirectory.Approveonlyifyoutrustit."
    text = ("Approve\x1b[5;18Honly\x1b[5;23Hif\x1b[5;26Hyou"
            "\x1b[5;30Htrust\x1b[5;36Hit.")
    q = extract_permission_question(text)
    assert q == "Approve only if you trust it."

def test_dialog_block_excludes_scrollback():
    from status_bus import extract_dialog_block
    raw = ("I will send the email to the investor after you approve.\n"
           "╭─ Bash command ─╮\n│ ls -la ~/Desktop │\n╰─╯\n"
           "Do you want to proceed?\n❯ 1. Yes\n")
    block = extract_dialog_block(raw)
    assert "send the email" not in block
    assert "ls -la" in block

def test_dialog_block_falls_back_without_box():
    from status_bus import extract_dialog_block
    block = extract_dialog_block("Do you want to proceed? (y/n)")
    assert "proceed" in block

def test_prose_about_permission_dialogs_is_not_permission():
    # Chat replies ABOUT the permission system echo phrases like
    # "Do you want to proceed" and "1. Yes" without being a dialog.
    text = ('Detector requires the actual menu ("Do you want/trust" + "1. Yes",\n'
            'or a y/n marker to co-occur) before opening a request.\n')
    assert detect_state(text, "idle") != "awaiting_permission"

def test_quoted_yn_mid_sentence_is_not_permission():
    # "(y/n)" quoted inside chat prose is not a prompt; only a y/n marker
    # at end of line (where a real prompt awaits input) counts.
    text = ('Note: legacy prompts end with (y/n) instead of a menu.\n'
            '\n❯ \n? for shortcuts\n')
    assert detect_state(text, "idle") != "awaiting_permission"

def test_yn_at_line_end_is_permission():
    assert detect_state("Overwrite existing file? [y/n]: ", "tool_running") == "awaiting_permission"
    assert detect_state("Continue? (y/n)\n", "idle") == "awaiting_permission"


# --- Dialog resolution re-arms detection (5 Jul 2026 late) ---
# Bug: state stuck in awaiting_permission after the first dialog (nothing in
# the output stream reliably transitions out), so every SUBSEQUENT dialog was
# invisible to the policy layer - only one auto-approve per pane per restart.

def test_resolve_dialog_closes_request_and_rearms():
    bus = StatusBus(1)
    assert bus.update(0, NUMBERED_DIALOG) == "awaiting_permission"
    req = bus.open_permission(0, "Do you want to proceed?")
    closed, new_state = bus.resolve_dialog(0, reason="auto_amber")
    assert closed["id"] == req["id"]
    assert new_state == "unknown"
    assert bus.states[0] == "unknown"
    assert bus.permissions[0] is None
    # The critical regression: a SECOND dialog must trigger a fresh transition.
    assert bus.update(0, NUMBERED_DIALOG) == "awaiting_permission"

def test_resolve_dialog_with_no_open_request_still_resets_state():
    bus = StatusBus(1)
    bus.update(0, NUMBERED_DIALOG)
    closed, new_state = bus.resolve_dialog(0, reason="pane_input")
    assert closed is None
    assert new_state == "unknown"

def test_resolve_dialog_leaves_other_states_alone():
    bus = StatusBus(1)
    bus.update(0, "⠋ Working...")
    closed, new_state = bus.resolve_dialog(0, reason="pane_input")
    assert closed is None
    assert new_state is None
    assert bus.states[0] == "thinking"


# --- is_dialog_answer_key: which pane keystrokes resolve a dialog ---

def test_dialog_answer_keys():
    from status_bus import is_dialog_answer_key
    for k in ("1", "2", "3", "9", "y", "Y", "n", "N", "\r", "\n", "\x1b"):
        assert is_dialog_answer_key(k), repr(k)

def test_non_answer_keys():
    from status_bus import is_dialog_answer_key
    # Arrow/page-key CSI sequences start with ESC but do not answer a dialog;
    # neither do ordinary letters (scroll, accidental typing).
    for k in ("\x1b[A", "\x1b[B", "\x1b[5~", "a", "q", " ", ""):
        assert not is_dialog_answer_key(k), repr(k)


# --- Rolling tail buffer: dialogs split across PTY chunks (6 Jul 2026) ---
# Bug: detect_state scanned only the last 400 chars of each SINGLE chunk, so a
# dialog whose "1. Yes" row landed in an earlier chunk (big heredoc command
# previews split mid-render) or more than 400 chars from the chunk end (wide
# panes: option rows + border + hint line after the row) was never detected -
# no auto-approve, no tray entry, the dialog just sat there.

HEREDOC_DIALOG = (
    "╭─ Bash command ──────────────────────────────╮\n"
    "│ python3 - <<'EOF'                           │\n"
    + "".join(f"│ data.append(process(row_{i}, key_{i}))          │\n" for i in range(12))
    + "│ EOF                                         │\n"
    "╰─────────────────────────────────────────────╯\n"
    "Do you want to proceed?\n"
    "❯ 1. Yes\n"
    "  2. Yes, and don't ask again for python3 commands in /Users/sean\n"
    "  3. No, and tell Claude what to do differently (esc)\n"
)

def test_dialog_split_across_chunks_is_detected():
    bus = StatusBus(1)
    cut = HEREDOC_DIALOG.index("❯ 1. Y") + len("❯ 1. Y")
    assert bus.update(0, HEREDOC_DIALOG[:cut]) != "awaiting_permission"
    assert bus.update(0, HEREDOC_DIALOG[cut:]) == "awaiting_permission"

def test_dialog_row_far_from_chunk_end_is_detected():
    # Wide pane: >400 chars of option rows / padding after the "❯ 1. Yes" row.
    pad = " " * 150
    text = (
        "Do you want to proceed?\n"
        "❯ 1. Yes" + pad + "\n"
        "  2. Yes, and don't ask again for this command" + pad + "\n"
        "  3. No, and tell Claude what to do differently (esc)" + pad + "\n"
    )
    assert len(text) - (text.index("❯ 1. Yes") + 8) > 400
    assert detect_state(text, "tool_running") == "awaiting_permission"

def test_answered_dialog_text_does_not_reopen_phantom():
    # After resolve_dialog the buffered dialog text must be dropped: the next
    # ordinary output chunk must not re-match "❯ 1. Yes" from the answered
    # dialog and open a phantom request (which would auto-type a stray "1").
    bus = StatusBus(1)
    assert bus.update(0, HEREDOC_DIALOG) == "awaiting_permission"
    bus.open_permission(0, "Do you want to proceed?")
    bus.resolve_dialog(0, reason="auto_amber")
    assert bus.update(0, "⏺ Bash(python3 - <<'EOF'...)\n  ⎿ ok\n") != "awaiting_permission"
    assert bus.states[0] != "awaiting_permission"

def test_state_change_close_also_drops_buffer():
    bus = StatusBus(1)
    assert bus.update(0, HEREDOC_DIALOG) == "awaiting_permission"
    bus.open_permission(0, "Do you want to proceed?")
    bus.close_permission(0, reason="state_change")
    assert bus.update(0, "some ordinary output\n") != "awaiting_permission"
