"""Printable A4 QR tip posters for cafés, events, and physical spaces.

The QR encodes the creator's deep link (https://t.me/<bot>?start=tip_<id>) so
one scan opens the tipping flow directly. Rendered with reportlab; the QR
itself is a PNG produced by the qrcode library.
"""
import io

import qrcode
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


def _ascii_safe(value: str | None, fallback: str = "") -> str:
    """Stock PDF fonts can't draw Ethiopic — transliterate to something safe."""
    if not value:
        return fallback
    cleaned = str(value).encode("ascii", "replace").decode("ascii")
    return cleaned.strip() or fallback


def tip_deep_link(bot_username: str, creator_id) -> str:
    return f"https://t.me/{bot_username}?start=tip_{creator_id}"


def build_qr_png(url: str, box_size: int = 10) -> bytes:
    qr = qrcode.QRCode(box_size=box_size, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _fit_text(value: str, font: str, size: float, max_width: float) -> str:
    if stringWidth(value, font, size) <= max_width:
        return value
    while value and stringWidth(value + "...", font, size) > max_width:
        value = value[:-1]
    return value + "..."


def build_poster_pdf(creator, url: str) -> bytes:
    """A4 poster: headline, creator name, big scannable QR, footer."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title="Tipa Tip Poster",
        author="Tipa",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("PosterTitle", parent=styles["Title"], fontSize=30, leading=36)
    name_style = ParagraphStyle(
        "CreatorName", parent=styles["Normal"], fontSize=20, leading=26, alignment=1
    )
    foot_style = ParagraphStyle(
        "Foot", parent=styles["Normal"], fontSize=11, leading=15, alignment=1
    )

    display_name = _ascii_safe(getattr(creator, "display_name", ""), "This Creator")
    safe_url = url if len(url) <= 60 else url[:57] + "..."

    title_para = Paragraph("Support this creator in Birr", title_style)
    # Title wraps naturally; center it via table below.
    title_table = Table([[title_para]], colWidths=[170 * mm])
    title_table.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER")]))

    qr_png = build_qr_png(url, box_size=12)
    qr_img = Image(io.BytesIO(qr_png), width=120 * mm, height=120 * mm)

    story = [
        Spacer(1, 8 * mm),
        title_table,
        Spacer(1, 6 * mm),
        Paragraph(f"<b>{_fit_text(display_name, 'Helvetica-Bold', 20, 160 * mm)}</b>", name_style),
        Spacer(1, 8 * mm),
        qr_img,
        Spacer(1, 10 * mm),
        Paragraph("📱 Scan · Tap · Tip in ETB — the money goes straight to the creator.", foot_style),
        Spacer(1, 2 * mm),
        Paragraph(safe_url, foot_style),
        Paragraph("Powered by Tipa 🎁", foot_style),
    ]

    doc.build(story)
    return buffer.getvalue()
