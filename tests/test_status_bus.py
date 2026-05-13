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
