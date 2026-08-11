"""Tests for the restart-resume handoff file (~/.quadmux/resume_next.json).

Background: the four `claude` CLIs are children of the server, on PTYs the
server owns. Killing the server (which is what `deploy.sh --restart` and any
plain `kill` do) tears them down, so the replacement server must be told which
session each pane had or every pane cold-starts and the conversations look
wiped. load_resume_next() already consumed this file, but nothing ever wrote
it, so restart-resume was dead code and every restart cleared all four panes.
"""

import importlib.util
import json
import os

import pytest


@pytest.fixture
def qm_module(tmp_path, monkeypatch):
    """Load quadmux-server.py as a module without hyphen import issues."""
    spec = importlib.util.spec_from_file_location(
        "qm_server_resume_next",
        os.path.join(os.path.dirname(__file__), "..", "quadmux-server.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # RESUME_NEXT_PATH is expanded at import time, so patch the attribute
    # rather than HOME: never touch the live server's handoff file.
    monkeypatch.setattr(mod, "RESUME_NEXT_PATH",
                        str(tmp_path / ".quadmux" / "resume_next.json"))
    return mod


def test_writes_pane_index_to_session_id_map(qm_module):
    qm_module.pane_meta = [
        {"session_id": "sid-a"},
        {"session_id": "sid-b"},
        {"session_id": "sid-c"},
        {"session_id": "sid-d"},
    ]
    qm_module.write_resume_next()
    with open(qm_module.RESUME_NEXT_PATH) as f:
        assert json.load(f) == {"0": "sid-a", "1": "sid-b",
                                "2": "sid-c", "3": "sid-d"}


def test_round_trips_through_load_and_is_consumed(qm_module):
    qm_module.pane_meta = [{"session_id": "sid-a"}, {"session_id": "sid-b"}]
    qm_module.write_resume_next()
    assert qm_module.load_resume_next() == {"0": "sid-a", "1": "sid-b"}
    # Consumed on load, so a later cold start is always fresh.
    assert not os.path.exists(qm_module.RESUME_NEXT_PATH)
    assert qm_module.load_resume_next() == {}


def test_panes_without_a_session_id_are_skipped(qm_module):
    # Fallback-CLI panes (e.g. gemini) never get a --session-id.
    qm_module.pane_meta = [{"session_id": "sid-a"}, {}, {"session_id": ""},
                           {"session_id": "sid-d"}]
    qm_module.write_resume_next()
    with open(qm_module.RESUME_NEXT_PATH) as f:
        assert json.load(f) == {"0": "sid-a", "3": "sid-d"}


def test_no_file_written_when_no_pane_has_a_session(qm_module):
    qm_module.pane_meta = [{}, {}]
    qm_module.write_resume_next()
    assert not os.path.exists(qm_module.RESUME_NEXT_PATH)


def test_write_leaves_no_temp_file_behind(qm_module):
    qm_module.pane_meta = [{"session_id": "sid-a"}]
    qm_module.write_resume_next()
    assert not os.path.exists(qm_module.RESUME_NEXT_PATH + ".tmp")


def test_overwrites_a_stale_map_from_an_earlier_run(qm_module):
    os.makedirs(os.path.dirname(qm_module.RESUME_NEXT_PATH), exist_ok=True)
    with open(qm_module.RESUME_NEXT_PATH, "w") as f:
        json.dump({"0": "old-sid", "1": "old-sid-2"}, f)
    qm_module.pane_meta = [{"session_id": "new-sid"}]
    qm_module.write_resume_next()
    with open(qm_module.RESUME_NEXT_PATH) as f:
        assert json.load(f) == {"0": "new-sid"}


def test_graceful_shutdown_records_the_resume_map(qm_module, monkeypatch):
    # The handoff must be written by the shutdown path itself, so that a plain
    # SIGTERM preserves the panes and not just a tidy in-app restart.
    monkeypatch.setattr(qm_module, "save_session", lambda: None)
    monkeypatch.setattr(qm_module, "child_pids", [])
    monkeypatch.setattr(qm_module, "masters", [])
    monkeypatch.setattr(qm_module, "session_dir", "")
    monkeypatch.setattr(qm_module, "worktree_repo", None)
    qm_module.pane_meta = [{"session_id": "sid-a"}, {"session_id": "sid-b"}]
    qm_module.graceful_shutdown()
    with open(qm_module.RESUME_NEXT_PATH) as f:
        assert json.load(f) == {"0": "sid-a", "1": "sid-b"}


def test_sigterm_is_wired_to_the_shutdown_path(qm_module):
    # `kill` (deploy.sh --restart) sends SIGTERM. Before this fix only
    # KeyboardInterrupt ran graceful_shutdown, so SIGTERM killed the server
    # outright: no save, no reap, no resume map.
    import signal as _signal
    calls = []
    qm_module.install_shutdown_handlers(lambda: calls.append("shutdown"))
    handler = _signal.getsignal(_signal.SIGTERM)
    assert callable(handler)
    assert handler not in (_signal.SIG_DFL, _signal.SIG_IGN)
    _signal.signal(_signal.SIGTERM, _signal.SIG_DFL)


def test_resume_map_follows_the_session_fork_chain(qm_module, tmp_path):
    """A pane that forked (--resume) or rolled (/clear) must resume its LIVE
    session, not the spawn-time one: resuming the spawn sid restores the
    conversation as of the previous restart and silently discards everything
    since."""
    import costs
    t = costs.CostTracker(str(tmp_path / "sid-a.jsonl"))
    (tmp_path / "sid-a.jsonl").write_text("")
    fork = tmp_path / "sid-a2.jsonl"
    fork.write_text("")
    t.attach(str(fork), keep_totals=True)
    qm_module.cost_trackers = [t, None]
    qm_module.pane_meta = [{"session_id": "sid-a"}, {"session_id": "sid-b"}]
    qm_module.write_resume_next()
    with open(qm_module.RESUME_NEXT_PATH) as f:
        assert json.load(f) == {"0": "sid-a2", "1": "sid-b"}


def test_resume_map_ignores_tracker_with_foreign_chain(qm_module, tmp_path):
    import costs
    t = costs.CostTracker(str(tmp_path / "someone-else.jsonl"))
    qm_module.cost_trackers = [t]
    qm_module.pane_meta = [{"session_id": "sid-a"}]
    qm_module.write_resume_next()
    with open(qm_module.RESUME_NEXT_PATH) as f:
        assert json.load(f) == {"0": "sid-a"}
