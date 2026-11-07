"""Tests for /api/file: how path clicks open when QuadMux is viewed over Tailscale.

Remotely, `open` would fire on the server's screen, so the browser fetches the
file instead. Same root guard as open_path, and every response is CSP-sandboxed
so served HTML cannot script QuadMux's origin.
"""

import importlib.util
import os

import pytest


@pytest.fixture
def qm():
    spec = importlib.util.spec_from_file_location(
        "qm_server_remote_file",
        os.path.join(os.path.dirname(__file__), "..", "quadmux-server.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _h(headers):
    return {k.lower(): v for k, v in headers}


def _root(qm, monkeypatch, path):
    monkeypatch.setattr(qm, "_open_roots", lambda: [os.path.realpath(str(path))])


def test_text_file_inline(qm, tmp_path, monkeypatch):
    _root(qm, monkeypatch, tmp_path)
    f = tmp_path / "notes.py"
    f.write_text("print('hi')\n")
    status, headers, body = qm.build_file_response(str(f))
    h = _h(headers)
    assert status == 200 and body == b"print('hi')\n"
    assert h["content-type"].startswith("text/plain")
    assert h["content-disposition"].startswith("inline")
    assert h["content-security-policy"] == "sandbox"


def test_html_is_sandboxed(qm, tmp_path, monkeypatch):
    _root(qm, monkeypatch, tmp_path)
    f = tmp_path / "page.html"
    f.write_text("<script>alert(1)</script>")
    status, headers, _ = qm.build_file_response(str(f))
    h = _h(headers)
    assert status == 200 and h["content-type"] == "text/html"
    assert h["content-security-policy"] == "sandbox"


def test_binary_downloads(qm, tmp_path, monkeypatch):
    _root(qm, monkeypatch, tmp_path)
    f = tmp_path / "part.stl"
    f.write_bytes(b"\x00\x01")
    status, headers, _ = qm.build_file_response(str(f))
    assert status == 200 and _h(headers)["content-disposition"].startswith("attachment")


def test_directory_listing_escapes_names(qm, tmp_path, monkeypatch):
    _root(qm, monkeypatch, tmp_path)
    (tmp_path / "<b>x").write_text("")
    (tmp_path / "sub").mkdir()
    status, headers, body = qm.build_file_response(str(tmp_path))
    assert status == 200 and _h(headers)["content-type"].startswith("text/html")
    assert b"&lt;b&gt;x" in body and b"<b>x" not in body
    assert b"sub/" in body


def test_outside_roots_rejected(qm, tmp_path, monkeypatch):
    (tmp_path / "allowed").mkdir()
    _root(qm, monkeypatch, tmp_path / "allowed")
    secret = tmp_path / "secret.txt"
    secret.write_text("x")
    status, _, _ = qm.build_file_response(str(secret))
    assert status == 404


def test_symlink_escape_rejected(qm, tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (tmp_path / "secret.txt").write_text("x")
    os.symlink(tmp_path / "secret.txt", allowed / "link.txt")
    _root(qm, monkeypatch, allowed)
    status, _, _ = qm.build_file_response(str(allowed / "link.txt"))
    assert status == 404


def test_size_cap(qm, tmp_path, monkeypatch):
    _root(qm, monkeypatch, tmp_path)
    monkeypatch.setattr(qm, "FILE_MAX_BYTES", 10)
    f = tmp_path / "big.txt"
    f.write_text("x" * 11)
    status, _, _ = qm.build_file_response(str(f))
    assert status == 413
