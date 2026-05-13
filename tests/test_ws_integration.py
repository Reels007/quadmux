"""End-to-end WS dispatch tests for phase 4 (handoff) and parked sidebar.

We don't spawn Claude or a real websockets server. Instead, we drive
``handler()`` directly with a fake WebSocket and capture what the server
writes to PTY masters and broadcasts to clients.
"""

import asyncio
import importlib.util
import json
import os
import types

import pytest


@pytest.fixture
def qm():
    spec = importlib.util.spec_from_file_location(
        "qm_server",
        os.path.join(os.path.dirname(__file__), "..", "quadmux-server.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeWS:
    """Minimal async WebSocket double. Supports send/iteration over a queue."""

    def __init__(self, incoming):
        self.incoming = list(incoming)
        self.sent = []

    async def send(self, msg):
        self.sent.append(msg)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.incoming:
            raise StopAsyncIteration
        return self.incoming.pop(0)


def _setup_server(qm, num_shells, monkeypatch, parked_path, bus_log=None,
                  cost_trackers=None):
    qm.NUM_SHELLS = num_shells
    qm.shell_buffers = [[] for _ in range(num_shells)]
    qm.pane_meta = [{} for _ in range(num_shells)]
    qm.masters = []
    qm.child_pids = []
    qm.clients = set()
    qm.bus = qm.StatusBus(num_shells)
    qm.cost_trackers = cost_trackers if cost_trackers is not None else []
    monkeypatch.setattr(qm.parked_mod, "PARKED_PATH", str(parked_path))
    if bus_log is not None:
        monkeypatch.setattr(qm.activity_mod, "BUS_LOG", str(bus_log))


def test_handoff_writes_to_target_pty(qm, tmp_path, monkeypatch):
    _setup_server(qm, 2, monkeypatch, tmp_path / "parked.json")

    # Pretend pane 0 has produced some prose
    qm.shell_buffers[0].append(
        "Here is a sufficient line of clean prose for the handoff to use.\n"
        "And another line of prose so the prose extractor is happy.\n"
    )

    # Capture writes to the fake masters
    writes = {}
    def fake_write(fd, data):
        writes.setdefault(fd, b"")
        writes[fd] += data
        return len(data)
    monkeypatch.setattr(qm.os, "write", fake_write)
    qm.masters = [10, 11]  # two fake fds

    msg = json.dumps({
        "type": "handoff_request",
        "source": 0,
        "target": 1,
        "instruction": "review this",
    })
    ws = FakeWS([msg])

    asyncio.run(qm.handler(ws))

    # The target pane (fd 11) should have received the wrapped prose
    assert 11 in writes
    body = writes[11].decode()
    assert "[Handoff from pane 1]" in body
    assert "review this" in body
    assert "clean prose" in body
    # And a handoff broadcast was sent to the client
    assert any('"type": "handoff"' in s for s in ws.sent)


def test_handoff_rejects_bad_target(qm, tmp_path, monkeypatch):
    _setup_server(qm, 2, monkeypatch, tmp_path / "parked.json")
    monkeypatch.setattr(qm.os, "write", lambda fd, data: len(data))
    qm.masters = [10, 11]

    msg = json.dumps({
        "type": "handoff_request",
        "source": 0,
        "target": 0,  # same as source
    })
    ws = FakeWS([msg])
    asyncio.run(qm.handler(ws))
    assert any('"type": "handoff_error"' in s for s in ws.sent)


def test_handoff_rejects_when_no_prose(qm, tmp_path, monkeypatch):
    _setup_server(qm, 2, monkeypatch, tmp_path / "parked.json")
    # buffers empty -> no prose
    monkeypatch.setattr(qm.os, "write", lambda fd, data: len(data))
    qm.masters = [10, 11]

    msg = json.dumps({"type": "handoff_request", "source": 0, "target": 1})
    ws = FakeWS([msg])
    asyncio.run(qm.handler(ws))
    errors = [json.loads(s) for s in ws.sent
              if '"handoff_error"' in s]
    assert errors and "no prose to hand off" in errors[0]["error"]


def test_parked_add_creates_and_broadcasts(qm, tmp_path, monkeypatch):
    parked_path = tmp_path / "parked.json"
    _setup_server(qm, 2, monkeypatch, parked_path)

    msg = json.dumps({
        "type": "parked_add",
        "title": "WS-test task",
        "note": "hello",
        "status": "blocked",
        "pane": 0,
    })
    ws = FakeWS([msg])
    asyncio.run(qm.handler(ws))

    # Task persisted to disk
    data = json.loads(parked_path.read_text())
    assert data["tasks"][0]["title"] == "WS-test task"
    assert data["tasks"][0]["status"] == "blocked"
    # Broadcast carried the new task
    parked_add = [s for s in ws.sent if '"type": "parked_add"' in s]
    assert parked_add
    payload = json.loads(parked_add[0])
    assert payload["task"]["title"] == "WS-test task"


def test_parked_update_and_delete_via_ws(qm, tmp_path, monkeypatch):
    parked_path = tmp_path / "parked.json"
    _setup_server(qm, 2, monkeypatch, parked_path)

    # Pre-seed
    t = qm.parked_mod.add_task("Initial", status="parked")

    update_msg = json.dumps({
        "type": "parked_update",
        "id": t["id"],
        "status": "in-progress",
        "title": "Renamed",
    })
    delete_msg = json.dumps({"type": "parked_delete", "id": t["id"]})
    ws = FakeWS([update_msg, delete_msg])
    asyncio.run(qm.handler(ws))

    # After the run, file should reflect deletion (last action)
    assert qm.parked_mod.list_tasks() == []

    # Broadcasts should include both an update with the new title/status
    # and a delete with the id.
    updates = [s for s in ws.sent if '"type": "parked_update"' in s]
    assert updates
    payload = json.loads(updates[0])
    assert payload["task"]["title"] == "Renamed"
    assert payload["task"]["status"] == "in-progress"
    deletes = [s for s in ws.sent if '"type": "parked_delete"' in s]
    assert deletes
    assert json.loads(deletes[0])["id"] == t["id"]


def test_initial_connect_sends_snapshots(qm, tmp_path, monkeypatch):
    parked_path = tmp_path / "parked.json"
    _setup_server(qm, 2, monkeypatch, parked_path)
    qm.parked_mod.add_task("Pre-existing", note="should appear on connect")

    ws = FakeWS([])  # no inbound messages -> just send snapshots and exit
    asyncio.run(qm.handler(ws))

    types_sent = []
    for s in ws.sent:
        try:
            types_sent.append(json.loads(s).get("type"))
        except json.JSONDecodeError:
            pass
    for required in ("hello", "state_snapshot", "permission_snapshot",
                     "pane_meta", "parked_list", "cost_snapshot"):
        assert required in types_sent, f"missing {required} on connect"

    # parked_list payload contains the seeded task
    parked_list_msg = next(s for s in ws.sent if '"parked_list"' in s)
    assert "Pre-existing" in parked_list_msg


def test_activity_request_returns_filtered_events(qm, tmp_path, monkeypatch):
    bus_log = tmp_path / "bus.jsonl"
    bus_log.write_text("\n".join([
        json.dumps({"type": "state", "shell": 0, "from": "idle", "to": "thinking", "ts": 1.0}),
        json.dumps({"type": "state", "shell": 1, "from": "idle", "to": "thinking", "ts": 2.0}),
        json.dumps({"type": "handoff", "source": 0, "target": 1, "ts": 3.0}),
        json.dumps({"type": "permission_request", "id": 1, "shell": 0, "ts": 4.0}),
    ]) + "\n")
    _setup_server(qm, 2, monkeypatch, tmp_path / "parked.json", bus_log=bus_log)

    msg = json.dumps({"type": "activity_request", "limit": 50,
                      "types": ["state", "handoff"]})
    ws = FakeWS([msg])
    asyncio.run(qm.handler(ws))

    responses = [json.loads(s) for s in ws.sent
                 if '"activity_response"' in s]
    assert responses
    events = responses[0]["events"]
    types_seen = {e["type"] for e in events}
    assert types_seen == {"state", "handoff"}
    # newest-first ordering
    assert events[0]["ts"] >= events[-1]["ts"]


def test_cost_snapshot_reflects_tracker_totals(qm, tmp_path, monkeypatch):
    # Build a fake session JSONL the tracker can read
    session = tmp_path / "session.jsonl"
    session.write_text(json.dumps({
        "message": {"model": "claude-opus-4-7",
                    "usage": {"input_tokens": 200, "output_tokens": 100}}
    }) + "\n")
    tracker = qm.costs_mod.CostTracker(str(session))
    tracker.poll()  # pull the line into the tracker

    _setup_server(qm, 1, monkeypatch, tmp_path / "parked.json",
                  cost_trackers=[tracker])

    ws = FakeWS([])
    asyncio.run(qm.handler(ws))
    cost_msgs = [json.loads(s) for s in ws.sent
                 if '"cost_snapshot"' in s]
    assert cost_msgs
    payload = cost_msgs[0]
    assert payload["panes"][0]["tokens"]["input"] == 200
    assert payload["panes"][0]["tokens"]["output"] == 100
    assert payload["total_tokens"] == 300
    assert payload["total_cost"] > 0
