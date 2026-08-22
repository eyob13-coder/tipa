"""Group E regression tests: receipt upload validation (size, type, pixels)."""
import io

import pytest
from PIL import Image

from app.storage import MAX_IMAGE_PIXELS, save_receipt_photo


def _jpeg(width=4, height=4):
    buf = io.BytesIO()
    Image.new("RGB", (width, height)).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def receipt_dir(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "receipt_storage_dir", str(tmp_path / "r"))
    return tmp_path / "r"


def test_non_image_bytes_rejected(receipt_dir):
    assert save_receipt_photo("t1", b"\x00\x01not an image at all") is None
    assert not receipt_dir.exists()


def test_oversized_pixel_budget_rejected(receipt_dir, monkeypatch):
    # Shrink the budget so a small real image still trips the guard.
    monkeypatch.setattr("app.storage.MAX_IMAGE_PIXELS", 16)
    assert save_receipt_photo("t2", _jpeg(10, 10)) is None


def test_valid_small_jpeg_stored(receipt_dir):
    data = _jpeg()
    path = save_receipt_photo("t3", data)
    assert path is not None
    with open(path, "rb") as f:
        assert f.read() == data


def test_size_bounds(receipt_dir):
    assert save_receipt_photo("t4", b"") is None
    assert save_receipt_photo("t4", b"x" * (8 * 1024 * 1024 + 1)) is None


def test_pixel_budget_is_sane():
    assert MAX_IMAGE_PIXELS == 25_000_000
