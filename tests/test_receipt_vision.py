"""Tests for vision-LLM receipt parsing (#8)."""
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.receipt_vision import _extract_json, _validate, parse_receipt_image


def _openai_response(content: str):
    return {
        "choices": [
            {"message": {"role": "assistant", "content": content}}
        ]
    }


# --- pure helpers ---------------------------------------------------------------


def test_extract_json_handles_code_fences_and_prose():
    assert _extract_json('```json\n{"reference": "TLB12345678"}\n```') == {
        "reference": "TLB12345678"
    }
    assert _extract_json('Sure! Here it is: {"reference": "FT12345678", "amount": 5} done') == {
        "reference": "FT12345678",
        "amount": 5,
    }
    assert _extract_json("no json here") is None
    assert _extract_json("") is None


def test_validate_normalises_reference():
    cleaned = _validate({"reference": " tlb 12345678 ", "amount": "250"})
    assert cleaned == {"reference": "TLB12345678", "amount": 250.0}

    # Too short / missing -> rejected entirely.
    assert _validate({"reference": "ab"}) is None
    assert _validate({"amount": 100}) is None
    # Absurd amounts dropped, reference kept.
    assert _validate({"reference": "TLB12345678", "amount": -5})["amount"] is None


@pytest.mark.asyncio
async def test_no_api_key_means_no_network_call():
    from app.config import settings

    post = AsyncMock()
    with (
        patch.object(settings, "vision_llm_api_key", ""),
        patch("httpx.AsyncClient.post", post),
    ):
        result = await parse_receipt_image(b"fake-image-bytes")

    assert result is None
    post.assert_not_awaited()


@pytest.mark.asyncio
async def test_successful_vision_read():
    from app.config import settings

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.raise_for_status.return_value = None
    fake_resp.json.return_value = _openai_response(
        '{"reference": "CBETR987654321", "amount": 300.00}'
    )

    captured = {}

    async def _capture(url=None, json=None, headers=None, **kwargs):
        captured.update({"url": url, "body": json, "headers": headers})
        return fake_resp

    with (
        patch.object(settings, "vision_llm_api_key", "sk-test"),
        patch.object(settings, "vision_llm_model", "vision-x"),
        patch("httpx.AsyncClient.post", side_effect=_capture),
    ):
        result = await parse_receipt_image(b"img")

    assert result == {"reference": "CBETR987654321", "amount": 300.0}
    assert captured["url"].endswith("/chat/completions")
    assert captured["headers"]["Authorization"] == "Bearer sk-test"
    assert captured["body"]["model"] == "vision-x"


@pytest.mark.asyncio
async def test_garbage_content_returns_none():
    from app.config import settings

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.raise_for_status.return_value = None
    fake_resp.json.return_value = _openai_response("I see a cat, not a receipt.")

    post = AsyncMock(return_value=fake_resp)
    with (
        patch.object(settings, "vision_llm_api_key", "sk-test"),
        patch("httpx.AsyncClient.post", post),
    ):
        result = await parse_receipt_image(b"img")
    assert result is None


@pytest.mark.asyncio
async def test_network_error_never_raises():
    from app.config import settings

    post = AsyncMock(side_effect=httpx.ConnectError("boom"))
    with (
        patch.object(settings, "vision_llm_api_key", "sk-test"),
        patch("httpx.AsyncClient.post", post),
    ):
        result = await parse_receipt_image(b"img")
    assert result is None
