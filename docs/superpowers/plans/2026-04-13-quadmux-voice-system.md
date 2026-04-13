# QuadMux Voice System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace QuadMux's flaky voice stack with a bulletproof system: ElevenLabs Scribe STT, 3-tier voice picker (ElevenLabs / Kokoro / browser SpeechSynthesis), session-transcript-tailing for TTS of Claude responses, click-to-converse with 1.2s silence turn detection, visible failure handling.

**Architecture:** Browser layer (mic capture, VAD worklet, state machine, TTS playback). Server layer (proxy endpoints to ElevenLabs/Kokoro/Whisper, transcript watcher). External services accessed only from the server so the API key never reaches the browser.

**Tech Stack:** Python 3.11+, `websockets` library (existing), `httpx` (new, async HTTP client for API proxying), `pytest` + `pytest-asyncio` (new, tests). Browser-side: vanilla JS, Web Audio API, MediaRecorder, `<audio>` element.

**Spec:** `docs/superpowers/specs/2026-04-13-quadmux-voice-system-design.md`

**Style:** Sean's global rule - no em-dashes anywhere, ASCII hyphens only. Plan text and all generated code/docs must comply. All DOM updates use safe methods (textContent, element creation, replaceChildren); no innerHTML.

---

## Task 1: Spec cleanup and dev dependency install

**Files:**
- Modify: `docs/superpowers/specs/2026-04-13-quadmux-voice-system-design.md`
- Create: `requirements-dev.txt`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Replace every em-dash in the spec with an ASCII hyphen**

Run (from repo root):

```bash
python3 -c "import pathlib; p=pathlib.Path('docs/superpowers/specs/2026-04-13-quadmux-voice-system-design.md'); p.write_text(p.read_text().replace(chr(8212), '-').replace(chr(8211), '-'))"
```

Then spot-check:

```bash
grep -n $'\u2014\|\u2013' docs/superpowers/specs/2026-04-13-quadmux-voice-system-design.md || echo OK
```

Expected: `OK`.

- [ ] **Step 2: Create `requirements-dev.txt`**

```
pytest>=8.0
pytest-asyncio>=0.23
httpx>=0.27
respx>=0.21
websockets>=12.0
```

- [ ] **Step 3: Install dev deps in the project venv**

```bash
cd "/Users/seanreel2022/Desktop/Golf - AI & Automation (AI)/quadmux"
.venv/bin/pip install -r requirements-dev.txt
```

Expected: "Successfully installed" line.

- [ ] **Step 4: Create `tests/__init__.py`**

```python
# empty; marks tests as a package
```

- [ ] **Step 5: Create `tests/conftest.py`**

```python
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
```

- [ ] **Step 6: Run pytest on empty suite to confirm toolchain works**

```bash
.venv/bin/pytest -q
```

Expected: exit code 5 with "no tests ran". That is acceptable here.

- [ ] **Step 7: Commit**

```bash
git add requirements-dev.txt tests/__init__.py tests/conftest.py docs/superpowers/specs/2026-04-13-quadmux-voice-system-design.md
git -c commit.gpgsign=false commit -m "chore: add pytest scaffold and strip em-dashes from voice spec"
```

---

## Task 2: HTTP route dispatch

The current `http_handler` always returns the HTML. Add a path-based dispatch table so `/api/*` and `/static/*` paths can be added without touching the HTML branch.

**Files:**
- Create: `voice_routes.py`
- Create: `tests/test_voice_routes.py`
- Modify: `quadmux-server.py` (function `http_handler`, lines 351-361)

- [ ] **Step 1: Write the failing test**

Create `tests/test_voice_routes.py`:

```python
import pytest
from voice_routes import dispatch


class FakeRequest:
    def __init__(self, path, method="GET", body=b"", headers=None):
        self.path = path
        self.method = method
        self.body = body
        self.headers = headers or {}


@pytest.mark.asyncio
async def test_dispatch_returns_none_for_unmatched_path():
    result = await dispatch(FakeRequest("/unknown"))
    assert result is None


@pytest.mark.asyncio
async def test_dispatch_routes_api_health_voice():
    result = await dispatch(FakeRequest("/api/health/voice"))
    assert result is not None
    status, _headers, body = result
    assert status == 200
    assert b"elevenlabs" in body
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_voice_routes.py -v
```

Expected: `ModuleNotFoundError: No module named 'voice_routes'`.

- [ ] **Step 3: Create `voice_routes.py` with minimal dispatcher**

```python
"""HTTP route dispatcher for voice-related endpoints.

Returns a (status, headers, body_bytes) triple on match, or None if
the path is not handled here (caller falls through to HTML).
"""
from __future__ import annotations

import json
from typing import Iterable, Optional, Tuple


Headers = Iterable[Tuple[str, str]]
RouteResult = Optional[Tuple[int, Headers, bytes]]


async def dispatch(request) -> RouteResult:
    path = getattr(request, "path", "")
    if path == "/api/health/voice":
        return await _health()
    return None


async def _health() -> RouteResult:
    body = json.dumps({
        "elevenlabs": "unconfigured",
        "local_whisper": "unconfigured",
        "kokoro": "unconfigured",
    }).encode()
    headers = [("Content-Type", "application/json"), ("Cache-Control", "no-store")]
    return 200, headers, body
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_voice_routes.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Wire dispatcher into `http_handler` in `quadmux-server.py`**

Replace lines 351-361 with:

```python
async def http_handler(connection, request):
    """Serve API routes or the HTML UI on HTTP requests (non-WebSocket)."""
    if request.headers.get("Upgrade") is not None:
        return  # let websockets handle the upgrade

    from websockets.http11 import Response
    from websockets.datastructures import Headers

    try:
        from voice_routes import dispatch
        route_result = await dispatch(request)
    except Exception as e:
        print(f"  voice_routes.dispatch error: {e}", flush=True)
        route_result = None

    if route_result is not None:
        status, header_pairs, body = route_result
        return Response(status, "OK" if status == 200 else "ERR",
                        Headers(list(header_pairs)), body)

    return Response(200, "OK", Headers([
        ("Content-Type", "text/html"),
        ("Cache-Control", "no-cache, no-store, must-revalidate"),
        ("Pragma", "no-cache"),
        ("Expires", "0"),
    ]), get_html_content().encode())
```

- [ ] **Step 6: Smoke test the live server**

```bash
pkill -f quadmux-server || true
.venv/bin/python quadmux-server.py --port 9876 &
sleep 1
curl -sS http://localhost:9876/api/health/voice
kill %1
```

Expected:

```
{"elevenlabs": "unconfigured", "local_whisper": "unconfigured", "kokoro": "unconfigured"}
```

- [ ] **Step 7: Commit**

```bash
git add voice_routes.py tests/test_voice_routes.py quadmux-server.py
git -c commit.gpgsign=false commit -m "feat(voice): add HTTP route dispatcher with /api/health/voice stub"
```

---

## Task 3: Voice provider config and health probes

Wire environment variables, add real health probes that reach out to ElevenLabs / Kokoro / Whisper and report their status.

**Files:**
- Create: `voice_providers.py`
- Create: `tests/test_voice_providers.py`
- Modify: `voice_routes.py` (update `_health`)

- [ ] **Step 1: Write the failing test**

Create `tests/test_voice_providers.py`:

```python
import pytest
import respx
import httpx

from voice_providers import probe_health, Config


