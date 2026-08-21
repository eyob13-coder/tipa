"""Vision-LLM receipt parsing (#8): read reference codes from screenshots.

Optional upgrade over the pytesseract fallback: when VISION_LLM_API_KEY is
configured, receipt screenshots are sent to any OpenAI-compatible vision
model which returns structured JSON (reference code + amount). Without a
key — or on any failure — the caller falls back to local OCR unchanged.
"""
import base64
import json
import logging
import re

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 20.0

_PROMPT = (
    "This is a screenshot of an Ethiopian mobile-money or bank payment "
    "confirmation (Telebirr, CBE Birr, Awash, Dashen, etc). Read it and reply "
    "with ONLY a single line of minified JSON, no prose, no code fences:\n"
    '{"reference": "<transaction reference code as printed>", "amount": <numeric amount in ETB>}\n'
    "Use null for either field if it is not visible."
)


def _extract_json(text: str) -> dict | None:
    """Pull the first JSON object out of model output (handles ``` fences)."""
    if not text:
        return None
    text = re.sub(r"```(?:json)?", "", text).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _validate(data: dict) -> dict | None:
    reference = data.get("reference")
    amount = data.get("amount")

    cleaned_ref: str | None = None
    if isinstance(reference, str):
        candidate = re.sub(r"[^A-Za-z0-9\-_]", "", reference).upper()
        if 6 <= len(candidate) <= 30:
            cleaned_ref = candidate

    cleaned_amount: float | None = None
    try:
        value = float(amount)
        if 0 < value <= 1_000_000:
            cleaned_amount = value
    except (TypeError, ValueError):
        pass

    if not cleaned_ref:
        return None
    return {"reference": cleaned_ref, "amount": cleaned_amount}


async def parse_receipt_image(image_bytes: bytes) -> dict | None:
    """Ask the configured vision model to read a receipt. Never raises.

    Returns {"reference": str, "amount": float|None}, or None when the
    feature is disabled, the model can't be reached, or nothing useful
    was extracted.
    """
    if not settings.vision_llm_api_key or not image_bytes:
        return None

    data_uri = "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode()
    body = {
        "model": settings.vision_llm_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _PROMPT},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }
        ],
        "max_tokens": 200,
        "temperature": 0,
    }
    headers = {
        "Authorization": f"Bearer {settings.vision_llm_api_key}",
        "Content-Type": "application/json",
    }

    try:
        url = f"{settings.vision_llm_base_url.rstrip('/')}/chat/completions"
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            resp = await client.post(url, json=body, headers=headers)
        resp.raise_for_status()

        content = resp.json()["choices"][0]["message"]["content"]
        parsed = _extract_json(content)
        if not parsed:
            logger.warning("Vision LLM returned unparseable content")
            return None
        result = _validate(parsed)
        if not result:
            logger.info("Vision LLM saw no usable reference in receipt")
        return result
    except Exception:  # network, auth, schema, anything — fall back to OCR
        logger.warning("Vision LLM receipt parsing failed, falling back to OCR", exc_info=True)
        return None
