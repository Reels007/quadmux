import json
import nudge


def _cfg(tmp_path, **kw):
    p = tmp_path / "policy.json"
    p.write_text(json.dumps(kw))
    return str(p)


def test_off_by_default(tmp_path):
    n = nudge.CompactNudger(2, _cfg(tmp_path))
    assert not n.enabled
    assert n.check(0, 990_000, "idle") is None


def test_missing_file_is_off(tmp_path):
    n = nudge.CompactNudger(2, str(tmp_path / "absent.json"))
    assert n.check(0, 990_000, "idle") is None


def test_nudges_once_per_crossing_and_only_when_idle(tmp_path):
    n = nudge.CompactNudger(2, _cfg(tmp_path, auto_compact_tokens=150_000))
    assert n.check(0, 60_000, "idle") is None
    assert n.check(0, 180_000, "thinking") is None        # busy: wait
    msg = n.check(0, 180_000, "idle")
    assert msg and "180k" in msg and "/compact" in msg
    assert n.check(0, 185_000, "idle") is None            # same crossing: silent
    assert n.check(0, 30_000, "idle") is None            # compact happened: re-arm
    assert n.check(0, 151_000, "idle")                    # next crossing fires
    assert n.check(1, 190_000, "idle")                    # panes independent


def test_custom_message_and_hot_reload(tmp_path):
    path = _cfg(tmp_path, auto_compact_tokens=50_000, auto_compact_message="ctx {k}k save+compact")
    n = nudge.CompactNudger(1, path)
    assert n.check(0, 55_000, "idle") == "ctx 55k save+compact"
    import os, time
    with open(path, "w") as f:
        json.dump({"auto_compact_tokens": 0}, f)
    os.utime(path, (time.time() + 5, time.time() + 5))
    assert n.check(0, 20_000, "idle") is None
    assert n.check(0, 990_000, "idle") is None            # reloaded: off