@pytest.mark.asyncio
async def test_probe_health_all_unconfigured(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    cfg = Config.from_env()
    result = await probe_health(cfg)
    assert result["elevenlabs"] == "unconfigured"


@pytest.mark.asyncio
async def test_probe_health_elevenlabs_ok(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "fake-key")
    cfg = Config.from_env()
    with respx.mock:
        respx.get("https://api.elevenlabs.io/v1/user").mock(
            return_value=httpx.Response(200, json={"xi_api_key_valid": True})
        )
        result = await probe_health(cfg)
    assert result["elevenlabs"] == "ok"


@pytest.mark.asyncio
async def test_probe_health_elevenlabs_invalid_key(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "bad")
    cfg = Config.from_env()
    with respx.mock:
        respx.get("https://api.elevenlabs.io/v1/user").mock(
            return_value=httpx.Response(401, json={"detail": "Unauthorized"})
        )
        result = await probe_health(cfg)
    assert result["elevenlabs"] == "down"


@pytest.mark.asyncio
async def test_probe_health_kokoro_ok(monkeypatch):
    cfg = Config.from_env()
    with respx.mock:
        respx.get(f"{cfg.kokoro_url}/v1/models").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        result = await probe_health(cfg)
    assert result["kokoro"] == "ok"


@pytest.mark.asyncio
async def test_probe_health_kokoro_down(monkeypatch):
    cfg = Config.from_env()
    with respx.mock:
        respx.get(f"{cfg.kokoro_url}/v1/models").mock(
            side_effect=httpx.ConnectError("refused")
        )
        result = await probe_health(cfg)
    assert result["kokoro"] == "down"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_voice_providers.py -v
```

Expected: ModuleNotFoundError for `voice_providers`.

- [ ] **Step 3: Create `voice_providers.py`**

```python
"""Config and health probes for voice providers (ElevenLabs, Kokoro, local Whisper)."""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Dict, Literal

import httpx

Status = Literal["ok", "down", "unconfigured"]


@dataclass(frozen=True)
class Config:
    elevenlabs_api_key: str
    kokoro_url: str
    whisper_url: str

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            elevenlabs_api_key=os.environ.get("ELEVENLABS_API_KEY", ""),
            kokoro_url=os.environ.get("KOKORO_URL", "http://localhost:8880"),
            whisper_url=os.environ.get("WHISPER_URL", "http://localhost:2022"),
        )


async def _probe_elevenlabs(cfg: Config, client: httpx.AsyncClient) -> Status:
    if not cfg.elevenlabs_api_key:
        return "unconfigured"
    try:
        r = await client.get(
            "https://api.elevenlabs.io/v1/user",
            headers={"xi-api-key": cfg.elevenlabs_api_key},
            timeout=3.0,
        )
        return "ok" if r.status_code == 200 else "down"
    except (httpx.HTTPError, httpx.TimeoutException):
        return "down"


async def _probe_kokoro(cfg: Config, client: httpx.AsyncClient) -> Status:
    try:
        r = await client.get(f"{cfg.kokoro_url}/v1/models", timeout=2.0)
        return "ok" if r.status_code == 200 else "down"
    except (httpx.HTTPError, httpx.TimeoutException):
        return "down"


async def _probe_whisper(cfg: Config, client: httpx.AsyncClient) -> Status:
    try:
        r = await client.get(f"{cfg.whisper_url}/v1/models", timeout=2.0)
        return "ok" if r.status_code == 200 else "down"
    except (httpx.HTTPError, httpx.TimeoutException):
        return "down"


async def probe_health(cfg: Config) -> Dict[str, Status]:
    async with httpx.AsyncClient() as client:
        el, ko, wh = await asyncio.gather(
            _probe_elevenlabs(cfg, client),
            _probe_kokoro(cfg, client),
            _probe_whisper(cfg, client),
        )
    return {"elevenlabs": el, "kokoro": ko, "local_whisper": wh}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_voice_providers.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Update `voice_routes._health` to use probes**

Replace the `_health` function in `voice_routes.py` with:

```python
async def _health() -> RouteResult:
    from voice_providers import Config, probe_health
    result = await probe_health(Config.from_env())
    body = json.dumps(result).encode()
    headers = [("Content-Type", "application/json"), ("Cache-Control", "no-store")]
    return 200, headers, body
```

- [ ] **Step 6: Verify the existing route test still passes**

```bash
.venv/bin/pytest tests/test_voice_routes.py tests/test_voice_providers.py -v
```

Expected: all 7 passed.

- [ ] **Step 7: Commit**

```bash
git add voice_providers.py voice_routes.py tests/test_voice_providers.py
git -c commit.gpgsign=false commit -m "feat(voice): add provider health probes (ElevenLabs, Kokoro, Whisper)"
```

---

## Task 4: STT endpoint (ElevenLabs Scribe + Whisper fallback)

`POST /api/stt` accepts `multipart/form-data` with an `audio` file field; proxies to ElevenLabs; on failure falls back to local Whisper.

**Files:**
- Create: `voice_stt.py`
- Create: `tests/test_voice_stt.py`
- Modify: `voice_routes.py` (add `/api/stt` case)

- [ ] **Step 1: Write the failing test**

Create `tests/test_voice_stt.py`:

```python
import pytest
import respx
import httpx

from voice_providers import Config
from voice_stt import transcribe


AUDIO = b"\x1a\x45\xdf\xa3" + b"\x00" * 100  # fake webm header + payload


@pytest.mark.asyncio
async def test_transcribe_elevenlabs_ok(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    cfg = Config.from_env()
    with respx.mock:
        respx.post("https://api.elevenlabs.io/v1/speech-to-text").mock(
            return_value=httpx.Response(200, json={"text": "hello world"})
        )
        result = await transcribe(cfg, AUDIO, "audio/webm")
    assert result.text == "hello world"
    assert result.provider == "elevenlabs"
    assert result.fallback_reason is None


@pytest.mark.asyncio
async def test_transcribe_elevenlabs_401_falls_back_to_whisper(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "bad")
    cfg = Config.from_env()
    with respx.mock:
        respx.post("https://api.elevenlabs.io/v1/speech-to-text").mock(
            return_value=httpx.Response(401, json={"detail": "bad key"})
        )
        respx.post(f"{cfg.whisper_url}/v1/audio/transcriptions").mock(
            return_value=httpx.Response(200, json={"text": "fallback text"})
        )
        result = await transcribe(cfg, AUDIO, "audio/webm")
    assert result.text == "fallback text"
    assert result.provider == "local_whisper"
    assert "401" in (result.fallback_reason or "")


@pytest.mark.asyncio
async def test_transcribe_both_providers_down_raises():
    cfg = Config.from_env()  # no env key -> elevenlabs unconfigured
    with respx.mock:
        respx.post(f"{cfg.whisper_url}/v1/audio/transcriptions").mock(
            side_effect=httpx.ConnectError("refused")
        )
        with pytest.raises(RuntimeError) as exc:
            await transcribe(cfg, AUDIO, "audio/webm")
    assert "no stt provider" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_transcribe_strips_whitespace(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    cfg = Config.from_env()
    with respx.mock:
        respx.post("https://api.elevenlabs.io/v1/speech-to-text").mock(
            return_value=httpx.Response(200, json={"text": "  hi there  \n"})
        )
        result = await transcribe(cfg, AUDIO, "audio/webm")
    assert result.text == "hi there"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_voice_stt.py -v
```

Expected: ModuleNotFoundError for `voice_stt`.

- [ ] **Step 3: Create `voice_stt.py`**

```python
"""Speech-to-text: try ElevenLabs Scribe first, fall back to local Whisper."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

import httpx

from voice_providers import Config


Provider = Literal["elevenlabs", "local_whisper"]


@dataclass
class Transcript:
    text: str
    provider: Provider
    fallback_reason: Optional[str] = None


async def _elevenlabs(cfg: Config, audio: bytes, mime: str) -> Transcript:
    if not cfg.elevenlabs_api_key:
        raise RuntimeError("elevenlabs unconfigured")
    async with httpx.AsyncClient(timeout=15.0) as client:
        files = {"file": ("audio.webm", audio, mime)}
        data = {"model_id": "scribe_v1"}
        r = await client.post(
            "https://api.elevenlabs.io/v1/speech-to-text",
            headers={"xi-api-key": cfg.elevenlabs_api_key},
            files=files,
            data=data,
        )
        if r.status_code != 200:
            raise RuntimeError(f"elevenlabs http {r.status_code}")
        text = (r.json().get("text") or "").strip()
    return Transcript(text=text, provider="elevenlabs")


async def _whisper(cfg: Config, audio: bytes, mime: str) -> Transcript:
    async with httpx.AsyncClient(timeout=30.0) as client:
        files = {"file": ("audio.webm", audio, mime)}
        data = {"model": "whisper-1"}
        r = await client.post(
            f"{cfg.whisper_url}/v1/audio/transcriptions",
            files=files,
            data=data,
        )
        if r.status_code != 200:
            raise RuntimeError(f"whisper http {r.status_code}")
        text = (r.json().get("text") or "").strip()
    return Transcript(text=text, provider="local_whisper")


async def transcribe(cfg: Config, audio: bytes, mime: str) -> Transcript:
    reason: Optional[str] = None
    try:
        return await _elevenlabs(cfg, audio, mime)
    except Exception as e:
        reason = f"elevenlabs: {e}"
    try:
        t = await _whisper(cfg, audio, mime)
        t.fallback_reason = reason
        return t
    except Exception as e:
        raise RuntimeError(f"no STT provider available ({reason}; whisper: {e})")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_voice_stt.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Write the failing route test**

Append to `tests/test_voice_routes.py`:

```python
@pytest.mark.asyncio
async def test_stt_route_rejects_non_post():
    result = await dispatch(FakeRequest("/api/stt", method="GET"))
    assert result is not None
    status, _headers, _body = result
    assert status == 405
