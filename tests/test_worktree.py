"""Tests for QuadMux v2 git worktree helpers."""

import os
import subprocess

import pytest

import worktree


def _run(cmd, cwd):
    subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=True)


@pytest.fixture
def fresh_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init", "-q", "-b", "main"], cwd=str(repo))
    _run(["git", "config", "user.email", "test@example.com"], cwd=str(repo))
    _run(["git", "config", "user.name", "Test"], cwd=str(repo))
    (repo / "README.md").write_text("hi\n")
    _run(["git", "add", "README.md"], cwd=str(repo))
    _run(["git", "commit", "-q", "-m", "init"], cwd=str(repo))
    return str(repo)


def test_is_git_repo_true_for_repo(fresh_repo):
    assert worktree.is_git_repo(fresh_repo) is True


def test_is_git_repo_false_for_plain_dir(tmp_path):
    assert worktree.is_git_repo(str(tmp_path)) is False


def test_is_git_repo_false_for_missing_path():
    assert worktree.is_git_repo("/nonexistent/path/that/does/not/exist") is False


def test_current_branch_returns_main(fresh_repo):
    assert worktree.current_branch(fresh_repo) == "main"


def test_create_and_prune_worktree(fresh_repo, tmp_path):
    dest = str(tmp_path / "wt-a")
    branch = "test/wt-a"
    ok = worktree.create_worktree(fresh_repo, dest, branch)
    assert ok is True
    assert os.path.isdir(dest)
    assert os.path.exists(os.path.join(dest, "README.md"))

    worktree.prune_worktree(fresh_repo, dest)
    assert not os.path.isdir(dest)

    worktree.prune_branch(fresh_repo, branch)
    # branch should be gone
    result = subprocess.run(
        ["git", "-C", fresh_repo, "branch", "--list", branch],
        capture_output=True, text=True,
    )
    assert result.stdout.strip() == ""


def test_setup_session_creates_one_per_role(fresh_repo, tmp_path, monkeypatch):
    root = tmp_path / "worktrees"
    monkeypatch.setattr(worktree, "WORKTREES_ROOT", str(root))
    created = worktree.setup_session_worktrees(
        fresh_repo, "test-session", ["planner", "impl-a", "impl-b", "reviewer"]
    )
    assert len(created) == 4
    paths = [c["path"] for c in created]
    assert len(set(paths)) == 4
    for c in created:
        assert os.path.isdir(c["path"])
        assert c["branch"].startswith("qm/test-session/")

    # Cleanup so we leave no branches behind
    for c in created:
        worktree.prune_worktree(fresh_repo, c["path"])
        worktree.prune_branch(fresh_repo, c["branch"])
