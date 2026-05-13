"""Tests for the QuadMux session archive / replay store."""

import json
import os
import time

import pytest

import sessions
import status_bus


@pytest.fixture
def fresh_root(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    monkeypatch.setattr(sessions, "SESSIONS_ROOT", str(root))
    return root


def _make_session(root, sid, meta=None, buffers=None):
    d = root / sid
    d.mkdir(parents=True)
    if meta is not None:
        (d / sessions.META_FILENAME).write_text(json.dumps(meta))
    for i, buf in enumerate(buffers or []):
        (d / f"shell_{i}.json").write_text(json.dumps({"buffer": buf}))
    return d


def test_list_sessions_empty(fresh_root):
    assert sessions.list_sessions() == []


def test_list_sessions_returns_newest_first(fresh_root):
    _make_session(fresh_root, "20260101-100000", {"started_at": 1.0, "pane_count": 2})
    _make_session(fresh_root, "20260301-100000", {"started_at": 2.0, "pane_count": 4})
    _make_session(fresh_root, "20260201-100000", {"started_at": 3.0, "pane_count": 3})
    out = sessions.list_sessions()
    assert [s["id"] for s in out] == ["20260301-100000", "20260201-100000", "20260101-100000"]


def test_previous_session_dir_skips_active(fresh_root):
    _make_session(fresh_root, "20260101-100000")
    _make_session(fresh_root, "20260201-100000")
    skip_active = sessions.previous_session_dir(skip_id="20260201-100000")
    assert skip_active and skip_active.endswith("20260101-100000")


def test_previous_session_dir_returns_none_when_empty(fresh_root):
    assert sessions.previous_session_dir() is None


def test_write_and_read_meta(fresh_root):
    d = sessions.session_path("20260513-100000")
    sessions.write_meta(d, {"started_at": 1234.5, "pane_count": 4})
    meta = sessions.read_meta(d)
    assert meta["started_at"] == 1234.5
    assert meta["pane_count"] == 4


def test_update_meta_merges(fresh_root):
    d = sessions.session_path("20260513-100000")
    sessions.write_meta(d, {"started_at": 1.0, "pane_count": 4})
    sessions.update_meta(d, ended_at=2.0)
    meta = sessions.read_meta(d)
    assert meta["started_at"] == 1.0
    assert meta["ended_at"] == 2.0
    assert meta["pane_count"] == 4


def test_load_buffers_handles_missing_files(fresh_root):
    _make_session(fresh_root, "abc", buffers=[["chunk a"], ["chunk b"]])
    out = sessions.load_buffers(str(fresh_root / "abc"), 4)
    assert out[0] == ["chunk a"]
    assert out[1] == ["chunk b"]
    assert out[2] == []
    assert out[3] == []


def test_filter_bus_events_by_range(tmp_path, monkeypatch):
    log = tmp_path / "bus.jsonl"
    log.write_text("\n".join([
        json.dumps({"type": "state", "ts": 1.0, "to": "x"}),
        json.dumps({"type": "state", "ts": 5.0, "to": "y"}),
        json.dumps({"type": "state", "ts": 9.0, "to": "z"}),
    ]) + "\n")
    monkeypatch.setattr(status_bus, "BUS_LOG", str(log))
    out = sessions.filter_bus_events(started_at=4.0, ended_at=8.0)
    assert len(out) == 1
    assert out[0]["to"] == "y"


def test_load_replay_returns_buffers_and_events(fresh_root, tmp_path, monkeypatch):
    log = tmp_path / "bus.jsonl"
    log.write_text(json.dumps({"type": "state", "ts": 100.0, "to": "thinking"}) + "\n")
    monkeypatch.setattr(status_bus, "BUS_LOG", str(log))

    _make_session(fresh_root, "20260513-101010",
                  meta={"started_at": 50.0, "ended_at": 200.0, "pane_count": 2},
                  buffers=[["hello from pane 1"], []])
    replay = sessions.load_replay("20260513-101010", 2)
    assert replay["found"] is True
    assert replay["meta"]["pane_count"] == 2
    assert replay["buffers"][0] == ["hello from pane 1"]
    assert replay["events"][0]["to"] == "thinking"


def test_load_replay_missing_session(fresh_root):
    replay = sessions.load_replay("nope", 4)
    assert replay["found"] is False
    assert replay["buffers"] == []
