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

# Telegram photos are JPEGs well under this; anything bigger is abuse.
_MAX_RECEIPT_BYTES = 8 * 1024 * 1024
# Decompression-bomb guard: ~25 megapixels is far above any real receipt.
MAX_IMAGE_PIXELS = 25_000_000


def save_receipt_photo(tip_id: str, data: bytes) -> str | None:
    """Persist a receipt screenshot and return its storage path (None on failure).

    Validates size and image-ness first: only real images within the size and
    pixel budget are stored, so garbage uploads never reach disk or OCR.
    """
    if not data or len(data) > _MAX_RECEIPT_BYTES:
        return None

    try:
        from io import BytesIO

        from PIL import Image, UnidentifiedImageError

        with Image.open(BytesIO(data)) as img:
            if img.width * img.height > MAX_IMAGE_PIXELS:
                logger.warning(
                    "Rejected oversized receipt image for tip %s (%dx%d)",
                    tip_id,
                    img.width,
                    img.height,
                )
                return None
            img.verify()
    except (UnidentifiedImageError, OSError, ValueError):
        logger.warning("Rejected non-image receipt upload for tip %s", tip_id)
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