```

- [ ] **Step 6: Add `/api/stt` to the dispatcher**

In `voice_routes.py`, extend `dispatch`:

```python
async def dispatch(request) -> RouteResult:
    path = getattr(request, "path", "")
    method = getattr(request, "method", "GET")
    if path == "/api/health/voice":
        return await _health()
    if path == "/api/stt":
        if method != "POST":
            return 405, [("Content-Type", "text/plain")], b"method not allowed"
        return await _stt(request)
    return None


async def _stt(request) -> RouteResult:
    from voice_providers import Config
    from voice_stt import transcribe

    ctype = request.headers.get("Content-Type", "")
    body = getattr(request, "body", b"") or b""
    audio, mime = _extract_multipart_audio(body, ctype)
    if audio is None:
        return 400, [("Content-Type", "application/json")], json.dumps({
            "error": "missing audio field"
        }).encode()
    try:
        result = await transcribe(Config.from_env(), audio, mime or "audio/webm")
    except RuntimeError as e:
        return 503, [("Content-Type", "application/json")], json.dumps({
            "error": str(e),
        }).encode()
    return 200, [("Content-Type", "application/json")], json.dumps({
        "text": result.text,
        "provider": result.provider,
        "fallback_reason": result.fallback_reason,
    }).encode()


def _extract_multipart_audio(body: bytes, content_type: str):
    """Return (audio_bytes, audio_mime) or (None, None) if not found.

    Minimal multipart parser: extracts a single file field named `audio`.
    """
    import re as _re
    m = _re.search(r"boundary=([^;]+)", content_type)
    if not m:
        return None, None
    boundary = b"--" + m.group(1).strip().strip('"').encode()
    parts = body.split(boundary)
    for part in parts:
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        head_end = part.find(b"\r\n\r\n")
        if head_end < 0:
            continue
        headers_blob = part[:head_end].decode("latin-1", errors="replace")
        payload = part[head_end + 4:]
        if payload.endswith(b"\r\n"):
            payload = payload[:-2]
        if 'name="audio"' not in headers_blob:
            continue
        mime_m = _re.search(r"Content-Type:\s*(\S+)", headers_blob)
        mime = mime_m.group(1) if mime_m else "audio/webm"
        return payload, mime
    return None, None
```

- [ ] **Step 7: Run all tests to verify they pass**

```bash
.venv/bin/pytest -v
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add voice_stt.py voice_routes.py tests/test_voice_stt.py tests/test_voice_routes.py
git -c commit.gpgsign=false commit -m "feat(voice): /api/stt with ElevenLabs Scribe primary + Whisper fallback"
```

---

## Task 5: TTS endpoint (ElevenLabs + Kokoro fallback; browser noop)

`POST /api/tts` with JSON body `{text, voice_id, provider}` streams audio bytes back. Browser tier short-circuits (server returns 204 + header telling the client to use SpeechSynthesis locally).

**Files:**
- Create: `voice_tts.py`
- Create: `tests/test_voice_tts.py`
- Modify: `voice_routes.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_voice_tts.py`:

```python
import pytest
import respx
import httpx

from voice_providers import Config
from voice_tts import synthesize, TTSResult


