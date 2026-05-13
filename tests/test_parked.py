"""Tests for the QuadMux parked-tasks store."""

import json

import pytest

import parked


@pytest.fixture
def store(tmp_path, monkeypatch):
    p = tmp_path / "parked.json"
    monkeypatch.setattr(parked, "PARKED_PATH", str(p))
    return p


def test_list_empty_when_no_file(store):
    assert parked.list_tasks() == []


def test_add_task_returns_full_record(store):
    t = parked.add_task("First task", note="some note", pane=2)
    assert t["id"] == 1
    assert t["title"] == "First task"
    assert t["note"] == "some note"
    assert t["status"] == "parked"
    assert t["pane"] == 2
    assert "created" in t and "updated" in t


def test_add_task_persists_to_disk(store):
    parked.add_task("Persisted")
    data = json.loads(store.read_text())
    assert data["tasks"][0]["title"] == "Persisted"


def test_add_task_requires_title(store):
    with pytest.raises(ValueError):
        parked.add_task("   ")


def test_add_task_clamps_invalid_status(store):
    t = parked.add_task("X", status="bogus")
    assert t["status"] == "parked"


def test_add_task_ids_are_unique(store):
    t1 = parked.add_task("A")
    t2 = parked.add_task("B")
    t3 = parked.add_task("C")
    assert {t1["id"], t2["id"], t3["id"]} == {1, 2, 3}


def test_update_changes_fields(store):
    t = parked.add_task("Original")
    updated = parked.update_task(t["id"], title="Renamed", status="in-progress", pane=1)
    assert updated["title"] == "Renamed"
    assert updated["status"] == "in-progress"
    assert updated["pane"] == 1
    assert updated["updated"] >= t["updated"]


def test_update_ignores_unknown_fields(store):
    t = parked.add_task("X")
    updated = parked.update_task(t["id"], bogus="value", title="Y")
    assert updated["title"] == "Y"
    assert "bogus" not in updated


def test_update_ignores_invalid_status(store):
    t = parked.add_task("X", status="parked")
    updated = parked.update_task(t["id"], status="bogus")
    assert updated["status"] == "parked"


def test_update_missing_id_returns_none(store):
    assert parked.update_task(999, title="X") is None


def test_delete_removes_task(store):
    t = parked.add_task("Doomed")
    assert parked.delete_task(t["id"]) is True
    assert parked.list_tasks() == []


def test_delete_missing_id_returns_false(store):
    assert parked.delete_task(999) is False


def test_corrupt_file_recovers_gracefully(store):
    store.write_text("{not json")
    assert parked.list_tasks() == []
    t = parked.add_task("After corruption")
    # Auto-recovers and starts a fresh sequence
    assert t["id"] == 1


def test_title_clamped(store):
    t = parked.add_task("A" * 500)
    assert len(t["title"]) == 200


def test_note_clamped(store):
    t = parked.add_task("title", note="x" * 5000)
    assert len(t["note"]) == 2000
