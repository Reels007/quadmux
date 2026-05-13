"""Tests for QuadMux v2 role presets."""

import json
import presets


def test_default_presets_present():
    names = presets.list_preset_names()
    for required in ("planner+2impl+reviewer", "parallel-bugfix", "review-loop"):
        assert required in names


def test_get_preset_returns_role_list():
    roles = presets.get_preset("planner+2impl+reviewer")
    assert len(roles) == 4
    assert {r["name"] for r in roles} == {"planner", "impl-a", "impl-b", "reviewer"}
    for r in roles:
        assert r["system_prompt"]  # non-empty


def test_parallel_bugfix_has_four_distinct_roles():
    roles = presets.get_preset("parallel-bugfix")
    names = [r["name"] for r in roles]
    assert len(names) == 4
    assert len(set(names)) == 4


def test_unknown_preset_returns_none():
    assert presets.get_preset("not-a-real-preset") is None


def test_user_presets_override_defaults(tmp_path, monkeypatch):
    custom_path = tmp_path / "presets.json"
    custom_path.write_text(json.dumps({
        "planner+2impl+reviewer": [
            {"name": "custom", "system_prompt": "you are custom"}
        ],
        "my-flow": [
            {"name": "alpha", "system_prompt": "a"},
            {"name": "beta",  "system_prompt": "b"},
        ],
    }))
    monkeypatch.setattr(presets, "PRESETS_PATH", str(custom_path))

    # User override wins
    pp = presets.get_preset("planner+2impl+reviewer")
    assert len(pp) == 1 and pp[0]["name"] == "custom"

    # New preset is available
    assert "my-flow" in presets.list_preset_names()

    # Defaults that weren't overridden are still present
    assert presets.get_preset("parallel-bugfix") is not None


def test_malformed_user_presets_file_does_not_crash(tmp_path, monkeypatch, capsys):
    bad = tmp_path / "presets.json"
    bad.write_text("{not valid json")
    monkeypatch.setattr(presets, "PRESETS_PATH", str(bad))
    # Should fall back to defaults silently
    assert presets.get_preset("review-loop") is not None
