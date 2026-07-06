"""Tests for find_session_cwd: restart-resume must spawn panes in the
session's original cwd, because `claude --resume <sid>` only finds sessions
belonging to the current directory's project (the 17:52 dead-panes bug).
"""

import importlib.util
import json
import os

import pytest


@pytest.fixture
def qm_module():
    """Load quadmux-server.py as a module without hyphen import issues."""
    spec = importlib.util.spec_from_file_location(
        "qm_server_resume",
        os.path.join(os.path.dirname(__file__), "..", "quadmux-server.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    projects = tmp_path / ".claude" / "projects"
    projects.mkdir(parents=True)
    return tmp_path


def _write_session(projects_dir, folder, sid, lines):
    d = projects_dir / folder
    d.mkdir(exist_ok=True)
    with open(d / f"{sid}.jsonl", "w") as f:
        for entry in lines:
            f.write(json.dumps(entry) + "\n")


def test_returns_cwd_recorded_in_jsonl(qm_module, fake_home, tmp_path):
    real_cwd = tmp_path / "some" / "project"
    real_cwd.mkdir(parents=True)
    projects = fake_home / ".claude" / "projects"
    _write_session(projects, "-whatever", "sid-1", [
        {"type": "summary"},
        {"cwd": str(real_cwd), "type": "user"},
    ])
    assert qm_module.find_session_cwd("sid-1") == str(real_cwd)


def test_unknown_session_returns_none(qm_module, fake_home):
    assert qm_module.find_session_cwd("no-such-sid") is None


def test_root_project_folder_decodes_to_slash(qm_module, fake_home):
    # Folder "-" is the project for cwd "/". No cwd field in any line, so
    # the folder-name fallback must decode it.
    projects = fake_home / ".claude" / "projects"
    _write_session(projects, "-", "sid-root", [{"type": "summary"}])
    assert qm_module.find_session_cwd("sid-root") == "/"


def test_stale_recorded_cwd_falls_back_to_folder_decode(qm_module, fake_home, tmp_path):
    # Recorded cwd no longer exists on disk; decoded folder path does.
    gone = tmp_path / "deleted-dir"
    projects = fake_home / ".claude" / "projects"
    _write_session(projects, "-", "sid-stale", [{"cwd": str(gone), "type": "user"}])
    assert qm_module.find_session_cwd("sid-stale") == "/"
