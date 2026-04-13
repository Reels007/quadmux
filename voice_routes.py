"""HTTP route dispatcher for voice-related endpoints.

Returns (status, headers, body_bytes) on match, or None if the path isn't
handled here (caller falls through to HTML).

Routes:
  GET  /api/health/voice        - status of Whisper, Kokoro, ElevenLabs
  GET  /api/voice/start         - launch voicemode whisper + kokoro services
  GET  /api/tts/voices          - list available voices per engine
  GET  /api/tts/elevenlabs?...  - synthesize text via ElevenLabs (returns MP3)
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import time
import urllib.request
from typing import Iterable, Optional, Tuple
from urllib.parse import urlparse, parse_qs

Headers = Iterable[Tuple[str, str]]
RouteResult = Optional[Tuple[int, Headers, bytes]]

ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()

# Voice shortlist when ElevenLabs /v1/voices is unreachable.
# Daniel is the JARVIS default (British, formal).
ELEVENLABS_FALLBACK_VOICES = [
    {"voice_id": "onwK4e9ZLuTAKqWW03F9", "name": "Daniel (JARVIS)"},
    {"voice_id": "21m00Tcm4TlvDq8ikWAM", "name": "Rachel"},
    {"voice_id": "TxGEqnHWrfWFTfGW9XjX", "name": "Josh"},
    {"voice_id": "ErXwobaYiN019PkySvjV", "name": "Antoni"},
    {"voice_id": "EXAVITQu4vr4xnSDxMaL", "name": "Bella"},
]

KOKORO_VOICES = [
    {"voice_id": "af_heart",   "name": "Heart (American F)"},
    {"voice_id": "af_alloy",   "name": "Alloy (American F)"},
    {"voice_id": "af_bella",   "name": "Bella (American F)"},
    {"voice_id": "af_nova",    "name": "Nova (American F)"},
    {"voice_id": "am_adam",    "name": "Adam (American M)"},
    {"voice_id": "am_michael", "name": "Michael (American M)"},
    {"voice_id": "bf_emma",    "name": "Emma (British F)"},
    {"voice_id": "bm_daniel",  "name": "Daniel (British M)"},
    {"voice_id": "bm_george",  "name": "George (British M)"},
]

_voices_cache: dict = {"ts": 0.0, "data": None}


def _port_open(host: str, port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


async def dispatch(request) -> RouteResult:
    path = getattr(request, "path", "")
    if path == "/api/health/voice":
        return await _health()
    if path == "/api/voice/start":
        return await _start_services()
    if path == "/api/tts/voices":
        return await _voices()
    if path.startswith("/api/tts/elevenlabs"):
        return await _tts_elevenlabs(path)
    return None


async def _health() -> RouteResult:
    whisper_ok, kokoro_ok = await asyncio.gather(
        asyncio.to_thread(_port_open, "localhost", 2022),
        asyncio.to_thread(_port_open, "localhost", 8880),
    )
    elevenlabs = "no-key" if not ELEVENLABS_API_KEY else "ok"
    body = json.dumps({
        "whisper":    "ok" if whisper_ok else "down",
        "kokoro":     "ok" if kokoro_ok  else "down",
        "elevenlabs": elevenlabs,
    }).encode()
    return 200, [("Content-Type", "application/json"), ("Cache-Control", "no-store")], body


async def _start_services() -> RouteResult:
    """Launch voicemode whisper + kokoro in the background. Returns immediately."""
    started, errors = [], []
    for svc in ("whisper", "kokoro"):
        try:
            proc = await asyncio.create_subprocess_exec(
                "voicemode", "service", "start", svc,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            started.append(svc)
            asyncio.create_task(_reap(proc, svc))
        except FileNotFoundError:
            errors.append(f"{svc}: voicemode CLI not found")
        except Exception as e:
            errors.append(f"{svc}: {e}")
    body = json.dumps({"started": started, "errors": errors}).encode()
    return 200, [("Content-Type", "application/json")], body


async def _reap(proc, svc: str) -> None:
    try:
        _, err = await proc.communicate()
        if proc.returncode not in (0, None):
            msg = err.decode(errors="replace")[:300] if err else ""
            print(f"  voicemode {svc} start rc={proc.returncode}: {msg}", flush=True)
    except Exception:
        pass


async def _voices() -> RouteResult:
    now = time.time()
    if _voices_cache["data"] and now - _voices_cache["ts"] < 300:
        body = _voices_cache["data"]
    else:
        el_list = (await asyncio.to_thread(_fetch_elevenlabs_voices)
                   if ELEVENLABS_API_KEY else [])
        if not el_list:
            el_list = ELEVENLABS_FALLBACK_VOICES
        body = json.dumps({"kokoro": KOKORO_VOICES, "elevenlabs": el_list}).encode()
        _voices_cache.update(ts=now, data=body)
    return 200, [("Content-Type", "application/json")], body


def _fetch_elevenlabs_voices() -> list:
    try:
        req = urllib.request.Request(
            "https://api.elevenlabs.io/v1/voices",
            headers={"xi-api-key": ELEVENLABS_API_KEY},
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read())
        return [{"voice_id": v["voice_id"], "name": v["name"]}
                for v in data.get("voices", [])]
    except Exception:
        return []


async def _tts_elevenlabs(path: str) -> RouteResult:
    """Synthesize via ElevenLabs. Query: text, voice_id, model_id, speed."""
    if not ELEVENLABS_API_KEY:
        return 400, [("Content-Type", "text/plain")], b"ELEVENLABS_API_KEY not set"

    params = parse_qs(urlparse(path).query)
    text = (params.get("text", [""])[0] or "").strip()
    voice_id = params.get("voice_id", ["onwK4e9ZLuTAKqWW03F9"])[0]
    model_id = params.get("model_id", ["eleven_turbo_v2_5"])[0]
    try:
        speed = max(0.7, min(1.5, float(params.get("speed", ["1.1"])[0])))
    except ValueError:
        speed = 1.1

    if not text:
        return 400, [("Content-Type", "text/plain")], b"text required"
    if len(text) > 2000:
        text = text[:2000]

    try:
        audio = await asyncio.to_thread(_call_elevenlabs, text, voice_id, model_id, speed)
    except Exception as e:
        return 502, [("Content-Type", "text/plain")], f"ElevenLabs error: {e}".encode()

    return 200, [("Content-Type", "audio/mpeg"), ("Cache-Control", "no-store")], audio


def _call_elevenlabs(text: str, voice_id: str, model_id: str, speed: float) -> bytes:
    url = (f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
           "?optimize_streaming_latency=3&output_format=mp3_44100_128")
    payload = json.dumps({
        "text": text,
        "model_id": model_id,
        "voice_settings": {
            "stability": 0.4,
            "similarity_boost": 0.75,
            "speed": speed,
        },
    }).encode()
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={
            "xi-api-key": ELEVENLABS_API_KEY,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()
