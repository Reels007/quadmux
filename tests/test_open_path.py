"""Tests for the open-path guard and its WS dispatch.

Clicking a linkified path in a pane asks the server to open a file on the Mac.
That is the one place a browser message turns into a local `open` call, so the
guard in ``resolve_open_path`` is the thing worth pinning down: absolute paths
only, must exist, must sit under an allowed root, and the resolved realpath (not
the raw string) is what gets handed to `open`.
"""

import asyncio
import importlib.util
import json
import os

import pytest

from .test_ws_integration import FakeWS, _setup_server


@pytest.fixture
def qm():
    spec = importlib.util.spec_from_file_location(
        "qm_server",
        os.path.join(os.path.dirname(__file__), "..", "quadmux-server.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def rooted(qm, tmp_path, monkeypatch):
    """Point the allowed-roots list at tmp_path so tests never touch $HOME."""
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(qm, "_OPEN_ROOTS_CACHE", [str(root.resolve())])
    return root


# --- resolve_open_path -----------------------------------------------------

def test_accepts_existing_file_under_root(qm, rooted):
    f = rooted / "model.stl"
    f.write_text("solid")
    ok, resolved, err = qm.resolve_open_path(str(f))
    assert ok and err == ""
    assert resolved == str(f.resolve())


def test_accepts_directory_under_root(qm, rooted):
    d = rooted / "sub"
    d.mkdir()
    ok, resolved, err = qm.resolve_open_path(str(d))
    assert ok and resolved == str(d.resolve())


def test_accepts_path_with_spaces_and_parens(qm, rooted):
    # Sean's NATO project folders look like "Golf - AI & Automation (AI)".
    d = rooted / "Golf - AI & Automation (AI)"
    d.mkdir()
    f = d / "notes file.md"
    f.write_text("x")
    ok, resolved, err = qm.resolve_open_path(str(f))
    assert ok and resolved == str(f.resolve())


def test_strips_surrounding_quotes_and_backticks(qm, rooted):
    f = rooted / "a.py"
    f.write_text("x")
    for wrapped in ('"%s"' % f, "'%s'" % f, "`%s`" % f, "  %s  " % f):
        ok, resolved, err = qm.resolve_open_path(wrapped)
        assert ok, (wrapped, err)
        assert resolved == str(f.resolve())


def test_rejects_empty_and_non_string(qm, rooted):
    for bad in ("", "   ", None, 42, [], {}):
        ok, _, err = qm.resolve_open_path(bad)
        assert not ok and err == "empty path"


def test_rejects_relative_path(qm, rooted):
    ok, _, err = qm.resolve_open_path("relative/file.md")
    assert not ok and err == "not an absolute path"


def test_rejects_missing_file(qm, rooted):
    ok, _, err = qm.resolve_open_path(str(rooted / "nope.stl"))
    assert not ok and err == "not found"


def test_rejects_path_outside_roots(qm, rooted, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    ok, _, err = qm.resolve_open_path(str(outside))
    assert not ok and err == "outside allowed roots"


def test_rejects_traversal_escaping_root(qm, rooted, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    # ..-walk out of the allowed root and back down to a real file.
    sneaky = str(rooted / ".." / "outside.txt")
    ok, _, err = qm.resolve_open_path(sneaky)
    assert not ok and err == "outside allowed roots"


def test_rejects_symlink_pointing_outside_root(qm, rooted, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    link = rooted / "link.txt"
    link.symlink_to(outside)
    # The link itself is inside the root; realpath resolution must still catch it.
    ok, _, err = qm.resolve_open_path(str(link))
    assert not ok and err == "outside allowed roots"


def test_root_prefix_match_is_boundary_aware(qm, tmp_path, monkeypatch):
    # "/x/root" must not authorise a sibling "/x/rootless".
    root = tmp_path / "root"
    root.mkdir()
    sibling = tmp_path / "rootless"
    sibling.mkdir()
    f = sibling / "a.txt"
    f.write_text("x")
    monkeypatch.setattr(qm, "_OPEN_ROOTS_CACHE", [str(root.resolve())])
    ok, _, err = qm.resolve_open_path(str(f))
    assert not ok and err == "outside allowed roots"


def test_root_itself_is_allowed(qm, rooted):
    ok, resolved, err = qm.resolve_open_path(str(rooted))
    assert ok and resolved == str(rooted.resolve())


def test_expands_tilde(qm, tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    f = home / "in-home.md"
    f.write_text("x")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(qm, "_OPEN_ROOTS_CACHE", [str(home.resolve())])
    ok, resolved, err = qm.resolve_open_path("~/in-home.md")
    assert ok and resolved == str(f.resolve())


def test_default_roots_include_home_and_tmp(qm, monkeypatch):
    monkeypatch.setattr(qm, "_OPEN_ROOTS_CACHE", None)
    roots = qm._open_roots()
    assert os.path.realpath(os.path.expanduser("~")) in roots
    assert "/tmp" in roots
    # Cached after the first call so every click doesn't re-derive them.
    assert qm._open_roots() is roots


# --- WS dispatch -----------------------------------------------------------

def test_ws_open_path_launches_and_confirms(qm, tmp_path, monkeypatch, rooted):
    _setup_server(qm, 2, monkeypatch, tmp_path / "parked.json")
    f = rooted / "part.stl"
    f.write_text("solid")

    launched = []
    monkeypatch.setattr(qm, "launch_open", lambda p: launched.append(p))

    ws = FakeWS([json.dumps({"type": "open_path", "shell": 1, "path": str(f)})])
    asyncio.run(qm.handler(ws))

    assert launched == [str(f.resolve())]
    done = [json.loads(s) for s in ws.sent if '"open_path_done"' in s]
    assert done and done[0]["shell"] == 1
    assert done[0]["path"] == str(f.resolve())


def test_ws_open_path_rejects_without_launching(qm, tmp_path, monkeypatch, rooted):
    _setup_server(qm, 2, monkeypatch, tmp_path / "parked.json")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")

    launched = []
    monkeypatch.setattr(qm, "launch_open", lambda p: launched.append(p))

    ws = FakeWS([json.dumps({"type": "open_path", "shell": 0, "path": str(outside)})])
    asyncio.run(qm.handler(ws))

    assert launched == []
    errs = [json.loads(s) for s in ws.sent if '"open_path_error"' in s]
    assert errs and errs[0]["error"] == "outside allowed roots"
    # The rejected raw path is echoed back so the toast can name the file.
    assert errs[0]["path"] == str(outside)


def test_ws_open_path_reports_launch_failure(qm, tmp_path, monkeypatch, rooted):
    _setup_server(qm, 2, monkeypatch, tmp_path / "parked.json")
    f = rooted / "part.stl"
    f.write_text("solid")

    def boom(_p):
        raise OSError("no such binary: open")
    monkeypatch.setattr(qm, "launch_open", boom)

    ws = FakeWS([json.dumps({"type": "open_path", "shell": 0, "path": str(f)})])
    asyncio.run(qm.handler(ws))

    errs = [json.loads(s) for s in ws.sent if '"open_path_error"' in s]
    assert errs and "no such binary" in errs[0]["error"]
