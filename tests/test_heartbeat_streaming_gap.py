"""Characterises where the in-band heartbeat is and is not reached.

``recover_stale_jobs()`` only sees a refreshed ``updated_at``. The heartbeat is
in-band: it runs where a ``check()`` hook is called. This module records, per
blocking operation, how many times a callback is invoked while the operation is
actually in flight, so the coverage claim is measured rather than assumed.

These are deliberately *characterisation* tests of plumbing reachability, not
runtime proofs: they say nothing about whether a real model request takes long
enough to matter. Classification is stated per test.
"""

from __future__ import annotations

import threading
import time

import httpx

from app import adapters, agents, inference_coordination


def _streaming_response(chunks: list[bytes], gap: float) -> httpx.Response:
    """A response whose body arrives incrementally with ``gap`` between chunks."""

    def body():
        for chunk in chunks:
            time.sleep(gap)
            yield chunk

    return httpx.Response(
        200,
        headers={"content-encoding": "identity"},
        content=body(),
    )


def test_check_runs_between_streamed_chunks(monkeypatch):
    """Plumbing: a chunked response ticks the caller's check between chunks.

    Classification: deterministic unit. Proves the callback is reached while the
    body is still arriving - i.e. coverage exists for a *streaming* provider.
    """
    ticks: list[float] = []

    def transport(request: httpx.Request) -> httpx.Response:
        return _streaming_response([b'{"a":', b" 1", b"}"], gap=0.01)

    transport = httpx.MockTransport(transport)
    real_client = httpx.Client

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(agents.httpx, "Client", client_factory)
    # ``_request`` imports the lease guard lazily from its defining module.
    monkeypatch.setattr(
        inference_coordination, "require_active_inference_lease", lambda: None
    )

    adapter = adapters.SharedOllamaTransport.__new__(adapters.SharedOllamaTransport)
    adapter.endpoint = "http://provider.test"
    adapter.model = "m"
    adapter.model_digest = "0" * 64
    adapter.stage_timeout_seconds = 60
    adapter.generation_timeout_seconds = 300

    adapter._request("GET", "/api/tags", check=lambda: ticks.append(time.monotonic()))

    assert len(ticks) >= 3, f"check reached only {len(ticks)} times across 3 chunks"


def test_check_is_not_reached_during_a_silent_body_delay(monkeypatch):
    """Plumbing gap: a provider that sends nothing until the end gets no ticks.

    Classification: deterministic unit. The first chunk is delayed; nothing can
    call ``check()`` because ``iter_bytes()`` has nothing to yield. This is the
    known limitation of in-band coverage, recorded so it cannot be over-claimed.
    """
    ticks: list[float] = []

    def slow_then_done():
        time.sleep(0.4)
        yield b"{}"

    def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-encoding": "identity"},
            content=slow_then_done(),
        )

    real_client = httpx.Client

    def client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(transport)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(agents.httpx, "Client", client_factory)
    monkeypatch.setattr(
        inference_coordination, "require_active_inference_lease", lambda: None
    )

    adapter = adapters.SharedOllamaTransport.__new__(adapters.SharedOllamaTransport)
    adapter.endpoint = "http://provider.test"
    adapter.model = "m"
    adapter.model_digest = "0" * 64
    adapter.stage_timeout_seconds = 60
    adapter.generation_timeout_seconds = 300

    observed: list[int] = []

    def watcher():
        # Sample the tick count while the request is still blocked.
        time.sleep(0.2)
        observed.append(len(ticks))

    thread = threading.Thread(target=watcher)
    thread.start()
    adapter._request("GET", "/api/tags", check=lambda: ticks.append(time.monotonic()))
    thread.join()

    assert observed == [1], (
        "expected exactly the pre-request tick during the silent wait, "
        f"observed {observed}"
    )
