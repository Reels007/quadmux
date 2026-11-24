"""Tests for the request guard and the credential denylist.

Pane input is code execution and /api/file serves files from the home folder,
so a request has to address this machine by a known name (Host) and, when a
browser says where it came from (Origin), come from QuadMux itself.
"""

import importlib.util
import os

import pytest
from websockets.datastructures import Headers

TAILNET = "mac-mini.example.ts.net"


@pytest.fixture
def qm(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "qm_server_request_guard",
        os.path.join(os.path.dirname(__file__), "..", "quadmux-server.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "_tailnet_names", lambda: frozenset({TAILNET, "100.64.0.7"}))
    # tailscale serve publishes this server on :443 and :8443 of the tailnet name.
    monkeypatch.setattr(mod, "_serve_origins", lambda: frozenset({(TAILNET, ""), (TAILNET, "8443")}))
    return mod


def _why(qm, path="/", **h):
    return qm.request_rejection(Headers([(k.replace("_", "-"), v) for k, v in h.items()]), path)


# --- Host -------------------------------------------------------------------

@pytest.mark.parametrize("host", [
    "localhost:8766", "127.0.0.1:8766", "[::1]:8766", "LOCALHOST:8766",
    TAILNET, TAILNET + ":8443", "100.64.0.7:8766",
])
def test_known_hosts_pass(qm, host):
    assert _why(qm, Host=host) == ""


@pytest.mark.parametrize("host", [
    "evil.example:8766",            # DNS rebinding: attacker name, our address
    "localhost.evil.example:8766",
    "evil.example.ts.net",
    "",
])
def test_unknown_hosts_refused(qm, host):
    assert "not allowed" in _why(qm, Host=host)


def test_missing_host_refused(qm):
    assert "not allowed" in qm.request_rejection(Headers([]), "/")


def test_allow_host_extra(qm):
    assert _why(qm, Host="studio.lan:8766") != ""
    qm.EXTRA_ALLOWED_HOSTS.add("studio.lan")
    assert _why(qm, Host="studio.lan:8766") == ""


def test_bind_host_is_allowed(qm, monkeypatch):
    monkeypatch.setattr(qm, "BIND_HOST", "192.168.4.20")
    assert _why(qm, Host="192.168.4.20:8766") == ""


# --- Origin -----------------------------------------------------------------

def test_no_origin_passes_on_host(qm):
    # curl, the tray and local scripts send no Origin.
    assert _why(qm, Host="localhost:8766", Upgrade="websocket") == ""


def test_same_origin_local(qm):
    assert _why(qm, Host="localhost:8766", Origin="http://localhost:8766") == ""


def test_same_origin_tailnet(qm):
    assert _why(qm, Host=TAILNET + ":8443", Origin=f"https://{TAILNET}:8443") == ""
    assert _why(qm, Host=TAILNET, Origin=f"https://{TAILNET}") == ""


@pytest.mark.parametrize("origin", [
    "https://evil.example",          # any site open in a browser on this Mac
    "http://evil.example:8766",
    "null",                          # sandboxed frame, file:// page
    "file://",
    "chrome-extension://abcdef",
    "",
])
def test_foreign_origin_refused(qm, origin):
    assert "not allowed" in _why(qm, Host="localhost:8766", Origin=origin)


def test_other_local_port_refused(qm):
    # Another web app on this Mac (a dev server, say) is not QuadMux.
    why = _why(qm, Host="localhost:8766", Origin="http://localhost:3000")
    assert "different site on this machine" in why
    assert "different site on this machine" in _why(
        qm, Host="localhost:8766", Origin="http://127.0.0.1:8766")


def test_other_tailnet_port_refused(qm):
    # Another service published under the same tailnet name is not QuadMux.
    for host in (TAILNET + ":8443", "localhost:8766"):
        why = _why(qm, Host=host, Origin=f"https://{TAILNET}:8000")
        assert "different site on this machine" in why


def test_served_origin_passes_when_proxy_rewrites_host(qm):
    assert _why(qm, Host="localhost:8766", Origin=f"https://{TAILNET}:8443") == ""
    assert _why(qm, Host="localhost:8766", Origin=f"https://{TAILNET}") == ""
    assert _why(qm, Host="localhost:8766", Origin=f"https://{TAILNET}:443") == ""


def test_origins_from_serve_status(qm):
    status = {"Web": {
        f"{TAILNET}:443": {"Handlers": {"/": {"Proxy": "http://localhost:8766"}}},
        f"{TAILNET}:8443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8766"},
                                         "/kokoro": {"Proxy": "http://localhost:8880"}}},
        f"{TAILNET}:8000": {"Handlers": {"/": {"Proxy": "http://localhost:8000"}}},
        f"{TAILNET}:9000": {"Handlers": {"/x": {"Proxy": "http://localhost:8766"}}},
        f"{TAILNET}:9001": {"Handlers": {"/": {"Proxy": "http://otherbox:8766"}}},
        f"{TAILNET}:9002": {"Handlers": {"/": {"Path": "/srv"}}},
    }}
    assert qm.origins_from_serve_status(status, 8766) == {(TAILNET, ""), (TAILNET, "8443")}
    assert qm.origins_from_serve_status(status, None) == frozenset()
    assert qm.origins_from_serve_status({}, 8766) == frozenset()
    assert qm.origins_from_serve_status({"Web": None}, 8766) == frozenset()


