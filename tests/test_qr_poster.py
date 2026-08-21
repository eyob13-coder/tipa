"""Tests for printable QR tip posters (#10)."""
import io

from app.posters import _ascii_safe, build_poster_pdf, build_qr_png, tip_deep_link


class _Creator:
    display_name = "ኢዮብ መቀኝት"


def test_tip_deep_link_format():
    assert tip_deep_link("TipaPayBot", "abc-123") == "https://t.me/TipaPayBot?start=tip_abc-123"


def test_qr_png_is_valid_and_encodes_link():
    png = build_qr_png("https://t.me/x?start=tip_y")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 500

    # Decode the QR back and confirm the payload survives round-trip.
    try:
        from PIL import Image
        from pyzbar.pyzbar import decode

        results = decode(Image.open(io.BytesIO(png)))
    except ImportError:
        import pytest

        pytest.skip("pyzbar not installed; visual round-trip skipped")
        return
    assert results and results[0].data.decode() == "https://t.me/x?start=tip_y"


def test_poster_pdf_structure():
    pdf = build_poster_pdf(_Creator(), "https://t.me/bot?start=tip_x")
    assert pdf[:5] == b"%PDF-"


def test_ascii_safe_fallbacks():
    assert _ascii_safe("", "Fallback") == "Fallback"
    assert _ascii_safe(None, "F") == "F"
    assert _ascii_safe("ሰላም")  # Ethiopic -> non-empty ASCII replacement


def test_fit_text_truncates_long_names():
    from app.posters import _fit_text

    long_name = "Very Long Creator Name That Will Not Fit On One Line At All"
    fitted = _fit_text(long_name, "Helvetica-Bold", 20, 160 * 2.834645669)  # ~160mm in points
    assert len(fitted) < len(long_name)
    assert fitted.endswith("...")
