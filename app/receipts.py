"""PDF receipt of a creator's verified tip history (Pro feature).

Rendered with reportlab's stock Helvetica fonts, which cannot draw Ethiopic
glyphs, so every free-text field is transliterated to ASCII before it reaches
the page — receipts stay readable even when names/notes contain Amharic.
"""
import io
import re
from datetime import datetime, timezone
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

_NON_ASCII = re.compile(r"[^\x20-\x7E]")


def _ascii_safe(value: str | None, fallback: str = "") -> str:
    """Collapse non-latin1 text so stock PDF fonts can render it."""
    if not value:
        return fallback
    return _NON_ASCII.sub("?", str(value)).strip() or fallback


def _shorten(value: str, font: str, size: float, max_width: float) -> str:
    if stringWidth(value, font, size) <= max_width:
        return value
    while value and stringWidth(value + "...", font, size) > max_width:
        value = value[:-1]
    return value + "..."


def build_tips_pdf(tips, creator, generated_by: str = "Tipa") -> bytes:
    """Render the creator's verified tips as a one-file PDF receipt."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title="Tipa Tip History Receipt",
        author=generated_by,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("ReceiptTitle", parent=styles["Title"], fontSize=16, spaceAfter=2)
    meta_style = ParagraphStyle("Meta", parent=styles["Normal"], textColor=colors.HexColor("#555555"), fontSize=9)

    method = _ascii_safe(getattr(creator, "payment_method", ""), "n/a").upper()
    account_tail = _ascii_safe(getattr(creator, "account_number", ""))
    masked_account = f"****{account_tail[-4:]}" if len(account_tail) >= 4 else "****"
    holder = _ascii_safe(getattr(creator, "account_name", ""), "Creator")
    issued = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    story: list = [
        Paragraph("Tipa Tip History Receipt", title_style),
        Paragraph(f"Creator: <b>{_ascii_safe(getattr(creator, 'display_name', ''), 'Creator')}</b>", meta_style),
        Paragraph(f"Payout account: {method} {masked_account} ({holder})", meta_style),
        Paragraph(f"Generated: {issued} &middot; Verified tips only", meta_style),
        Spacer(1, 6 * mm),
    ]

    header = ["Date", "Tipper", "Amount (ETB)", "Fee", "Via", "Tx Ref"]
    rows: list[list[str]] = []
    total_gross = Decimal(0)
    total_fee = Decimal(0)

    cell_font, cell_size = "Helvetica", 8.5
    col_widths = [28 * mm, 42 * mm, 24 * mm, 18 * mm, 26 * mm, 34 * mm]

    for tip in tips:
        amount = Decimal(str(tip.amount))
        fee = Decimal(str(tip.platform_fee))
        total_gross += amount
        total_fee += fee
        when = (tip.verified_at or tip.created_at)
        rows.append(
            [
                _ascii_safe(when.strftime("%Y-%m-%d") if when else ""),
                _shorten(_ascii_safe(tip.tipper_display_name, "Anonymous"), cell_font, cell_size, col_widths[1] - 6),
                f"{float(amount):,.2f}",
                f"{float(fee):,.2f}",
                _ascii_safe((tip.verification_method or "")[:12]).upper(),
                _shorten(_ascii_safe(tip.tx_ref), cell_font, cell_size, col_widths[5] - 6),
            ]
        )

    table_data = [header] + rows
    table = Table(table_data, colWidths=col_widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), cell_size),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f6feb")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f6fc")]),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c9d4e4")),
                ("ALIGN", (2, 0), (3, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 5 * mm))

    net = total_gross - total_fee
    summary_style = ParagraphStyle("Summary", parent=styles["Normal"], fontSize=10, leading=15)
    story.append(
        Paragraph(
            f"<b>{len(rows)}</b> tips &middot; Gross <b>{float(total_gross):,.2f} ETB</b> &middot; "
            f"Platform fees {float(total_fee):,.2f} ETB &middot; Net to creator "
            f"<b>{float(net):,.2f} ETB</b>",
            summary_style,
        )
    )

    doc.build(story)
    return buffer.getvalue()
