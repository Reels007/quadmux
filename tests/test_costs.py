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
    assert p["input"] == 5.0 and p["output"] == 25.0


def test_prices_for_opus_5_pinned():
    p = costs.prices_for("claude-opus-5")
    assert p["input"] == 5.0 and p["output"] == 25.0
    assert p["cache_read"] == 0.5
    assert p["cache_write"] == 10.0 and p["cache_write_5m"] == 6.25


def test_prices_for_longest_prefix_wins():
    # "claude-opus-5" must beat the shorter "claude-opus" family entry
    # regardless of PRICE_TABLE insertion order.
    assert costs.prices_for("claude-opus-5")["cache_write"] == \
        costs.PRICE_TABLE["claude-opus-5"]["cache_write"]


def test_prices_for_fable():
    p = costs.prices_for("claude-fable-5")
    assert p["input"] == 10.0 and p["output"] == 50.0


def test_prices_for_sonnet():
    p = costs.prices_for("claude-sonnet-4-6")
    assert p["input"] == 3.0 and p["output"] == 15.0


def test_prices_for_unknown_falls_back_to_opus():
    p = costs.prices_for("nonsense-model")
    assert p["input"] == 5.0


def test_prices_env_override(monkeypatch):
    monkeypatch.setenv("QM_PRICE_INPUT", "1.5")
    p = costs.prices_for("claude-opus-4-7")
    assert p["input"] == 1.5


def test_compute_cost_basic():
    usage = {"input_tokens": 1_000_000, "output_tokens": 0}
    assert costs.compute_cost(usage, "claude-opus-4-7") == pytest.approx(5.0)


def test_compute_cost_blends_cache_buckets():
    usage = {
        "input_tokens": 100,
        "output_tokens": 200,
        "cache_read_input_tokens": 500,
        "cache_creation_input_tokens": 1000,
    }
    c = costs.compute_cost(usage, "claude-opus-4-7")
    # No per-TTL breakdown present, so all creation tokens bill at the 1h rate:
    # 100*5/M + 200*25/M + 500*0.5/M + 1000*10/M
    expected = (100 * 5 + 200 * 25 + 500 * 0.5 + 1000 * 10) / 1_000_000
    assert c == pytest.approx(expected)


def test_compute_cost_splits_cache_write_by_ttl():
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 1000,
        "cache_creation": {
            "ephemeral_1h_input_tokens": 400,
            "ephemeral_5m_input_tokens": 600,
        },
    }
    c = costs.compute_cost(usage, "claude-opus-5")
    assert c == pytest.approx((400 * 10.0 + 600 * 6.25) / 1_000_000)


def test_compute_cost_matches_api_reported_total():
    # Figures captured from a real claude-opus-5 run (--output-format json):
    # modelUsage["claude-opus-5"].costUSD == 0.195664
    usage = {
        "input_tokens": 2,
        "output_tokens": 7,
        "cache_read_input_tokens": 15738,
        "cache_creation_input_tokens": 18761,
        "cache_creation": {
            "ephemeral_1h_input_tokens": 18761,
            "ephemeral_5m_input_tokens": 0,
        },
    }
    assert costs.compute_cost(usage, "claude-opus-5") == pytest.approx(0.195664)


def test_cache_write_env_override_covers_both_ttls(monkeypatch):
    monkeypatch.setenv("QM_PRICE_CACHE_WRITE", "2.0")
    p = costs.prices_for("claude-opus-5")
    assert p["cache_write"] == 2.0 and p["cache_write_5m"] == 2.0


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


def _assistant(input_t, cache_read=0, cache_write=0, output=0, sidechain=False):
    line = {"type": "assistant",
            "message": {"model": "claude-opus-5",
                        "usage": {"input_tokens": input_t,
                                  "cache_read_input_tokens": cache_read,
                                  "cache_creation_input_tokens": cache_write,
                                  "output_tokens": output}}}
    if sidechain:
        line["isSidechain"] = True
    return json.dumps(line)


def test_context_used_sums_the_input_side():
    assert costs.context_used({"input_tokens": 2, "cache_read_input_tokens": 67190,
                               "cache_creation_input_tokens": 2756,
                               "output_tokens": 102}) == 69948


def test_context_limit_env_override(monkeypatch):
    assert costs.context_limit_for("claude-opus-5") == costs.DEFAULT_CONTEXT_LIMIT
    monkeypatch.setenv("QM_CONTEXT_LIMIT", "1000000")
    assert costs.context_limit_for("claude-opus-5") == 1_000_000
    monkeypatch.setenv("QM_CONTEXT_LIMIT", "nonsense")
    assert costs.context_limit_for("claude-opus-5") == costs.DEFAULT_CONTEXT_LIMIT