def test_repeated_host_or_origin_refused(qm):
    h = Headers([("Host", "localhost:8766"), ("Host", "evil.example")])
    assert qm.request_rejection(h, "/") == "repeated Host or Origin header"
    h = Headers([("Host", "localhost:8766"), ("Origin", "http://localhost:8766"),
                 ("Origin", "https://evil.example")])
    assert qm.request_rejection(h, "/") == "repeated Host or Origin header"


# --- /api/* reached from another site ---------------------------------------

API_PATHS = ["/api/file?path=/tmp/x", "/api/voice/start", "/api/tts/elevenlabs?text=hi",
             "/api/account", "/api/health/voice"]


@pytest.mark.parametrize("path", API_PATHS)
@pytest.mark.parametrize("site,mode", [
    ("cross-site", "no-cors"),      # <script src> / <img src> on another site
    ("cross-site", "navigate"),     # a link or frame on another site
    ("same-site", "navigate"),      # another port on this machine's name
    ("same-site", "cors"),
])
def test_api_request_from_another_site_refused(qm, path, site, mode):
    why = _why(qm, path, Host="localhost:8766", Sec_Fetch_Site=site, Sec_Fetch_Mode=mode)
    assert why == f"api request from another site ({site})"


@pytest.mark.parametrize("path", API_PATHS)
@pytest.mark.parametrize("site", ["same-origin", "none", None])
def test_api_request_from_quadmux_or_typed_url_passes(qm, path, site):
    h = {"Host": "localhost:8766"}
    if site:
        h["Sec_Fetch_Site"] = site
    assert _why(qm, path, **h) == ""


def test_fetch_site_does_not_gate_the_ui_page(qm):
    # The UI itself can be opened from a bookmark page or a link anywhere.
    assert _why(qm, "/", Host="localhost:8766",
                Sec_Fetch_Site="cross-site", Sec_Fetch_Mode="navigate") == ""


# --- tailnet lookup ---------------------------------------------------------

def test_tailnet_lookup_retries_after_failure(qm, monkeypatch):
    answers = [frozenset(), frozenset({TAILNET})]
    monkeypatch.setattr(qm, "_tailnet_names", lambda: answers.pop(0) if answers else frozenset({TAILNET}))
    monkeypatch.setattr(qm, "_TAILNET_RETRY_SECS", 0)
    assert _why(qm, Host="localhost:8766") == ""       # first lookup: Tailscale down
    assert _why(qm, Host=TAILNET + ":8443") == ""      # unknown host triggers a retry


def test_tailnet_blip_keeps_last_good_answer(qm, monkeypatch):
    assert _why(qm, Host=TAILNET) == ""
    monkeypatch.setattr(qm, "_tailnet_names", lambda: frozenset())
    monkeypatch.setattr(qm, "_TAILNET_RETRY_SECS", 0)
    assert _why(qm, Host="evil.example") != ""         # forces a refresh that fails
    assert _why(qm, Host=TAILNET) == ""


