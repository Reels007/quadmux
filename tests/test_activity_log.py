"""Tests for the activity-log reader (phase 5)."""

import json
import time

import pytest

import activity_log
import status_bus


def _seed(path, events):
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")


def test_returns_empty_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(status_bus, "BUS_LOG", str(tmp_path / "nope.jsonl"))
    monkeypatch.setattr(activity_log, "BUS_LOG", str(tmp_path / "nope.jsonl"))
    assert activity_log.recent_events() == []


def test_returns_events_newest_first(tmp_path, monkeypatch):
    log = tmp_path / "bus.jsonl"
    _seed(log, [
        {"type": "state", "shell": 0, "to": "thinking", "ts": 1.0},
        {"type": "state", "shell": 0, "to": "idle",     "ts": 2.0},
        {"type": "handoff", "source": 0, "target": 1,   "ts": 3.0},
    ])
    monkeypatch.setattr(activity_log, "BUS_LOG", str(log))
    out = activity_log.recent_events()
    assert [e["ts"] for e in out] == [3.0, 2.0, 1.0]


def test_filter_by_shell(tmp_path, monkeypatch):
    log = tmp_path / "bus.jsonl"
    _seed(log, [
        {"type": "state", "shell": 0, "ts": 1.0},
        {"type": "state", "shell": 1, "ts": 2.0},
        {"type": "state", "shell": 1, "ts": 3.0},
    ])
    monkeypatch.setattr(activity_log, "BUS_LOG", str(log))
    out = activity_log.recent_events(shell=1)
    assert len(out) == 2
    assert all(e["shell"] == 1 for e in out)


def test_filter_by_type(tmp_path, monkeypatch):
    log = tmp_path / "bus.jsonl"
    _seed(log, [
        {"type": "state",             "ts": 1.0},
        {"type": "permission_request","ts": 2.0},
        {"type": "permission_resolved","ts": 3.0},
        {"type": "handoff",           "ts": 4.0},
    ])
    monkeypatch.setattr(activity_log, "BUS_LOG", str(log))
    out = activity_log.recent_events(event_types=["permission_request", "permission_resolved"])
    assert {e["type"] for e in out} == {"permission_request", "permission_resolved"}


def test_limit_respected(tmp_path, monkeypatch):
    log = tmp_path / "bus.jsonl"
    _seed(log, [{"type": "state", "ts": float(i)} for i in range(50)])
    monkeypatch.setattr(activity_log, "BUS_LOG", str(log))
    out = activity_log.recent_events(limit=10)
    assert len(out) == 10
    # newest first
    assert out[0]["ts"] == 49.0


def test_malformed_lines_skipped(tmp_path, monkeypatch):
    log = tmp_path / "bus.jsonl"
    log.write_text("{not json\n" + json.dumps({"type": "state", "ts": 5.0}) + "\n")
    monkeypatch.setattr(activity_log, "BUS_LOG", str(log))
    out = activity_log.recent_events()
    assert len(out) == 1
    assert out[0]["ts"] == 5.0
