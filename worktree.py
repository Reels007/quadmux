"""Git worktree helpers for QuadMux v2 role-mode sessions.

When QuadMux is launched with --preset and --repo, each pane gets its own
git worktree off ``~/.quadmux/worktrees/<session>/pane-<n>`` on a fresh
branch ``qm/<session>/<role>``. This isolates the role's work and lets us
diff between them cleanly.
"""

import os
import subprocess
import time
from typing import List, Optional

WORKTREES_ROOT = os.path.expanduser("~/.quadmux/worktrees")


def is_git_repo(path: str) -> bool:
    if not os.path.isdir(path):
        return False
    try:
        result = subprocess.run(
            ["git", "-C", path, "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def current_branch(path: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "-C", path, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def make_session_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def create_worktree(repo: str, dest: str, branch: str, base: Optional[str] = None) -> bool:
    """Create a worktree at ``dest`` on a new ``branch``. Returns True on success."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    args = ["git", "-C", repo, "worktree", "add", "-b", branch, dest]
    if base:
        args.append(base)
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"  Worktree create failed ({dest}): {e}", flush=True)
        return False
    if result.returncode != 0:
        print(f"  Worktree create failed ({dest}): {result.stderr.strip()}", flush=True)
        return False
    return True


def prune_worktree(repo: str, path: str) -> None:
    """Remove a worktree. Force-removes if working tree is dirty (we own it)."""
    try:
        subprocess.run(
            ["git", "-C", repo, "worktree", "remove", "--force", path],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass


def prune_branch(repo: str, branch: str) -> None:
    """Delete a branch (force, since we created it for the session)."""
    try:
        subprocess.run(
            ["git", "-C", repo, "branch", "-D", branch],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass


def setup_session_worktrees(repo: str, session_id: str,
                            role_names: List[str], base: Optional[str] = None
                            ) -> List[dict]:
    """Create one worktree per role. Returns a list of metadata dicts:

        [{ "role": str, "path": str, "branch": str }, ...]

    Roles that fail to create are skipped (caller should fall back to repo cwd).
    """
    session_dir = os.path.join(WORKTREES_ROOT, session_id)
    os.makedirs(session_dir, exist_ok=True)
    out = []
    for i, role in enumerate(role_names):
        safe_role = role.replace("/", "-")
        path = os.path.join(session_dir, f"pane-{i+1}-{safe_role}")
        branch = f"qm/{session_id}/{safe_role}"
        if create_worktree(repo, path, branch, base):
            out.append({"role": role, "path": path, "branch": branch})
    return out
