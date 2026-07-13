"""
Minimal fake httpx.AsyncClient for adapter tests — no real network calls.
Queue canned FakeResponse objects; each get()/post() pops the next one and
records the call (method, url, params_or_json) for assertions.
"""
import httpx


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.text = text or ""

    def json(self):
        return self._json


class _FakeAsyncClient:
    def __init__(self, responses, calls, raise_timeout=False, **kwargs):
        # NOTE: `responses` must be the *same* list object shared across
        # every httpx.AsyncClient(...) instantiation (one per request in the
        # real adapters), not a copy — otherwise each new client would see
        # the full original queue again instead of continuing where the
        # previous one left off.
        self._responses = responses
        self._calls = calls
        self._raise_timeout = raise_timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None, params=None):
        if self._raise_timeout:
            raise httpx.TimeoutException("simulated timeout")
        self._calls.append(("GET", url, params))
        return self._responses.pop(0)

    async def post(self, url, headers=None, params=None, json=None):
        if self._raise_timeout:
            raise httpx.TimeoutException("simulated timeout")
        self._calls.append(("POST", url, json))
        return self._responses.pop(0)

    async def put(self, url, headers=None, params=None, json=None):
        if self._raise_timeout:
            raise httpx.TimeoutException("simulated timeout")
        self._calls.append(("PUT", url, json))
        return self._responses.pop(0)

    async def patch(self, url, headers=None, params=None, json=None):
        if self._raise_timeout:
            raise httpx.TimeoutException("simulated timeout")
        self._calls.append(("PATCH", url, json))
        return self._responses.pop(0)

    async def delete(self, url, headers=None, params=None):
        if self._raise_timeout:
            raise httpx.TimeoutException("simulated timeout")
        self._calls.append(("DELETE", url, None))
        return self._responses.pop(0)


def make_fake_async_client(responses, calls=None, raise_timeout=False):
    """Returns a factory usable as a monkeypatch replacement for
    httpx.AsyncClient(timeout=...). `calls` (a list) is appended to for
    every request made, if provided."""
    calls = calls if calls is not None else []
    shared_responses = list(responses)

    def factory(*args, **kwargs):
        return _FakeAsyncClient(shared_responses, calls, raise_timeout=raise_timeout)

    return factory
