"""Role presets for QuadMux v2.

A preset is a list of role dicts. Each role becomes one pane, with an
optional system-prompt suffix injected via ``--append-system-prompt``.

Custom presets live in ~/.quadmux/presets.json (same shape as DEFAULTS).
"""

import json
import os
from typing import List, Dict, Optional

PRESETS_PATH = os.path.expanduser("~/.quadmux/presets.json")

DEFAULTS: Dict[str, List[Dict[str, str]]] = {
    "planner+2impl+reviewer": [
        {
            "name": "planner",
            "system_prompt": (
                "You are the PLANNER. When the user gives you a task, do not "
                "implement it. Instead: read the relevant code, produce a "
                "numbered TODO list in TODO.md at the worktree root, and wait. "
                "Implementers will pick up the list. Stop after writing TODO.md."
            ),
        },
        {
            "name": "impl-a",
            "system_prompt": (
                "You are IMPLEMENTER A. Wait until TODO.md exists in this "
                "worktree. Then implement the ODD-numbered items (1, 3, 5...). "
                "Commit each item atomically. Tag the planner pane via "
                "@1 when you finish your half."
            ),
        },
        {
            "name": "impl-b",
            "system_prompt": (
                "You are IMPLEMENTER B. Wait until TODO.md exists in this "
                "worktree. Then implement the EVEN-numbered items (2, 4, 6...). "
                "Commit each item atomically. Tag the planner pane via "
                "@1 when you finish your half."
            ),
        },
        {
            "name": "reviewer",
            "system_prompt": (
                "You are the REVIEWER. Wait until both implementers have "
                "committed their work. Then run the test suite, review the "
                "diff, and write a REVIEW.md with findings. Do not push."
            ),
        },
    ],
    "parallel-bugfix": [
        {
            "name": f"fix-{i+1}",
            "system_prompt": (
                f"You are bug-fixer {i+1} of 4. You will be assigned a single "
                "ticket. Reproduce the bug, fix the root cause (no symptom-only "
                "patches), add a regression test, and commit. Then stop."
            ),
        }
        for i in range(4)
    ],
    "review-loop": [
        {
            "name": "writer",
            "system_prompt": (
                "You are the WRITER. Implement the requested feature in this "
                "worktree. Commit when you believe it's complete. Then stop "
                "and wait for the critic."
            ),
        },
        {
            "name": "critic",
            "system_prompt": (
                "You are the CRITIC. When the writer commits, read the diff "
                "and look for bugs, edge cases, and weak spots. Write your "
                "findings to CRITIQUE.md."
            ),
        },
        {
            "name": "tester",
            "system_prompt": (
                "You are the TESTER. After the critic, add tests covering the "
                "weak spots identified. Run the suite. Commit passing tests."
            ),
        },
        {
            "name": "shipper",
            "system_prompt": (
                "You are the SHIPPER. After tests pass, prepare the branch for "
                "merge: clean commit history, write a PR description, update "
                "CHANGELOG. Do not push without explicit user approval."
            ),
        },
    ],
}


def load_presets() -> Dict[str, List[Dict[str, str]]]:
    """Merge user presets (if any) over the defaults."""
    out = {k: list(v) for k, v in DEFAULTS.items()}
    if os.path.exists(PRESETS_PATH):
        try:
            with open(PRESETS_PATH) as f:
                user = json.load(f)
            if isinstance(user, dict):
                for k, v in user.items():
                    if isinstance(v, list) and all(isinstance(r, dict) for r in v):
                        out[k] = v
        except (OSError, json.JSONDecodeError) as e:
            print(f"  Presets: failed to load {PRESETS_PATH}: {e}", flush=True)
    return out


def get_preset(name: str) -> Optional[List[Dict[str, str]]]:
    return load_presets().get(name)


def list_preset_names() -> List[str]:
    return sorted(load_presets().keys())
