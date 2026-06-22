"""Tests for QuadMux v2 cost tracking (phase 5)."""

import json
import os

import pytest

import costs


def _make_session_dir(tmp_path, cwd_path, usage_lines):
    """Build a fake ~/.claude/projects/<enc>/<uuid>.jsonl for `cwd_path`."""
    proj = tmp_path / "projects"
    enc = costs._encoded_cwd(cwd_path)
    sub = proj / enc
    sub.mkdir(parents=True)
    f = sub / "abc.jsonl"
    lines = []
    for u in usage_lines:
        lines.append(json.dumps({
            "type": "assistant",
            "cwd": cwd_path,
            "message": {"model": "claude-opus-4-7", "usage": u},
        }))
    f.write_text("\n".join(lines) + "\n")
    return proj, f


def test_prices_for_opus_default():
    p = costs.prices_for("claude-opus-4-7")
    assert p["input"] == 15.0 and p["output"] == 75.0


def test_prices_for_sonnet():
    p = costs.prices_for("claude-sonnet-4-6")
    assert p["input"] == 3.0 and p["output"] == 15.0


def test_prices_for_unknown_falls_back_to_opus():
    p = costs.prices_for("nonsense-model")
    assert p["input"] == 15.0


def test_prices_env_override(monkeypatch):
    monkeypatch.setenv("QM_PRICE_INPUT", "1.5")
    p = costs.prices_for("claude-opus-4-7")
    assert p["input"] == 1.5


def test_compute_cost_basic():
    usage = {"input_tokens": 1_000_000, "output_tokens": 0}
    assert costs.compute_cost(usage, "claude-opus-4-7") == pytest.approx(15.0)


def test_compute_cost_blends_cache_buckets():
    usage = {
        "input_tokens": 100,
        "output_tokens": 200,
        "cache_read_input_tokens": 500,
        "cache_creation_input_tokens": 1000,
    }
    c = costs.compute_cost(usage, "claude-opus-4-7")
    # quick manual check: 100*15/M + 200*75/M + 500*1.5/M + 1000*18.75/M
    expected = (100 * 15 + 200 * 75 + 500 * 1.5 + 1000 * 18.75) / 1_000_000
    assert c == pytest.approx(expected)


def test_session_files_finds_match(tmp_path, monkeypatch):
    cwd = "/tmp/qm-cost-test-cwd"
    proj, f = _make_session_dir(tmp_path, cwd, [{"input_tokens": 10, "output_tokens": 5}])
    monkeypatch.setattr(costs, "PROJECTS_DIR", str(proj))
    files = costs.session_files_for_cwd(cwd)
    assert len(files) == 1
    assert files[0] == str(f)


def test_session_files_filters_by_cwd(tmp_path, monkeypatch):
    cwd_target = "/tmp/qm-cost-target"
    cwd_other = "/tmp/qm-cost-other"
    proj, _ = _make_session_dir(tmp_path, cwd_target, [{"input_tokens": 1, "output_tokens": 0}])
    # Drop a foreign file in target dir whose first-line cwd is different
    enc = costs._encoded_cwd(cwd_target)
    foreign = proj / enc / "foreign.jsonl"
    foreign.write_text(json.dumps({"cwd": cwd_other, "message": {}}) + "\n")
    monkeypatch.setattr(costs, "PROJECTS_DIR", str(proj))
    files = costs.session_files_for_cwd(cwd_target)
    assert all("foreign" not in f for f in files)


def test_assign_session_files_handles_shared_cwd(tmp_path, monkeypatch):
    cwd = "/tmp/qm-cost-shared"
    proj, f1 = _make_session_dir(tmp_path, cwd, [{"input_tokens": 1, "output_tokens": 0}])
    # Add a second session for the same cwd
    f2 = proj / costs._encoded_cwd(cwd) / "second.jsonl"
    f2.write_text(json.dumps({"cwd": cwd, "message": {}}) + "\n")
    monkeypatch.setattr(costs, "PROJECTS_DIR", str(proj))
    # Two panes both using the same cwd; each should get a different file
    out = costs.assign_session_files([cwd, cwd])
    assert out[0] and out[1]
    assert out[0] != out[1]


