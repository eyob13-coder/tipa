"""Receipt evidence storage.

Payment screenshots are persisted to disk (configurable directory, swap for
S3/blob storage later) so disputes can be resolved with evidence instead of
vanishing after OCR — previously the image lived only in memory.
"""
import logging
import uuid
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


def save_receipt_photo(tip_id: str, data: bytes) -> str | None:
    """Persist a receipt screenshot and return its storage path (None on failure)."""
    if not data:
        return None
    try:
        tip_dir = Path(settings.receipt_storage_dir) / tip_id
        tip_dir.mkdir(parents=True, exist_ok=True)
        path = tip_dir / f"{uuid.uuid4().hex}.jpg"
        path.write_bytes(data)
        return str(path)
    except OSError:
        logger.exception("Failed to persist receipt screenshot for tip %s", tip_id)
        return None
