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