def test_context_tracks_latest_reply_not_a_running_total(tmp_path):
    f = tmp_path / "s.jsonl"
    f.write_text("\n".join([
        _assistant(10, cache_read=40_000),
        _assistant(10, cache_read=90_000),
    ]) + "\n")
    t = costs.CostTracker(str(f))
    t.poll()
    snap = t.snapshot()
    assert snap["context_tokens"] == 90_010
    assert snap["context_limit"] == costs.DEFAULT_CONTEXT_LIMIT
    assert snap["context_pct"] == pytest.approx(45.0, abs=0.1)
    # Cumulative token totals keep counting both replies.
    assert snap["tokens"]["cache_read"] == 130_000


def test_context_falls_back_after_compaction(tmp_path):
    f = tmp_path / "s.jsonl"
    f.write_text(_assistant(10, cache_read=180_000) + "\n")
    t = costs.CostTracker(str(f))
    t.poll()
    assert t.snapshot()["context_tokens"] == 180_010
    with open(f, "a") as fh:
        fh.write(_assistant(10, cache_read=12_000) + "\n")
    t.poll()
    assert t.snapshot()["context_tokens"] == 12_010


def test_context_ignores_subagent_replies(tmp_path):
    f = tmp_path / "s.jsonl"
    f.write_text("\n".join([
        _assistant(10, cache_read=50_000),
        _assistant(10, cache_read=150_000, sidechain=True),
    ]) + "\n")
    t = costs.CostTracker(str(f))
    t.poll()
    assert t.snapshot()["context_tokens"] == 50_010


def test_context_resets_on_attach(tmp_path):
    f = tmp_path / "s.jsonl"
    f.write_text(_assistant(10, cache_read=50_000) + "\n")
    t = costs.CostTracker(str(f))
    t.poll()
    assert t.context_tokens
    other = tmp_path / "s2.jsonl"
    other.write_text("")
    t.attach(str(other))
    snap = t.snapshot()
    assert snap["context_tokens"] == 0
    assert snap["context_pct"] == 0.0


def test_context_zero_when_no_session(tmp_path):
    t = costs.CostTracker(None)
    snap = t.snapshot()
    assert snap["context_tokens"] == 0
    assert snap["context_pct"] == 0.0
    assert snap["context_limit"] > 0


# --- session re-binding: --resume forks and /clear roll the JSONL ----------

CLEAR_LINE = json.dumps({
    "type": "user", "isMeta": True,
    "message": {"role": "user", "content":
                "<command-name>/clear</command-name><command-message>clear</command-message>"}})


def _copied_history_line(parent_sid, cache_read=880_000):
    return json.dumps({"type": "assistant", "sessionId": parent_sid,
                       "message": {"model": "claude-fable-5",
                                   "usage": {"input_tokens": 10,
                                             "cache_read_input_tokens": cache_read,
                                             "output_tokens": 5}}})


def _aged(path, seconds):
    import time
    old = time.time() - seconds
    os.utime(path, (old, old))


def test_context_limit_knows_fable_and_bumps_on_overflow():
    assert costs.context_limit_for("claude-fable-5") == costs.BIG_CONTEXT_LIMIT
    assert costs.context_limit_for("claude-opus-5") == costs.DEFAULT_CONTEXT_LIMIT
    # A reading above the nominal window proves the nominal window is wrong.
    assert costs.context_limit_for("claude-opus-5", seen_tokens=250_000) \
        == costs.BIG_CONTEXT_LIMIT


def test_rebind_follows_resume_fork_without_double_counting(tmp_path):
    parent = tmp_path / "1111aaaa-0000-0000-0000-000000000001.jsonl"
    parent.write_text(_assistant(10, cache_read=180_000) + "\n")
    _aged(parent, 30)
    t = costs.CostTracker(str(parent))
    t.poll()
    assert t.context_tokens == 180_010
    cost_before = t.cost

    fork = tmp_path / "2222bbbb-0000-0000-0000-000000000002.jsonl"
    fork.write_text(_copied_history_line("1111aaaa-0000-0000-0000-000000000001") + "\n")
    _aged(fork, 10)

    known = set()
    rebinds = costs.find_rebinds([t], [0.0], known)
    assert rebinds == [(0, str(fork), True)]

    t.attach(str(fork), from_end=True, keep_totals=True)
    t.poll()
    # The stale 180k reading is gone and the replayed history line was skipped:
    # neither its context nor its cost is counted again.
    assert t.context_tokens == 0
    assert t.cost == cost_before
    with open(fork, "a") as fh:
        fh.write(_assistant(10, cache_read=40_000) + "\n")
    t.poll()
    assert t.context_tokens == 40_010
    assert t.cost > cost_before
    assert t.session_ids == ["1111aaaa-0000-0000-0000-000000000001",
                             "2222bbbb-0000-0000-0000-000000000002"]


