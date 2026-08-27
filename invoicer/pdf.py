"""Render an Invoice to a PDF laid out like a standard Word invoice template."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .config import Config
from .invoice import Invoice
from .parsing import format_hours

INK = colors.HexColor("#1F2933")
MUTED = colors.HexColor("#6B7280")
ACCENT = colors.HexColor("#1F3A5F")
RULE = colors.HexColor("#D4D9DF")
BAND = colors.HexColor("#F2F4F7")

PAGE_SIZES = {"A4": A4, "LETTER": LETTER}


def _styles():
    base = ParagraphStyle(
        "base", fontName="Helvetica", fontSize=9.5, leading=13.5, textColor=INK
    )
    return {
        "base": base,
        "company": ParagraphStyle(
            "company", parent=base, fontName="Helvetica-Bold", fontSize=16, leading=19,
            textColor=ACCENT,
        ),
        "small": ParagraphStyle("small", parent=base, fontSize=8.5, leading=12, textColor=MUTED),
        "title": ParagraphStyle(
            "title", parent=base, fontName="Helvetica-Bold", fontSize=26, leading=28,
            alignment=TA_RIGHT, textColor=ACCENT,
        ),
        "label": ParagraphStyle(
            "label", parent=base, fontName="Helvetica-Bold", fontSize=8, leading=11,
            textColor=MUTED,
        ),
        "th": ParagraphStyle(
            "th", parent=base, fontName="Helvetica-Bold", fontSize=8.5, leading=11,
            textColor=colors.white,
        ),
        "th_r": ParagraphStyle(
            "th_r", parent=base, fontName="Helvetica-Bold", fontSize=8.5, leading=11,
            textColor=colors.white, alignment=TA_RIGHT,
        ),
        "cell": ParagraphStyle("cell", parent=base, fontSize=9, leading=12),
        "cell_r": ParagraphStyle("cell_r", parent=base, fontSize=9, leading=12, alignment=TA_RIGHT),
        "total": ParagraphStyle(
            "total", parent=base, fontName="Helvetica-Bold", fontSize=11.5, leading=14,
            alignment=TA_RIGHT,
        ),
    }


class _NumberedCanvas(pdfcanvas.Canvas):
    """Two-pass canvas so the footer can say 'Page 1 of 3'."""

    def __init__(self, *args, footer_text: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self._saved = []
        self._footer_text = footer_text

    def showPage(self):
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved)
        for state in self._saved:
            self.__dict__.update(state)
            self._draw_footer(total)
            super().showPage()
        super().save()

    def _draw_footer(self, total_pages: int):
        width, _ = self._pagesize
        self.setStrokeColor(RULE)
        self.setLineWidth(0.5)
        self.line(18 * mm, 15 * mm, width - 18 * mm, 15 * mm)
        self.setFont("Helvetica", 7.5)
        self.setFillColor(MUTED)
        if self._footer_text:
            self.drawString(18 * mm, 10.5 * mm, self._footer_text[:120])
        self.drawRightString(
            width - 18 * mm, 10.5 * mm, f"Page {self._pageNumber} of {total_pages}"
        )


def _fmt_money(symbol: str, value: Decimal) -> str:
    return f"{symbol}{value:,.2f}"


def _header(invoice: Invoice, config: Config, st: dict) -> Table:
    company = config.company
    left = []
    logo_path = Path(company.logo) if company.logo else None
    if logo_path and logo_path.exists():
        try:
            left.append(Image(str(logo_path), width=45 * mm, height=15 * mm, kind="proportional"))
            left.append(Spacer(1, 4))
        except Exception:
            pass  # a bad logo must never stop an invoice from being produced
    left.append(Paragraph(company.name, st["company"]))
    contact = list(company.address_lines)
    if company.phone:
        contact.append(company.phone)
    if company.email:
        contact.append(company.email)
    if company.tax_id:
        contact.append(company.tax_id)
    if contact:
        left.append(Spacer(1, 3))
        left.append(Paragraph("<br/>".join(contact), st["small"]))

    meta_rows = [
        ("INVOICE NO.", invoice.number),
        ("DATE ISSUED", invoice.issue_date.strftime("%d %b %Y")),
        ("DUE DATE", invoice.due_date.strftime("%d %b %Y")),
    ]
    if invoice.period_label:
        meta_rows.append(("BILLING PERIOD", invoice.period_label))

    meta = Table(
        [[Paragraph(k, st["label"]), Paragraph(v, st["cell_r"])] for k, v in meta_rows],
        colWidths=[30 * mm, 34 * mm],
    )
    meta.setStyle(
        TableStyle(
            [
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    right = [Paragraph("INVOICE", st["title"]), Spacer(1, 8), meta]

    header = Table([[left, right]], colWidths=[95 * mm, 79 * mm], hAlign="LEFT")
    header.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return header


def _bill_to(invoice: Invoice, st: dict) -> Table:
    client = invoice.client
    lines = list(client.bill_to) or [client.key]
    if client.email and client.email not in lines:
        lines.append(client.email)
    block = [
        Paragraph("BILL TO", st["label"]),
        Spacer(1, 3),
        Paragraph(f"<b>{lines[0]}</b>", st["cell"]),
    ]
    if len(lines) > 1:
        block.append(Paragraph("<br/>".join(lines[1:]), st["small"]))

    # hAlign is explicit: reportlab centres tables narrower than the frame.
    table = Table([[block]], colWidths=[95 * mm], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return table


def _line_items(invoice: Invoice, config: Config, st: dict) -> Table:
    symbol = invoice.currency_symbol
    decimals = config.invoice.hours_decimals

    data = [
        [
            Paragraph("DESCRIPTION", st["th"]),
            Paragraph("HOURS", st["th_r"]),
            Paragraph("RATE", st["th_r"]),
            Paragraph("AMOUNT", st["th_r"]),
        ]
    ]
    for item in invoice.line_items:
        text = item.description
        if item.first_date and item.last_date:
            span = (
                item.first_date.strftime("%d %b")
                if item.first_date == item.last_date
                else f"{item.first_date.strftime('%d %b')} - {item.last_date.strftime('%d %b')}"
            )
            text += f'<br/><font size="7.5" color="#6B7280">{span}</font>'
        data.append(
            [
                Paragraph(text, st["cell"]),
                Paragraph(format_hours(item.hours, decimals), st["cell_r"]),
                Paragraph(_fmt_money(symbol, item.rate), st["cell_r"]),
                Paragraph(_fmt_money(symbol, item.amount), st["cell_r"]),
            ]
        )

    table = Table(data, colWidths=[94 * mm, 20 * mm, 28 * mm, 32 * mm],
                  repeatRows=1, hAlign="LEFT")
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, RULE),
    ]
    for idx in range(1, len(data)):
        if idx % 2 == 0:
            style.append(("BACKGROUND", (0, idx), (-1, idx), BAND))
    table.setStyle(TableStyle(style))
    return table


def _totals(invoice: Invoice, st: dict) -> Table:
    symbol = invoice.currency_symbol
    rows = [
        [Paragraph("Subtotal", st["cell_r"]),
         Paragraph(_fmt_money(symbol, invoice.subtotal), st["cell_r"])],
    ]
    if invoice.client.tax_rate:
        pct = (invoice.client.tax_rate * 100).normalize()
        rows.append(
            [
                Paragraph(f"{invoice.client.tax_label} ({pct:f}%)", st["cell_r"]),
                Paragraph(_fmt_money(symbol, invoice.tax_amount), st["cell_r"]),
            ]
        )
    rows.append(
        [
            Paragraph(f"<b>Total Due ({invoice.currency})</b>", st["total"]),
            Paragraph(f"<b>{_fmt_money(symbol, invoice.total)}</b>", st["total"]),
        ]
    )

    table = Table(rows, colWidths=[52 * mm, 32 * mm], hAlign="RIGHT")
    table.setStyle(
        TableStyle(
            [
                ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("LINEABOVE", (0, len(rows) - 1), (-1, len(rows) - 1), 0.9, ACCENT),
                ("TOPPADDING", (0, len(rows) - 1), (-1, len(rows) - 1), 7),
            ]
        )
    )
    return table


def render_invoice(invoice: Invoice, config: Config, path: Path, page_size: str = "A4") -> Path:
    """Write one invoice PDF. Returns the path written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    st = _styles()
    pagesize = PAGE_SIZES.get(page_size.upper(), A4)

    doc = SimpleDocTemplate(
        str(path),
        pagesize=pagesize,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=22 * mm,
        title=f"Invoice {invoice.number} - {invoice.client.key}",
        author=config.company.name,
        subject=f"Invoice for {invoice.period_label}" if invoice.period_label else "Invoice",
    )

    story = [
        _header(invoice, config, st),
        Spacer(1, 14),
        _bill_to(invoice, st),
        Spacer(1, 14),
        _line_items(invoice, config, st),
        Spacer(1, 10),
        _totals(invoice, st),
    ]

    hours_note = (
        f"Total billable hours: {format_hours(invoice.total_hours, config.invoice.hours_decimals)}"
    )
    terms = f"Payment due within {invoice.terms_days} days of the invoice date."
    tail = [Spacer(1, 16), Paragraph(hours_note, st["small"]), Spacer(1, 10),
            Paragraph("<b>PAYMENT TERMS</b>", st["label"]), Spacer(1, 3),
            Paragraph(terms, st["small"])]
    if invoice.notes:
        tail += [Spacer(1, 10), Paragraph("<b>NOTES</b>", st["label"]), Spacer(1, 3),
                 Paragraph(invoice.notes, st["small"])]
    story.append(KeepTogether(tail))

    footer_text = config.invoice.footer or f"{config.company.name}  |  Invoice {invoice.number}"

    def make_canvas(*args, **kwargs):
        return _NumberedCanvas(*args, footer_text=footer_text, **kwargs)

    doc.build(story, canvasmaker=make_canvas)
    return path
