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