# --- credential denylist ----------------------------------------------------

def _home(qm, monkeypatch, tmp_path):
    home = os.path.realpath(str(tmp_path))
    monkeypatch.setattr(qm, "_open_roots", lambda: [home])
    monkeypatch.setattr(qm, "_deny_roots",
                        lambda: [os.path.join(home, d) for d in qm._DENY_HOME_DIRS])
    return tmp_path


@pytest.mark.parametrize("rel", [
    ".ssh/id_ed25519", ".ssh", ".aws/credentials", ".config/gh/hosts.yml",
    "Library/Keychains/login.keychain-db", ".claude/.credentials.json",
    ".claude.json", ".netrc", "proj/.npmrc",
])
def test_credential_paths_blocked(qm, monkeypatch, tmp_path, rel):
    home = _home(qm, monkeypatch, tmp_path)
    target = home / rel
    if "." in os.path.basename(rel)[1:] or os.path.basename(rel).startswith("id_") \
            or os.path.basename(rel) in (".netrc", ".npmrc", "credentials"):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("secret")
    else:
        target.mkdir(parents=True, exist_ok=True)
    ok, _, err = qm.resolve_open_path(str(target))
    assert not ok and err == "blocked: credential location"
    status, _, body = qm.build_file_response(str(target))
    assert status == 404 and b"secret" not in body


def test_symlink_into_credential_dir_blocked(qm, monkeypatch, tmp_path):
    home = _home(qm, monkeypatch, tmp_path)
    (home / ".ssh").mkdir()
    (home / ".ssh" / "id_ed25519").write_text("secret")
    (home / "innocent.txt").symlink_to(home / ".ssh" / "id_ed25519")
    assert qm.resolve_open_path(str(home / "innocent.txt"))[2] == "blocked: credential location"


def test_ordinary_files_still_served(qm, monkeypatch, tmp_path):
    home = _home(qm, monkeypatch, tmp_path)
    (home / ".claude" / "projects").mkdir(parents=True)
    f = home / ".claude" / "projects" / "MEMORY.md"
    f.write_text("notes")
    g = home / ".sshnotes.txt"
    g.write_text("fine")
    assert qm.resolve_open_path(str(f))[0]
    assert qm.resolve_open_path(str(g))[0]


@pytest.mark.parametrize("alias,real_rel", [
    (".SSH/id_ed25519", ".ssh/id_ed25519"),          # case-insensitive volume
    (".Ssh/ID_ED25519", ".ssh/id_ed25519"),
    ("Library/keychains/login.keychain-db", "Library/Keychains/login.keychain-db"),
    (".CLAUDE.JSON", ".claude.json"),
    (".claude/.Credentials.JSON", ".claude/.credentials.json"),
])
def test_case_variants_do_not_bypass(qm, monkeypatch, tmp_path, alias, real_rel):
    home = _home(qm, monkeypatch, tmp_path)
    target = home / real_rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("secret")
    ok, _, err = qm.resolve_open_path(str(home / alias))
    # On a case-sensitive volume the alias simply does not exist; either way
    # the secret must not come back.
    assert not ok and err in ("blocked: credential location", "not found")
    status, _, body = qm.build_file_response(str(home / alias))
    assert status == 404 and b"secret" not in body
    if os.path.exists(str(home / alias)):
        assert err == "blocked: credential location"


def test_identity_check_catches_respelled_directory(qm, monkeypatch, tmp_path):
    # Even if string comparison is defeated, the directory itself is recognised.
    home = _home(qm, monkeypatch, tmp_path)
    (home / ".ssh").mkdir()
    (home / ".ssh" / "key").write_text("secret")
    real = os.path.join(os.path.realpath(str(home)), ".ssh", "key")
    assert qm.is_credential_path(real)
    ids_only = os.path.join(os.path.realpath(str(home)), ".ssh")
    monkeypatch.setattr(qm, "_deny_roots", lambda: [os.path.join(str(home), ".", ".ssh")])
    assert qm.is_credential_path(os.path.join(ids_only, "key"))