def test_rebind_clear_goes_to_the_pane_that_typed(tmp_path):
    import time
    a = tmp_path / "aaaa.jsonl"
    b = tmp_path / "bbbb.jsonl"
    for f in (a, b):
        f.write_text(_assistant(10, cache_read=50_000) + "\n")
        _aged(f, 120)
    ta, tb = costs.CostTracker(str(a)), costs.CostTracker(str(b))

    cleared = tmp_path / "cccc.jsonl"
    cleared.write_text(CLEAR_LINE + "\n" + _assistant(10, cache_read=15_000) + "\n")
    _aged(cleared, 10)

    now = time.time()
    known = set()
    # Pane 1 typed 5s before the clear file was born; pane 0 has been idle.
    rebinds = costs.find_rebinds([ta, tb], [0.0, now - 15], known, now=now)
    assert rebinds == [(1, str(cleared), False)]
    tb.attach(str(cleared), from_end=False, keep_totals=True)
    tb.poll()
    # Read from the start: the first post-clear reply is already counted.
    assert tb.context_tokens == 15_010


def test_rebind_leaves_unrelated_sessions_alone(tmp_path):
    mine = tmp_path / "aaaa.jsonl"
    mine.write_text(_assistant(10, cache_read=50_000) + "\n")
    _aged(mine, 120)
    t = costs.CostTracker(str(mine))

    other = tmp_path / "dddd.jsonl"
    other.write_text(_assistant(10, cache_read=9_000) + "\n")
    _aged(other, 10)

    known = set()
    assert costs.find_rebinds([t], [0.0], known) == []
    # Young and unmatched: not written off yet (its pane may not be attached).
    assert str(other) not in known
    _aged(other, costs.REBIND_GRACE_SECS + 5)
    assert costs.find_rebinds([t], [0.0], known) == []
    assert str(other) in known


def test_attach_keep_totals_preserves_cumulative_cost(tmp_path):
    f = tmp_path / "aaaa.jsonl"
    f.write_text(_assistant(10, cache_read=50_000, output=200) + "\n")
    t = costs.CostTracker(str(f))
    t.poll()
    assert t.cost > 0
    cost, tokens = t.cost, dict(t.tokens)
    g = tmp_path / "bbbb.jsonl"
    g.write_text("")
    t.attach(str(g), keep_totals=True)
    assert t.cost == cost and t.tokens == tokens
    assert t.context_tokens == 0
    assert t.session_ids == ["aaaa", "bbbb"]


def test_rebind_ignores_sessions_that_merely_mention_a_sid(tmp_path):
    """Seen live: a debugging session whose transcript QUOTED another pane's
    session id was claimed as that pane's fork. Bare uuid text is not
    lineage; only a structural "sessionId" field is."""
    mine = tmp_path / "1111aaaa-0000-0000-0000-000000000001.jsonl"
    mine.write_text(_assistant(10, cache_read=50_000) + "\n")
    _aged(mine, 120)
    t = costs.CostTracker(str(mine))

    import time
    gossip = tmp_path / "eeee.jsonl"
    # Filler first, so the quoted /clear marker lands PAST the head-of-file
    # line window a genuine caveat would sit inside.
    lines = [json.dumps({"type": "user", "message": {
        "role": "user", "content": "filler"}})
        for _ in range(costs.CLEAR_MARKER_SCAN_LINES)]
    lines.append(json.dumps({
        "type": "user",
        "message": {"role": "user", "content":
                    "pane attached to 1111aaaa-0000-0000-0000-000000000001.jsonl"
                    " and someone typed <command-name>/clear</command-name>"}}))
    gossip.write_text("\n".join(lines) + "\n")
    _aged(gossip, costs.REBIND_GRACE_SECS + 5)

    known = set()
    # The pane typed recently, so only the two content guards protect it:
    # bare uuid text is not a fork marker, and a deep /clear quote is not a
    # /clear caveat.
    now = time.time()
    assert costs.find_rebinds([t], [now - 15], known, now=now) == []
    assert str(gossip) in known
