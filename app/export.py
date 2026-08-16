"""Shared CSV export of a creator's verified tip history."""
import csv
import io


def build_tips_csv(tips) -> str:
    """Render tip rows as CSV text (reconciliation trail).

    Columns: date, tipper name, amount, note, verification method, tx ref.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["date", "tipper_name", "amount_etb", "note", "verification_method", "tx_ref"])
    for tip in tips:
        writer.writerow(
            [
                (tip.verified_at or tip.created_at).strftime("%Y-%m-%d %H:%M"),
                tip.tipper_display_name or "Anonymous",
                f"{float(tip.amount):.2f}",
                tip.note or "",
                tip.verification_method or "",
                tip.tx_ref,
            ]
        )
    return buffer.getvalue()