def test_cost_tracker_accumulates_across_polls(tmp_path):
    f = tmp_path / "session.jsonl"
    f.write_text(json.dumps({
        "message": {"model": "claude-opus-4-7",
                    "usage": {"input_tokens": 100, "output_tokens": 50}}
    }) + "\n")
    t = costs.CostTracker(str(f))
    assert t.poll() is True
    snap1 = t.snapshot()
    assert snap1["tokens"]["input"] == 100
    assert snap1["tokens"]["output"] == 50
    assert snap1["cost"] > 0

    # Append more usage
    with open(f, "a") as fh:
        fh.write(json.dumps({
            "message": {"model": "claude-opus-4-7",
                        "usage": {"input_tokens": 50, "output_tokens": 25}}
        }) + "\n")
    assert t.poll() is True
    snap2 = t.snapshot()
    assert snap2["tokens"]["input"] == 150
    assert snap2["tokens"]["output"] == 75


def test_cost_tracker_no_change_returns_false(tmp_path):
    f = tmp_path / "s.jsonl"
    f.write_text(json.dumps({
        "message": {"usage": {"input_tokens": 1, "output_tokens": 1}}
    }) + "\n")
    t = costs.CostTracker(str(f))
    assert t.poll() is True
    assert t.poll() is False  # nothing new


def test_cost_tracker_skips_lines_without_usage(tmp_path):
    f = tmp_path / "s.jsonl"
    f.write_text("\n".join([
        json.dumps({"type": "snapshot", "snapshot": "no usage here"}),
        json.dumps({"message": {"usage": {"input_tokens": 5, "output_tokens": 5}}}),
    ]) + "\n")
    t = costs.CostTracker(str(f))
    t.poll()
    assert t.tokens["input"] == 5


def test_cost_tracker_missing_path_is_inert():
    t = costs.CostTracker(None)
    assert t.poll() is False
    snap = t.snapshot()
    assert snap["total_tokens"] == 0
    assert snap["cost"] == 0.0


def test_session_files_fallback_when_encoding_differs(tmp_path, monkeypatch):
    """Paths with spaces / special chars don't get a simple '/' -> '-' encoding.
    We should still find the session via a fallback scan."""
    cwd = "/tmp/qm has spaces & punct"
    # Claude actually encodes spaces/special chars as multiple dashes;
    # simulate that with a deliberately different dir name.
    proj = tmp_path / "projects"
    weird_dir = proj / "-tmp-qm-has-spaces---punct"
    weird_dir.mkdir(parents=True)
    f = weird_dir / "session.jsonl"
    f.write_text(json.dumps({
        "cwd": cwd,
        "message": {"model": "claude-opus-4-7",
                    "usage": {"input_tokens": 1, "output_tokens": 1}},
    }) + "\n")
    monkeypatch.setattr(costs, "PROJECTS_DIR", str(proj))

    files = costs.session_files_for_cwd(cwd)
    assert len(files) == 1
    assert files[0] == str(f)


def test_task_extracted_from_user_prompt(tmp_path):
    f = tmp_path / "s.jsonl"
    f.write_text("\n".join([
        json.dumps({"type": "user", "message": {"role": "user",
                    "content": "build the fundraising tracker"}}),
        json.dumps({"type": "assistant", "message": {"role": "assistant", "model": "claude-opus-4-7",
                    "usage": {"input_tokens": 1, "output_tokens": 1}}}),
        json.dumps({"type": "user", "message": {"role": "user",
                    "content": [{"type": "tool_result", "content": "ignored"}]}}),
        json.dumps({"type": "user", "message": {"role": "user",
                    "content": "<command-name>/clear</command-name>"}}),
    ]) + "\n")
    t = costs.CostTracker(str(f))
    t.poll()
    snap = t.snapshot()
    assert snap["task"] == "build the fundraising tracker"


def test_task_truncated_to_title_length(tmp_path):
    long_prompt = "x" * 100
    f = tmp_path / "s.jsonl"
    f.write_text(json.dumps({"type": "user",
                             "message": {"role": "user", "content": long_prompt}}) + "\n")
    t = costs.CostTracker(str(f))
    t.poll()
    assert len(t.task) <= costs.TASK_MAX_CHARS
    assert t.task.endswith("...")