@pytest.mark.asyncio
async def test_synthesize_elevenlabs(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    cfg = Config.from_env()
    with respx.mock:
        respx.post("https://api.elevenlabs.io/v1/text-to-speech/voice123").mock(
            return_value=httpx.Response(200, content=b"AUDIO_BYTES", headers={
                "Content-Type": "audio/mpeg"
            })
        )
        result = await synthesize(cfg, "hello", "voice123", "elevenlabs")
    assert result.audio == b"AUDIO_BYTES"
    assert result.mime == "audio/mpeg"


@pytest.mark.asyncio
async def test_synthesize_kokoro():
    cfg = Config.from_env()
    with respx.mock:
        respx.post(f"{cfg.kokoro_url}/v1/audio/speech").mock(
            return_value=httpx.Response(200, content=b"KOKORO", headers={
                "Content-Type": "audio/wav"
            })
        )
        result = await synthesize(cfg, "hello", "af_bella", "kokoro")
    assert result.audio == b"KOKORO"
    assert result.mime == "audio/wav"


@pytest.mark.asyncio
async def test_synthesize_browser_returns_empty():
    cfg = Config.from_env()
    result = await synthesize(cfg, "hello", "system", "browser")
    assert result.audio == b""
    assert result.mime == "application/x-use-browser-tts"


@pytest.mark.asyncio
async def test_synthesize_elevenlabs_401_raises(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "bad")
    cfg = Config.from_env()
    with respx.mock:
        respx.post("https://api.elevenlabs.io/v1/text-to-speech/v").mock(
            return_value=httpx.Response(401, content=b"")
        )
        with pytest.raises(RuntimeError):
            await synthesize(cfg, "hi", "v", "elevenlabs")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_voice_tts.py -v
```

Expected: ModuleNotFoundError for `voice_tts`.

- [ ] **Step 3: Create `voice_tts.py`**

```python
"""Text-to-speech: ElevenLabs or Kokoro, with 'browser' tier as a noop sentinel."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import httpx

from voice_providers import Config


Tier = Literal["elevenlabs", "kokoro", "browser"]


@dataclass
class TTSResult:
    audio: bytes
    mime: str


async def _elevenlabs(cfg: Config, text: str, voice_id: str) -> TTSResult:
    if not cfg.elevenlabs_api_key:
        raise RuntimeError("elevenlabs unconfigured")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    body = {
        "text": text,
        "model_id": "eleven_turbo_v2_5",
        "voice_settings": {"stability": 0.4, "similarity_boost": 0.75},
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            url,
            headers={
                "xi-api-key": cfg.elevenlabs_api_key,
                "Accept": "audio/mpeg",
            },
            json=body,
        )
        if r.status_code != 200:
            raise RuntimeError(f"elevenlabs http {r.status_code}")
    return TTSResult(audio=r.content, mime=r.headers.get("Content-Type", "audio/mpeg"))


async def _kokoro(cfg: Config, text: str, voice_id: str) -> TTSResult:
    body = {"model": "kokoro", "input": text, "voice": voice_id, "response_format": "wav"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(f"{cfg.kokoro_url}/v1/audio/speech", json=body)
        if r.status_code != 200:
            raise RuntimeError(f"kokoro http {r.status_code}")
    return TTSResult(audio=r.content, mime=r.headers.get("Content-Type", "audio/wav"))


async def synthesize(cfg: Config, text: str, voice_id: str, provider: Tier) -> TTSResult:
    if provider == "elevenlabs":
        return await _elevenlabs(cfg, text, voice_id)
    if provider == "kokoro":
        return await _kokoro(cfg, text, voice_id)
    if provider == "browser":
        return TTSResult(audio=b"", mime="application/x-use-browser-tts")
    raise ValueError(f"unknown TTS provider: {provider}")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_voice_tts.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Add `/api/tts` route**

In `voice_routes.py`, inside `dispatch`:

```python
    if path == "/api/tts":
        if method != "POST":
            return 405, [("Content-Type", "text/plain")], b"method not allowed"
        return await _tts(request)
```

Add `_tts`:

```python
async def _tts(request) -> RouteResult:
    from voice_providers import Config
    from voice_tts import synthesize
    body = getattr(request, "body", b"") or b""
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        return 400, [("Content-Type", "application/json")], b'{"error":"bad json"}'
    text = (payload.get("text") or "").strip()
    voice_id = payload.get("voice_id") or ""
    provider = payload.get("provider") or "elevenlabs"
    if not text:
        return 400, [("Content-Type", "application/json")], b'{"error":"empty text"}'
    try:
        result = await synthesize(Config.from_env(), text, voice_id, provider)
    except RuntimeError as e:
        return 503, [("Content-Type", "application/json")], json.dumps({
            "error": str(e), "provider": provider,
        }).encode()
    if result.mime == "application/x-use-browser-tts":
        return 204, [("X-Voice-Provider", "browser")], b""
    return 200, [("Content-Type", result.mime)], result.audio
```

- [ ] **Step 6: Run all tests**

```bash
.venv/bin/pytest -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add voice_tts.py voice_routes.py tests/test_voice_tts.py
git -c commit.gpgsign=false commit -m "feat(voice): /api/tts with ElevenLabs + Kokoro + browser noop"
```

---

## Task 6: /api/voices endpoint

Returns available voices, grouped by tier. ElevenLabs list cached for 1 hour.

**Files:**
- Modify: `voice_providers.py` (add voice list fetcher with cache)
- Modify: `voice_routes.py` (add `/api/voices` case)
- Create: `tests/test_voice_list.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_voice_list.py
import pytest
import respx
import httpx

from voice_providers import Config, list_voices


@pytest.mark.asyncio
async def test_list_voices_groups_by_tier(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    cfg = Config.from_env()
    with respx.mock:
        respx.get("https://api.elevenlabs.io/v1/voices").mock(
            return_value=httpx.Response(200, json={
                "voices": [
                    {"voice_id": "onwK4e9ZLuTAKqWW03F9", "name": "Daniel"},
                    {"voice_id": "21m00Tcm4TlvDq8ikWAM", "name": "Rachel"},
                ]
            })
        )
        result = await list_voices(cfg)
    assert "elevenlabs" in result
    assert any(v["name"].startswith("JARVIS") for v in result["elevenlabs"])
    assert any(v["voice_id"] == "af_bella" for v in result["kokoro"])
    assert result["browser"] is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_voice_list.py -v
```

Expected: `AttributeError: module 'voice_providers' has no attribute 'list_voices'`.

- [ ] **Step 3: Add `list_voices` to `voice_providers.py`**

Append to `voice_providers.py`:

```python
import time
from typing import List


_KOKORO_VOICES = [
    {"voice_id": "af_bella",   "name": "Bella (F, warm)"},
    {"voice_id": "af_nicole",  "name": "Nicole (F, clear)"},
    {"voice_id": "am_adam",    "name": "Adam (M, neutral)"},
    {"voice_id": "am_michael", "name": "Michael (M, deep)"},
]

_PREFERRED_ELEVENLABS = {
    "onwK4e9ZLuTAKqWW03F9": "JARVIS (Daniel, British)",
    "21m00Tcm4TlvDq8ikWAM": "Rachel (F, American)",
    "pNInz6obpgDQGcFmaJgB": "Adam (M, American)",
    "piTKgcLEGmPE4e6mEKli": "Nicole (F, soft)",
    "EXAVITQu4vr4xnSDxMaL": "Bella (F, English)",
    "ErXwobaYiN019PkySvjV": "Antoni (M, well-rounded)",
}

_voice_cache: dict = {"ts": 0.0, "data": None}
_VOICE_TTL = 3600.0


async def _fetch_elevenlabs_voices(cfg: Config) -> List[dict]:
    if not cfg.elevenlabs_api_key:
        return []
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            r = await client.get(
                "https://api.elevenlabs.io/v1/voices",
                headers={"xi-api-key": cfg.elevenlabs_api_key},
            )
            if r.status_code != 200:
                return []
            voices = r.json().get("voices", [])
        except (httpx.HTTPError, httpx.TimeoutException):
            return []
    result = []
    seen = set()
    for vid, display_name in _PREFERRED_ELEVENLABS.items():
        for v in voices:
            if v.get("voice_id") == vid:
                result.append({"voice_id": vid, "name": display_name})
                seen.add(vid)
                break
    rest = [v for v in voices if v.get("voice_id") not in seen]
    rest.sort(key=lambda v: v.get("name") or "")
    for v in rest:
        result.append({"voice_id": v["voice_id"], "name": v.get("name") or v["voice_id"]})
    return result


async def list_voices(cfg: Config) -> dict:
    now = time.time()
    if _voice_cache["data"] and now - _voice_cache["ts"] < _VOICE_TTL:
        return _voice_cache["data"]
    el = await _fetch_elevenlabs_voices(cfg)
    data = {
        "elevenlabs": el,
        "kokoro": list(_KOKORO_VOICES),
        "browser": None,
    }
    _voice_cache["ts"] = now
    _voice_cache["data"] = data
    return data
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_voice_list.py -v
```

Expected: 1 passed.

- [ ] **Step 5: Add `/api/voices` route**

In `voice_routes.py`, inside `dispatch`:

```python
    if path == "/api/voices":
        return await _voices()
```

```python
async def _voices() -> RouteResult:
    from voice_providers import Config, list_voices
    data = await list_voices(Config.from_env())
    body = json.dumps(data).encode()
    return 200, [("Content-Type", "application/json"), ("Cache-Control", "max-age=600")], body
```

- [ ] **Step 6: Run all tests**

```bash
.venv/bin/pytest -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add voice_providers.py voice_routes.py tests/test_voice_list.py
git -c commit.gpgsign=false commit -m "feat(voice): /api/voices with 3-tier grouping and 1hr cache"
```

---

## Task 7: Session transcript watcher

Watch the active Claude Code session's jsonl file and emit `voice_response` messages whenever a new assistant text chunk appears. Strip markdown before emitting.

**Files:**
- Create: `voice_transcript.py`
- Create: `tests/test_voice_transcript.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_voice_transcript.py
import asyncio
import json
import pytest
from pathlib import Path

from voice_transcript import strip_markdown, TranscriptWatcher


def test_strip_markdown_removes_code_fences():
    s = "Here is code:\n```python\nprint('hi')\n```\nDone."
    out = strip_markdown(s)
    assert "```" not in out
    assert "print" not in out
    assert "Done." in out


def test_strip_markdown_removes_inline_backticks():
    assert strip_markdown("Use `ls` to list") == "Use ls to list"


def test_strip_markdown_removes_headings_and_bullets():
    s = "# Title\n\n- one\n- two\n\n## Sub"
    out = strip_markdown(s)
    assert "#" not in out
    assert "Title" in out
    assert "one" in out


@pytest.mark.asyncio
async def test_watcher_emits_only_new_assistant_text(tmp_path):
    session_file = tmp_path / "s.jsonl"
    session_file.write_text(
        json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n"
        + json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "hello back"}
        ]}}) + "\n"
    )

    emitted = []

    async def on_text(text):
        emitted.append(text)

    watcher = TranscriptWatcher(session_file, on_text)
    task = asyncio.create_task(watcher.run())
    await asyncio.sleep(0.1)

    with session_file.open("a") as f:
        f.write(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "second reply"}
        ]}}) + "\n")
    await asyncio.sleep(0.5)

    watcher.stop()
    await task

    assert emitted == ["second reply"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_voice_transcript.py -v
```

Expected: ModuleNotFoundError.

- [ ] **Step 3: Create `voice_transcript.py`**

```python
"""Session transcript watcher for Claude Code jsonl files."""
from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Awaitable, Callable, Optional


def strip_markdown(text: str) -> str:
    """Remove markdown formatting that sounds bad when read aloud."""
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"^\s*#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class TranscriptWatcher:
    """Tail a Claude Code session.jsonl and emit assistant text chunks."""

    def __init__(self, path: Path, on_text: Callable[[str], Awaitable[None]]):
        self.path = Path(path)
        self.on_text = on_text
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    async def run(self) -> None:
        for _ in range(30):
            if self.path.exists():
                break
            if self._stop:
                return
            await asyncio.sleep(0.1)
        if not self.path.exists():
            return

        with self.path.open("r") as f:
            f.seek(0, os.SEEK_END)  # only new data
            while not self._stop:
                line = f.readline()
                if not line:
                    await asyncio.sleep(0.2)
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                text = _extract_assistant_text(obj)
                if text:
                    cleaned = strip_markdown(text)
                    if cleaned:
                        await self.on_text(cleaned)


def _extract_assistant_text(obj: dict) -> Optional[str]:
    if obj.get("type") != "assistant":
        return None
    msg = obj.get("message") or {}
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                t = c.get("text")
                if t:
                    parts.append(t)
        return "\n".join(parts) if parts else None
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_voice_transcript.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add voice_transcript.py tests/test_voice_transcript.py
git -c commit.gpgsign=false commit -m "feat(voice): session transcript watcher with markdown stripping"
```

---

## Task 8: Wire transcript watcher into quadmux-server WebSocket

The browser sends `voice_start` with `{shell, session_path}` and `voice_stop`. The server starts/stops a watcher; watcher text is pushed as `voice_response`.

**Files:**
- Modify: `quadmux-server.py` (lines 53-116 and 413-420)

- [ ] **Step 1: Remove the old voice extraction block**

Delete lines 53-116 of `quadmux-server.py` (the `ANSI_RE` regex for voice, `voice_shell`/`voice_capturing`/`voice_buffers`/`voice_timers` globals, `extract_prose`, and `voice_output_settled`).

Replace with:

```python
# --- Voice ---
from voice_transcript import TranscriptWatcher
from pathlib import Path

voice_watcher: "TranscriptWatcher | None" = None
voice_watcher_task: "asyncio.Task | None" = None
voice_shell: int = -1


async def _broadcast_voice_text(shell_idx: int, text: str) -> None:
    msg = json.dumps({"type": "voice_response", "shell": shell_idx, "text": text})
    for ws in clients.copy():
        try:
            await ws.send(msg)
        except Exception:
            pass


async def start_voice_watcher(shell_idx: int, session_path: str) -> None:
    global voice_watcher, voice_watcher_task, voice_shell
    await stop_voice_watcher()
    path = Path(session_path).expanduser()
    if not path.is_absolute() or not path.exists():
        print(f"  voice: session file not found: {session_path}", flush=True)
        msg = json.dumps({"type": "voice_error",
                          "reason": f"session file not found: {session_path}"})
        for ws in clients.copy():
            try:
                await ws.send(msg)
            except Exception:
                pass
        return

    async def on_text(t: str):
        await _broadcast_voice_text(shell_idx, t)

    voice_shell = shell_idx
    voice_watcher = TranscriptWatcher(path, on_text)
    voice_watcher_task = asyncio.create_task(voice_watcher.run())
    print(f"  voice: watching {path} for shell {shell_idx}", flush=True)


async def stop_voice_watcher() -> None:
    global voice_watcher, voice_watcher_task, voice_shell
    if voice_watcher:
        voice_watcher.stop()
    if voice_watcher_task:
        try:
            await asyncio.wait_for(voice_watcher_task, timeout=1.0)
        except asyncio.TimeoutError:
            voice_watcher_task.cancel()
    voice_watcher = None
    voice_watcher_task = None
    voice_shell = -1
```

- [ ] **Step 2: Remove the old output-capture hook**

Find the block around lines 315-331 beginning with `# Voice: accumulate output if capturing`. Delete it entirely. The watcher now owns response extraction.

- [ ] **Step 3: Replace `voice_start` / `voice_stop` handlers**

Around lines 413-420 in `quadmux-server.py`, find:

```python
elif msg_type == "voice_start":
    voice_shell = data.get("shell", -1)
    print(f"  Voice active on pane {voice_shell + 1}", flush=True)
elif msg_type == "voice_stop":
    voice_shell = -1
    voice_capturing.clear()
    voice_buffers.clear()
    print(f"  Voice stopped", flush=True)
```

Replace with:

```python
elif msg_type == "voice_start":
    shell = int(data.get("shell", -1))
    session_path = str(data.get("session_path", ""))
    if shell < 0 or not session_path:
        await ws.send(json.dumps({
            "type": "voice_error", "reason": "missing shell or session_path"
        }))
    else:
        await start_voice_watcher(shell, session_path)
elif msg_type == "voice_stop":
    await stop_voice_watcher()
```

- [ ] **Step 4: Remove the now-unused `global voice_shell` declaration**

Inside `handler` (~line 365), remove `global voice_shell`.

- [ ] **Step 5: Smoke test**

```bash
pkill -f quadmux-server || true
.venv/bin/python quadmux-server.py --port 9876 &
sleep 1
curl -sS http://localhost:9876/api/health/voice
kill %1
```

Expected: JSON output; no tracebacks in server logs.

- [ ] **Step 6: Run full test suite**

```bash
.venv/bin/pytest -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add quadmux-server.py
git -c commit.gpgsign=false commit -m "feat(voice): replace terminal-scraping with transcript watcher"
```

---

## Task 9: Static file serving for `/static/voice.js`

The browser module lives in `static/voice.js` rather than inline. Add a `/static/*` route.

**Files:**
- Modify: `voice_routes.py` (add `/static/*` case)
- Create: `static/.gitkeep`
- Create: `tests/test_static_routes.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_static_routes.py
import pytest
from voice_routes import dispatch


class FakeRequest:
    def __init__(self, path, method="GET"):
        self.path = path
        self.method = method
        self.headers = {}
        self.body = b""


@pytest.mark.asyncio
async def test_static_serves_existing_file(tmp_path, monkeypatch):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "voice.js").write_bytes(b"console.log('hi');")
    monkeypatch.setenv("QUADMUX_STATIC_DIR", str(static_dir))
    result = await dispatch(FakeRequest("/static/voice.js"))
    assert result is not None
    status, headers, body = result
    assert status == 200
    assert body == b"console.log('hi');"
    assert any(h[0] == "Content-Type" and "javascript" in h[1] for h in headers)


@pytest.mark.asyncio
async def test_static_404_for_missing():
    result = await dispatch(FakeRequest("/static/nonexistent.js"))
    assert result is not None
    status, _headers, _body = result
    assert status == 404


@pytest.mark.asyncio
async def test_static_rejects_path_traversal():
    result = await dispatch(FakeRequest("/static/../secret.txt"))
    assert result is not None
    status, _headers, _body = result
    assert status == 400
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_static_routes.py -v
```

Expected: 3 tests fail.

- [ ] **Step 3: Add `/static/*` case to `dispatch`**

In `voice_routes.py`, inside `dispatch`:

```python
    if path.startswith("/static/"):
        return _static(path)
```

Add `_static`:

```python
import os as _os
from pathlib import Path as _Path

_DEFAULT_STATIC_DIR = _Path(__file__).resolve().parent / "static"


def _static(path: str) -> RouteResult:
    rel = path[len("/static/"):]
    if ".." in rel or rel.startswith("/"):
        return 400, [("Content-Type", "text/plain")], b"bad path"
    base = _Path(_os.environ.get("QUADMUX_STATIC_DIR", str(_DEFAULT_STATIC_DIR))).resolve()
    target = (base / rel).resolve()
    if not str(target).startswith(str(base)):
        return 400, [("Content-Type", "text/plain")], b"bad path"
    if not target.is_file():
        return 404, [("Content-Type", "text/plain")], b"not found"
    mime = "application/javascript" if target.suffix == ".js" else \
           "text/css" if target.suffix == ".css" else \
           "application/octet-stream"
    return 200, [("Content-Type", mime), ("Cache-Control", "no-cache")], target.read_bytes()
```

- [ ] **Step 4: Create the static dir placeholder**

```bash
mkdir -p static
touch static/.gitkeep
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_static_routes.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add static/.gitkeep voice_routes.py tests/test_static_routes.py
git -c commit.gpgsign=false commit -m "feat(voice): serve /static/* from disk for browser voice module"
```

---

## Task 10: Browser voice module - state machine and VAD

Creates the file and the audio worklet. Server calls added in later tasks.

**Files:**
- Create: `static/voice.js`
- Create: `static/voice-vad.worklet.js`

- [ ] **Step 1: Create `static/voice-vad.worklet.js`**

```javascript
// RMS-based voice activity detector. Posts "speech" | "silence"
// frame events back to the main thread every ~50ms.

class VadProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buf = new Float32Array(0);
    this.speechThreshold = 0.012;
    this.frameSamples = Math.round(sampleRate * 0.05);
  }

  static get parameterDescriptors() { return []; }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    const merged = new Float32Array(this.buf.length + ch.length);
    merged.set(this.buf, 0);
    merged.set(ch, this.buf.length);
    this.buf = merged;

    while (this.buf.length >= this.frameSamples) {
      const frame = this.buf.slice(0, this.frameSamples);
      this.buf = this.buf.slice(this.frameSamples);
      let sum = 0;
      for (let i = 0; i < frame.length; i++) sum += frame[i] * frame[i];
      const rms = Math.sqrt(sum / frame.length);
      this.port.postMessage({
        type: rms > this.speechThreshold ? "speech" : "silence",
        rms: rms,
      });
    }
    return true;
  }
}

registerProcessor("vad-processor", VadProcessor);
```

- [ ] **Step 2: Create `static/voice.js`**

```javascript
// QuadMux voice module. Loaded as a plain script; exposes window.QMVoice.
(function () {
  "use strict";

  const STATES = {
    IDLE: "idle",
    LISTENING: "listening",
    CAPTURING: "capturing",
    TRANSCRIBING: "transcribing",
    SPEAKING: "speaking",
  };

  const state = {
    current: STATES.IDLE,
    stream: null,
    ctx: null,
    worklet: null,
    recorder: null,
    recorderChunks: [],
    silenceFramesNeeded: 24,
    silenceCount: 0,
    onStateChange: () => {},
    config: {
      voice_id: "onwK4e9ZLuTAKqWW03F9",
      provider: "elevenlabs",
      micDeviceId: null,
    },
    targetShell: -1,
    sessionPath: null,
    ws: null,
  };

  function setState(next) {
    state.current = next;
    state.onStateChange(next);
  }

  async function start({ shell, sessionPath, ws, onStateChange }) {
    if (state.current !== STATES.IDLE) await stop();
    state.targetShell = shell;
    state.sessionPath = sessionPath;
    state.ws = ws;
    state.onStateChange = onStateChange || (() => {});

    const audio = {
      echoCancellation: true, noiseSuppression: true, autoGainControl: true,
    };
    if (state.config.micDeviceId) audio.deviceId = { exact: state.config.micDeviceId };
    state.stream = await navigator.mediaDevices.getUserMedia({ audio });
    state.ctx = new AudioContext({ sampleRate: 16000 });
    await state.ctx.audioWorklet.addModule("/static/voice-vad.worklet.js");

    const src = state.ctx.createMediaStreamSource(state.stream);
    state.worklet = new AudioWorkletNode(state.ctx, "vad-processor");
    state.worklet.port.onmessage = (ev) => onVadFrame(ev.data);
    src.connect(state.worklet);

    ws.send(JSON.stringify({
      type: "voice_start",
      shell: shell,
      session_path: sessionPath,
    }));

    setState(STATES.LISTENING);
  }

  async function stop() {
    if (state.current === STATES.IDLE) return;
    if (state.recorder && state.recorder.state !== "inactive") {
      try { state.recorder.stop(); } catch (e) {}
    }
    state.recorder = null;
    if (state.worklet) {
      try { state.worklet.disconnect(); } catch (e) {}
      state.worklet = null;
    }
    if (state.ctx) {
      try { await state.ctx.close(); } catch (e) {}
      state.ctx = null;
    }
    if (state.stream) {
      state.stream.getTracks().forEach((t) => t.stop());
      state.stream = null;
    }
    if (state.ws && state.ws.readyState === 1) {
      state.ws.send(JSON.stringify({ type: "voice_stop" }));
    }
    state.silenceCount = 0;
    state.recorderChunks = [];
    setState(STATES.IDLE);
  }

  function onVadFrame(frame) {
    if (state.current === STATES.LISTENING && frame.type === "speech") {
      beginCapture();
      return;
    }
    if (state.current === STATES.CAPTURING) {
      if (frame.type === "silence") {
        state.silenceCount += 1;
        if (state.silenceCount >= state.silenceFramesNeeded) endCapture();
      } else {
        state.silenceCount = 0;
      }
    }
  }

  function beginCapture() {
    state.silenceCount = 0;
    state.recorderChunks = [];
    const mime = pickRecorderMime();
    state.recorder = new MediaRecorder(state.stream, { mimeType: mime });
    state.recorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) state.recorderChunks.push(e.data);
    };
    state.recorder.onstop = onRecorderStop;
    state.recorder.start();
    setState(STATES.CAPTURING);
  }

  function endCapture() {
    if (state.recorder && state.recorder.state !== "inactive") {
      state.recorder.stop();
    }
    setState(STATES.TRANSCRIBING);
  }

  function pickRecorderMime() {
    const candidates = [
      "audio/webm;codecs=opus",
      "audio/webm",
      "audio/ogg;codecs=opus",
    ];
    for (const c of candidates) {
      if (MediaRecorder.isTypeSupported(c)) return c;
    }
    return "";
  }

  async function onRecorderStop() {
    const blob = new Blob(state.recorderChunks, {
      type: state.recorder?.mimeType || "audio/webm",
    });
    state.recorderChunks = [];
    try {
      const text = await postStt(blob);
      if (text && text.trim()) {
        if (window.QMVoice.onTranscript) window.QMVoice.onTranscript(text);
      }
    } catch (e) {
      console.error("STT failed", e);
      if (window.QMVoice.onError) window.QMVoice.onError(`stt: ${e.message || e}`);
    }
    if (state.current !== STATES.IDLE) setState(STATES.LISTENING);
  }

  async function postStt(blob) {
    const form = new FormData();
    form.append("audio", blob, "utterance.webm");
    const r = await fetch("/api/stt", { method: "POST", body: form });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    return data.text;
  }

  window.QMVoice = {
    STATES,
    start, stop,
    getState: () => state.current,
    setConfig: (cfg) => Object.assign(state.config, cfg),
    getConfig: () => ({ ...state.config }),
    onTranscript: null,
    onError: null,
  };
})();
```

- [ ] **Step 3: Smoke-check that the server serves them**

```bash
pkill -f quadmux-server || true
.venv/bin/python quadmux-server.py --port 9876 &
sleep 1
curl -sSI http://localhost:9876/static/voice.js | head -5
curl -sSI http://localhost:9876/static/voice-vad.worklet.js | head -5
kill %1
```

Expected: `HTTP/1.1 200 OK` and `Content-Type: application/javascript` for both.

- [ ] **Step 4: Commit**

```bash
git add static/voice.js static/voice-vad.worklet.js
git -c commit.gpgsign=false commit -m "feat(voice): browser voice state machine + VAD worklet"
```

---

## Task 11: Inject transcripts into the focused terminal pane

Hook `window.QMVoice.onTranscript` into the existing terminal code so transcribed text is typed into the pane followed by Enter. Wire `voice_response` WebSocket messages to `/api/tts`.

**Files:**
- Modify: `quadmux.html`
- Modify: `quadmux-server.py` (announce session path from shell output)

- [ ] **Step 1: Add the script tag**

In `quadmux.html`, immediately before `</body>`:

```html
<script src="/static/voice.js"></script>
```

- [ ] **Step 2: Replace the inline voice block**

Find the block starting with `// --- Voice mode (Local Whisper STT + Kokoro TTS) ---` (near line 1210) and ending at the end of the voice functions (approximately line 1400). Delete the whole block.

Replace with:

```javascript
// --- Voice (v2: delegated to /static/voice.js) ---

let voiceTargetPane = -1;
const shellSessionPaths = [null, null, null, null];

function currentSessionPathForShell(shellIdx) {
  return shellSessionPaths[shellIdx] || null;
}

window.QMVoice.onTranscript = (text) => {
  if (voiceTargetPane < 0) return;
  ws.send(JSON.stringify({ type: "input", shell: voiceTargetPane, text: text + "\r" }));
};

window.QMVoice.onError = (msg) => {
  showBanner(`Voice error: ${msg}`, "error");
};

async function toggleVoice(paneIdx) {
  if (window.QMVoice.getState() !== window.QMVoice.STATES.IDLE) {
    await window.QMVoice.stop();
    voiceTargetPane = -1;
    document.querySelectorAll(".btn-voice").forEach((b) => {
      b.classList.remove("voice-active", "voice-capturing");
    });
    return;
  }
  const sessionPath = currentSessionPathForShell(paneIdx);
  if (!sessionPath) {
    showBanner("No Claude Code session detected in this pane. Start `claude` first.", "warn");
    return;
  }
  voiceTargetPane = paneIdx;
  try {
    await window.QMVoice.start({
      shell: paneIdx,
      sessionPath: sessionPath,
      ws: ws,
      onStateChange: (s) => {
        const btn = document.querySelector(`.btn-voice[data-shell="${paneIdx}"]`);
        if (!btn) return;
        btn.classList.toggle("voice-active", s !== "idle");
        btn.classList.toggle("voice-capturing", s === "capturing");
      },
    });
  } catch (e) {
    showBanner(`Voice failed to start: ${e.message || e}`, "error");
    voiceTargetPane = -1;
  }
}

function showBanner(msg, kind) {
  let bar = document.getElementById("voice-banner");
  if (!bar) {
    bar = document.createElement("div");
    bar.id = "voice-banner";
    bar.style.cssText = "position:fixed;top:8px;left:50%;transform:translateX(-50%);"
      + "padding:8px 14px;border-radius:8px;font:13px/1.4 system-ui;z-index:9999;"
      + "background:#222;color:#fff;box-shadow:0 4px 12px rgba(0,0,0,.3);max-width:90vw;";
    document.body.appendChild(bar);
  }
  bar.textContent = msg;
  bar.style.background = kind === "error" ? "#a33" : kind === "warn" ? "#b80" : "#333";
  bar.style.display = "block";
  clearTimeout(bar._t);
  bar._t = setTimeout(() => { bar.style.display = "none"; }, 6000);
}

async function playTts(text) {
  const cfg = window.QMVoice.getConfig();
  try {
    const r = await fetch("/api/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, voice_id: cfg.voice_id, provider: cfg.provider }),
    });
    if (r.status === 204 && r.headers.get("X-Voice-Provider") === "browser") {
      speakWithBrowser(text, cfg.voice_id);
      return;
    }
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      showBanner(`TTS failed: ${j.error || r.status}`, "warn");
      return;
    }
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = new Audio(url);
    a.addEventListener("ended", () => URL.revokeObjectURL(url));
    a.play();
  } catch (e) {
    showBanner(`TTS error: ${e.message || e}`, "error");
  }
}

function speakWithBrowser(text, voiceName) {
  const u = new SpeechSynthesisUtterance(text);
  if (voiceName) {
    const v = speechSynthesis.getVoices().find((v) => v.name === voiceName);
    if (v) u.voice = v;
  }
  speechSynthesis.speak(u);
}
```

- [ ] **Step 3: Rewire mic buttons**

Replace the existing `.btn-voice` click handler with:

```javascript
document.querySelectorAll(".btn-voice").forEach((btn) => {
  btn.addEventListener("click", () => {
    const paneIdx = parseInt(btn.dataset.shell, 10);
    toggleVoice(paneIdx);
  });
});
```

Attach this inside the existing DOM-ready initialization block alongside other button wiring.

- [ ] **Step 4: Handle `voice_response` and session path messages**

Find the existing WebSocket `onmessage` handler. Locate the `voice_response` branch. Replace its body with:

```javascript
} else if (data.type === "voice_response") {
  if (voiceTargetPane !== data.shell || !data.text) return;
  playTts(data.text);
} else if (data.type === "voice_error") {
  showBanner(data.reason || "voice error", "error");
} else if (data.type === "voice_session_path") {
  shellSessionPaths[data.shell] = data.path;
```

- [ ] **Step 5: Announce session path from server**

In `quadmux-server.py`, add near the other globals:

```python
SESSION_LINE_RE = re.compile(r"(/[^\s]+\.claude/projects/[^\s]+\.jsonl)")

def maybe_announce_session_path(idx, chunk):
    m = SESSION_LINE_RE.search(chunk)
    if not m:
        return
    path = m.group(1)
    msg = json.dumps({"type": "voice_session_path", "shell": idx, "path": path})
    for ws in clients.copy():
        try:
            asyncio.create_task(ws.send(msg))
        except Exception:
            pass
```

Call `maybe_announce_session_path(idx, text)` immediately after the existing shell-output decode in the per-shell reader loop.

Note: if the Claude Code version in use does not print the session path to the terminal, the user must set `CLAUDE_SESSION_FILE` manually or run a helper shell alias that echoes it. A v2 enhancement can infer the path from `~/.claude/projects/<encoded-cwd>/` timestamps.

- [ ] **Step 6: Commit**

```bash
git add quadmux.html quadmux-server.py
git -c commit.gpgsign=false commit -m "feat(voice): wire browser state machine to terminal + TTS playback"
```

---

## Task 12: Voice settings modal

Settings dialog with voice picker, mic picker, health indicator, test-voice button.

**Files:**
- Modify: `quadmux.html`

- [ ] **Step 1: Add modal markup**

In `quadmux.html`, add near the other modals:

```html
<div id="voice-settings-modal" class="modal" hidden>
  <div class="modal-content" style="max-width:480px;">
    <h3>Voice Settings</h3>

    <label>Voice</label>
    <select id="voice-picker" style="width:100%;margin-bottom:12px;"></select>

    <label>Microphone</label>
    <select id="mic-picker" style="width:100%;margin-bottom:12px;"></select>

    <div style="display:flex;gap:8px;align-items:center;margin-bottom:12px;">
      <span id="voice-health-dot" style="display:inline-block;width:10px;height:10px;border-radius:50%;background:#888;"></span>
      <span id="voice-health-text">Checking...</span>
    </div>

    <div style="display:flex;gap:8px;">
      <button id="voice-test-btn">Test voice</button>
      <button id="voice-settings-close">Close</button>
    </div>
  </div>
</div>
```

Add a gear icon to the header:

```html
<button id="voice-settings-btn" title="Voice settings" aria-label="Voice settings">&#x2699;</button>
```

- [ ] **Step 2: Wire it up**

In the DOM-ready init block:

```javascript
document.getElementById("voice-settings-btn").addEventListener("click", openVoiceSettings);
document.getElementById("voice-settings-close").addEventListener("click", closeVoiceSettings);
document.getElementById("voice-test-btn").addEventListener("click", testVoice);
document.getElementById("voice-picker").addEventListener("change", onVoicePicked);
document.getElementById("mic-picker").addEventListener("change", onMicPicked);

loadVoicePreferences();

async function openVoiceSettings() {
  document.getElementById("voice-settings-modal").hidden = false;
  await refreshVoiceHealth();
  await populateVoicePicker();
  await populateMicPicker();
}
function closeVoiceSettings() {
  document.getElementById("voice-settings-modal").hidden = true;
}

async function refreshVoiceHealth() {
  const r = await fetch("/api/health/voice");
  const j = await r.json();
  const dot = document.getElementById("voice-health-dot");
  const txt = document.getElementById("voice-health-text");
  const anyOk = Object.values(j).some((v) => v === "ok");
  const allNonDown = Object.values(j).every((v) => v === "ok" || v === "unconfigured");
  dot.style.background = anyOk ? (allNonDown ? "#2a2" : "#b80") : "#a33";
  txt.textContent = `ElevenLabs: ${j.elevenlabs}, Kokoro: ${j.kokoro}, Whisper: ${j.local_whisper}`;
}

function clearSelect(sel) {
  while (sel.firstChild) sel.removeChild(sel.firstChild);
}

async function populateVoicePicker() {
  const r = await fetch("/api/voices");
  const j = await r.json();
  const sel = document.getElementById("voice-picker");
  clearSelect(sel);
  const addGroup = (label, items, provider) => {
    if (!items || !items.length) return;
    const og = document.createElement("optgroup");
    og.label = label;
    for (const v of items) {
      const opt = document.createElement("option");
      opt.value = JSON.stringify({ voice_id: v.voice_id, provider });
      opt.textContent = v.name;
      og.appendChild(opt);
    }
    sel.appendChild(og);
  };
  addGroup("ElevenLabs (premium)", j.elevenlabs, "elevenlabs");
  addGroup("Kokoro (local)", j.kokoro, "kokoro");
  const browserVoices = speechSynthesis.getVoices().map((v) => ({
    voice_id: v.name, name: `${v.name} (browser)`,
  }));
  addGroup("Browser system voices", browserVoices, "browser");

  const cfg = window.QMVoice.getConfig();
  const want = JSON.stringify({ voice_id: cfg.voice_id, provider: cfg.provider });
  for (const opt of sel.options) {
    if (opt.value === want) { opt.selected = true; break; }
  }
}

async function populateMicPicker() {
  try {
    const s = await navigator.mediaDevices.getUserMedia({ audio: true });
    s.getTracks().forEach((t) => t.stop());
  } catch (e) {
    showBanner("Mic permission needed to list devices", "warn");
    return;
  }
  const devices = await navigator.mediaDevices.enumerateDevices();
  const mics = devices.filter((d) => d.kind === "audioinput");
  const sel = document.getElementById("mic-picker");
  clearSelect(sel);
  for (const m of mics) {
    const opt = document.createElement("option");
    opt.value = m.deviceId;
    opt.textContent = m.label || `Mic ${m.deviceId.slice(0, 6)}`;
    sel.appendChild(opt);
  }
  const cfg = window.QMVoice.getConfig();
  if (cfg.micDeviceId) sel.value = cfg.micDeviceId;
}

function onVoicePicked(ev) {
  const { voice_id, provider } = JSON.parse(ev.target.value);
  window.QMVoice.setConfig({ voice_id, provider });
  localStorage.setItem("quadmux-voice-pick", JSON.stringify({ voice_id, provider }));
}

function onMicPicked(ev) {
  window.QMVoice.setConfig({ micDeviceId: ev.target.value });
  localStorage.setItem("quadmux-mic-device", ev.target.value);
}

function loadVoicePreferences() {
  try {
    const v = JSON.parse(localStorage.getItem("quadmux-voice-pick") || "null");
    if (v && v.voice_id) window.QMVoice.setConfig(v);
  } catch (e) {}
  const m = localStorage.getItem("quadmux-mic-device");
  if (m) window.QMVoice.setConfig({ micDeviceId: m });
}

async function testVoice() {
  playTts("Voice systems nominal. This is a test of the selected voice.");
}
```

- [ ] **Step 3: Smoke test in browser**

```bash
pkill -f quadmux-server || true
.venv/bin/python quadmux-server.py --port 9876 &
sleep 1
open http://localhost:9876
```

Click the gear icon; voice picker populates (ElevenLabs entries require `ELEVENLABS_API_KEY`); Test Voice produces audio. Close browser and kill the server:

```bash
kill %1
```

- [ ] **Step 4: Commit**

```bash
git add quadmux.html
git -c commit.gpgsign=false commit -m "feat(voice): settings modal with voice/mic picker and health dot"
```

---

## Task 13: Remove the old voice UI

The old mic picker modal and custom `voice-hearing` / `voice-pulse` animations are superseded.

**Files:**
- Modify: `quadmux.html` (CSS at lines 197-250 and the mic-picker modal markup near line 625)

- [ ] **Step 1: Delete the old mic picker modal**

Remove the markup block starting with `<h3>Select Microphone</h3>` and its surrounding `<div class="modal">`.

- [ ] **Step 2: Delete the old voice animations**

Remove CSS blocks `.btn-voice`, `.btn-voice:hover`, `.btn-voice.voice-active`, `.btn-voice.voice-active.voice-hearing`, `.btn-voice:focus-visible`, `@keyframes voice-pulse`, `@keyframes voice-hearing`, `.voice-level-bar`, `.voice-level-fill`, `@keyframes voice-pulse-color` (lines roughly 197-250).

Replace with minimal mic-button styles:

```css
.btn-voice {
  background: transparent;
  border: none;
  color: var(--text);
  cursor: pointer;
  padding: 4px 8px;
  border-radius: 6px;
  font-size: 14px;
}
.btn-voice:hover { background: var(--surface-2); }
.btn-voice.voice-active { color: var(--accent); animation: qmv-pulse 1.5s ease-in-out infinite; }
.btn-voice.voice-capturing { color: #6f6; animation: qmv-fast 0.25s ease-in-out infinite alternate; }
@keyframes qmv-pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.55; } }
@keyframes qmv-fast  { from { opacity: 0.7; } to { opacity: 1; } }
```

- [ ] **Step 3: Remove the `voice-level-bar` spans from mic buttons**

In each of the 4 `<button class="btn-voice" ...>` elements, remove the nested `<span class="voice-level-bar"><span class="voice-level-fill" id="voice-level-N"></span></span>`.

- [ ] **Step 4: Manual smoke test**

Reload the app. Confirm:
- Mic buttons still visible and clickable.
- Clicking mic pulses the button; clicking again stops it.
- Gear icon still opens the settings modal.
- No console errors about undefined elements.

- [ ] **Step 5: Commit**

```bash
git add quadmux.html
git -c commit.gpgsign=false commit -m "refactor(voice): remove superseded mic modal + voice-level bars"
```

---

## Task 14: Integration test and feature flag

End-to-end test of STT + TTS + transcript watcher against mocked providers. Gate the v2 path behind `localStorage.quadmux-voice-v2`.

**Files:**
- Create: `tests/test_voice_integration.py`
- Modify: `quadmux.html` (feature flag around `toggleVoice`)

- [ ] **Step 1: Write the integration test**

```python
# tests/test_voice_integration.py
import pytest
import respx
import httpx

from voice_providers import Config
from voice_stt import transcribe
from voice_tts import synthesize
from voice_transcript import strip_markdown


AUDIO = b"\x00" * 128


@pytest.mark.asyncio
async def test_one_turn_roundtrip(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    cfg = Config.from_env()
    with respx.mock:
        respx.post("https://api.elevenlabs.io/v1/speech-to-text").mock(
            return_value=httpx.Response(200, json={"text": "what's two plus two"})
        )
        respx.post("https://api.elevenlabs.io/v1/text-to-speech/v1").mock(
            return_value=httpx.Response(
                200, content=b"MP3_BYTES",
                headers={"Content-Type": "audio/mpeg"},
            )
        )
        stt = await transcribe(cfg, AUDIO, "audio/webm")
        spoken_text = strip_markdown("The answer is **4**.")
        tts = await synthesize(cfg, spoken_text, "v1", "elevenlabs")
    assert stt.text == "what's two plus two"
    assert tts.audio == b"MP3_BYTES"
    assert "**" not in spoken_text
```

- [ ] **Step 2: Run it**

```bash
.venv/bin/pytest tests/test_voice_integration.py -v
```

Expected: 1 passed.

- [ ] **Step 3: Add the feature flag**

In `quadmux.html`, replace the mic click handler from Task 11 Step 3 with:

```javascript
document.querySelectorAll(".btn-voice").forEach((btn) => {
  btn.addEventListener("click", () => {
    const paneIdx = parseInt(btn.dataset.shell, 10);
    const v2 = localStorage.getItem("quadmux-voice-v2") === "1";
    if (!v2) {
      showBanner("Voice v2 is opt-in. Set localStorage.quadmux-voice-v2 = '1' and reload.", "warn");
      return;
    }
    toggleVoice(paneIdx);
  });
});
```

- [ ] **Step 4: Manual end-to-end test**

1. `.venv/bin/python quadmux-server.py --port 9876`
2. Open `http://localhost:9876`.
3. In DevTools console: `localStorage.setItem("quadmux-voice-v2", "1"); location.reload();`
4. Open voice settings; pick ElevenLabs - JARVIS; click Test Voice; hear JARVIS speak.
5. In pane 1 start `claude` and wait for the first prompt.
6. Click the mic on pane 1. Speak: "say hello". Wait 1.2s. Confirm the transcript is typed into the terminal and Claude responds.
7. When Claude's response streams, confirm it is spoken back.
8. Click the mic again to end the session.
9. Kill Kokoro (`pkill -f kokoro`), switch voice to Kokoro, Test Voice. Confirm banner reports Kokoro down; switching to browser tier works.

- [ ] **Step 5: Run full test suite**

```bash
.venv/bin/pytest -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add quadmux.html tests/test_voice_integration.py
git -c commit.gpgsign=false commit -m "feat(voice): integration test + feature flag gate"
```

---

## Task 15: Remove the v1 feature flag

Once the v2 path has run for a day or two without issues, remove the feature flag.

**Files:**
- Modify: `quadmux.html`

- [ ] **Step 1: Remove the flag check**

Delete the `const v2 = localStorage.getItem(...)` block from Task 14 Step 3, leaving just `toggleVoice(paneIdx)`.

- [ ] **Step 2: Grep for any remaining v1 leftovers**

```bash
grep -nE "WHISPER_URL|KOKORO_URL|voice_buffers|voice_capturing|voice_timers|extract_prose|voice_output_settled" quadmux-server.py quadmux.html
```

Expected output: empty.

- [ ] **Step 3: Run full suite**

```bash
.venv/bin/pytest -v
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add quadmux.html
git -c commit.gpgsign=false commit -m "feat(voice): remove v1 feature flag; v2 is the default"
```

---

## Completion checklist

- [ ] All pytest tests pass (`.venv/bin/pytest -v`).
- [ ] Server starts and `/api/health/voice` returns a JSON health block.
- [ ] The gear icon opens the voice settings modal; voices populate; test-voice produces audio.
- [ ] Clicking the mic on a pane with an active `claude` session transcribes a phrase and Claude's response is spoken back in the selected voice.
- [ ] Killing ElevenLabs (by clearing the env var) causes a visible banner and falls back to Whisper / Kokoro / browser TTS as applicable.
- [ ] No references to the v1 voice code remain.

## Glossary

- **VAD** - voice activity detection. Per-frame speech/silence classifier over 50 ms chunks.
- **Scribe** - ElevenLabs' STT product, model id `scribe_v1`.
- **Turbo v2.5** - ElevenLabs' low-latency TTS model, id `eleven_turbo_v2_5`.
- **Transcript jsonl** - Claude Code's per-session log at `~/.claude/projects/<encoded-cwd>/<session>.jsonl`.
