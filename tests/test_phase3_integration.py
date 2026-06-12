"""End-to-end test for phase 3: preset + worktree setup wired through main().

Avoids actually spawning Claude (or running asyncio) by patching
spawn_claude and asyncio.run before invoking main().
"""

import importlib.util
import os
import subprocess
import sys
import types
import uuid

import pytest


@pytest.fixture
def qm_module(monkeypatch):
    """Load quadmux-server.py as a module without hyphen import issues."""
    spec = importlib.util.spec_from_file_location(
        "qm_server",
        os.path.join(os.path.dirname(__file__), "..", "quadmux-server.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def fresh_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(repo), check=True)
    (repo / "README.md").write_text("x")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=str(repo), check=True)
    return str(repo)


def test_main_with_preset_creates_worktrees_and_passes_role_args(
    qm_module, fresh_repo, tmp_path, monkeypatch
):
    # Isolate worktree root
    monkeypatch.setattr(qm_module.worktree_mod, "WORKTREES_ROOT", str(tmp_path / "wts"))

    # Capture spawn_claude calls instead of actually forking
    calls = []

    def fake_spawn(claude_path, idx, rows=24, cols=80, cwd=None, extra_args=None):
        calls.append({"idx": idx, "cwd": cwd, "extra_args": list(extra_args or [])})
        return (1000 + idx, idx + 100)  # fake (pid, master_fd)

    monkeypatch.setattr(qm_module, "spawn_claude", fake_spawn)
    monkeypatch.setattr(qm_module, "find_claude", lambda: "/fake/claude")
    # Skip reader threads, port check, and asyncio.run
    monkeypatch.setattr(qm_module.threading, "Thread", lambda *a, **kw: types.SimpleNamespace(start=lambda: None, daemon=True))
    monkeypatch.setattr(qm_module, "port_is_free", lambda p: True)
    monkeypatch.setattr(qm_module.asyncio, "run", lambda *a, **kw: None)
    monkeypatch.setattr(qm_module.os, "kill", lambda *a, **kw: None)

    monkeypatch.setattr(qm_module.sys, "argv", [
        "quadmux-server.py",
        "--preset", "parallel-bugfix",
        "--repo", fresh_repo,
        "--port", "9999",
    ])

    qm_module.main()

    # All 4 panes spawned
    assert len(calls) == 4
    # Each has a worktree cwd (not the source repo)
    for c in calls:
        assert c["cwd"]
        assert c["cwd"] != fresh_repo
        assert c["cwd"].startswith(str(tmp_path))
        # Role system prompt passed via --append-system-prompt
        assert "--append-system-prompt" in c["extra_args"]
        prompt_idx = c["extra_args"].index("--append-system-prompt") + 1
        assert "bug-fixer" in c["extra_args"][prompt_idx]

    # Each worktree exists on disk and is a checkout of the repo
    for c in calls:
        assert os.path.isdir(c["cwd"])
        assert os.path.exists(os.path.join(c["cwd"], "README.md"))

    # pane_meta is populated with role + branch
    assert len(qm_module.pane_meta) == 4
    for i, meta in enumerate(qm_module.pane_meta):
        assert meta["role"] == f"fix-{i+1}"
        assert meta["branch"].startswith("qm/")
        assert "system_prompt" in meta

    # Cleanup: prune the worktrees so we don't leave branches in the test repo
    for c in calls:
        qm_module.worktree_mod.prune_worktree(fresh_repo, c["cwd"])
    for meta in qm_module.pane_meta:
        qm_module.worktree_mod.prune_branch(fresh_repo, meta["branch"])


def test_main_without_preset_uses_default_shells(qm_module, monkeypatch):
    calls = []

    def fake_spawn(claude_path, idx, rows=24, cols=80, cwd=None, extra_args=None):
        calls.append({"idx": idx, "cwd": cwd, "extra_args": list(extra_args or [])})
        return (2000 + idx, idx + 200)

    monkeypatch.setattr(qm_module, "spawn_claude", fake_spawn)
    monkeypatch.setattr(qm_module, "find_claude", lambda: "/fake/claude")
    monkeypatch.setattr(qm_module.threading, "Thread", lambda *a, **kw: types.SimpleNamespace(start=lambda: None, daemon=True))
    monkeypatch.setattr(qm_module, "port_is_free", lambda p: True)
    monkeypatch.setattr(qm_module.asyncio, "run", lambda *a, **kw: None)
    monkeypatch.setattr(qm_module.os, "kill", lambda *a, **kw: None)

    monkeypatch.setattr(qm_module.sys, "argv", [
        "quadmux-server.py",
        "--shells", "2",
        "--port", "9998",
    ])

    qm_module.main()

    assert len(calls) == 2
    # No preset = no system prompt, but every pane still gets a session id
    # so cost tracking can find its JSONL deterministically
    for c in calls:
        assert c["extra_args"][:1] == ["--session-id"]
        uuid.UUID(c["extra_args"][1])
        assert c["extra_args"][2:] == []
        # No worktree = cwd not forced
        assert c["cwd"] is None


def test_no_worktrees_flag_runs_in_repo_cwd(qm_module, fresh_repo, monkeypatch):
    calls = []

    def fake_spawn(claude_path, idx, rows=24, cols=80, cwd=None, extra_args=None):
        calls.append({"idx": idx, "cwd": cwd})
        return (3000 + idx, idx + 300)

    monkeypatch.setattr(qm_module, "spawn_claude", fake_spawn)
    monkeypatch.setattr(qm_module, "find_claude", lambda: "/fake/claude")
    monkeypatch.setattr(qm_module.threading, "Thread", lambda *a, **kw: types.SimpleNamespace(start=lambda: None, daemon=True))
    monkeypatch.setattr(qm_module, "port_is_free", lambda p: True)
    monkeypatch.setattr(qm_module.asyncio, "run", lambda *a, **kw: None)
    monkeypatch.setattr(qm_module.os, "kill", lambda *a, **kw: None)

    monkeypatch.setattr(qm_module.sys, "argv", [
        "quadmux-server.py",
        "--preset", "review-loop",
        "--repo", fresh_repo,
        "--no-worktrees",
        "--port", "9997",
    ])

    qm_module.main()

    assert len(calls) == 4
    for c in calls:
        # All panes share the same repo cwd, no worktrees
        assert c["cwd"] == fresh_repo
