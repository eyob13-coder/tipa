"""Tests for the live tip overlay (#7): hub, page rendering, SSE generator."""
import json
from unittest.mock import patch

import pytest

from app import overlay
from app.main import app
from app.overlay import _KEEPALIVE_SECONDS  # noqa: F401  (referenced via patch.object)


@pytest.fixture(autouse=True)
def _clean_hub():
    overlay._SUBSCRIBERS.clear()
    yield
    overlay._SUBSCRIBERS.clear()


# --- hub ----------------------------------------------------------------------


def test_hub_roundtrip():
    q = overlay.subscribe("creator-a")
    assert overlay.subscriber_count("creator-a") == 1

    overlay.publish_tip(
        "creator-a", {"amount": "50.50", "tipper": "Abebe", "note": "goat"}
    )
    event = q.get_nowait()
    assert event["event"] == "tip"
    assert event["amount"] == 50.5
    assert event["tipper"] == "Abebe"
    assert event["note"] == "goat"

    overlay.unsubscribe("creator-a", q)
    assert overlay.subscriber_count("creator-a") == 0


def test_publish_without_subscribers_is_noop():
    overlay.publish_tip("ghost", {"amount": 1})  # must not raise


def test_publish_fans_out_to_all_viewers():
    q1, q2 = overlay.subscribe("creator-b"), overlay.subscribe("creator-b")
    other = overlay.subscribe("creator-c")

    overlay.publish_tip("creator-b", {"amount": 7})

    assert q1.get_nowait()["amount"] == 7
    assert q2.get_nowait()["amount"] == 7
    assert other.empty()
    assert overlay.subscriber_count("creator-c") == 1


def test_full_queue_drops_event_not_crashes():
    q = overlay.subscribe("creator-d")  # maxsize=32
    for i in range(32):
        overlay.publish_tip("creator-d", {"amount": i})
    # Overflow event is dropped silently instead of raising QueueFull.
    overlay.publish_tip("creator-d", {"amount": 999})
    assert q.qsize() == 32


def test_missing_tipper_defaults_to_anonymous():
    q = overlay.subscribe("creator-e")
    overlay.publish_tip("creator-e", {})
    assert q.get_nowait()["tipper"] == "A follower"


# --- page rendering ------------------------------------------------------------


@pytest.mark.asyncio
async def test_overlay_page_renders_and_escapes_id():
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/overlay/abc-123")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "__CREATOR_ID__" not in resp.text
        assert '"abc-123"' in resp.text


@pytest.mark.asyncio
async def test_overlay_page_escapes_html_injection():
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Slash-free XSS payload (a raw or encoded "/" won't match the route).
        resp = await client.get("/overlay/%3Cimg%20src%3Dx%20onerror%3Dalert(1)%3E")
        assert resp.status_code == 200
        assert "<img" not in resp.text
        assert "&lt;img" in resp.text


# --- SSE generator (driven directly: ASGI transports buffer infinite streams) ---


@pytest.mark.asyncio
async def test_stream_sends_retry_then_published_tip():
    import asyncio

    from app.main import overlay_stream

    response = await overlay_stream("live-one")
    assert response.media_type == "text/event-stream"
    assert response.headers.get("cache-control") == "no-cache"

    try:
        first = await asyncio.wait_for(response.body_iterator.__anext__(), timeout=5)
        assert first == "retry: 3000\n\n"

        # Publish while the stream is open (same loop -> thread-safe).
        overlay.publish_tip(
            "live-one", {"amount": 42, "tipper": "Selam", "note": "for coffee"}
        )

        second = await asyncio.wait_for(response.body_iterator.__anext__(), timeout=5)
        payload_line = next(
            line for line in second.splitlines() if line.startswith("data:")
        )
        received = json.loads(payload_line.removeprefix("data:"))
        assert received == {
            "event": "tip",
            "amount": 42,
            "tipper": "Selam",
            "note": "for coffee",
        }
    finally:
        await response.body_iterator.aclose()  # triggers unsubscribe in finally

    assert overlay.subscriber_count("live-one") == 0


@pytest.mark.asyncio
async def test_stream_keepalive_when_idle():
    import asyncio

    from app.main import overlay_stream

    # main.py resolves _KEEPALIVE_SECONDS at request time -> patch before calling.
    with patch.object(overlay, "_KEEPALIVE_SECONDS", 0.01):
        response = await overlay_stream("idle-one")
        try:
            first = await asyncio.wait_for(response.body_iterator.__anext__(), timeout=5)
            assert first == "retry: 3000\n\n"

            second = await asyncio.wait_for(
                response.body_iterator.__anext__(), timeout=5
            )
            assert ": keepalive" in second
        finally:
            await response.body_iterator.aclose()
