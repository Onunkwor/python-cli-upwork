"""Turn validated entries into invoices: group, total, number, date."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from .config import Client, Config
from .parsing import money
from .timesheet import Entry


@dataclass
class LineItem:
    """One printed row on the invoice: many time entries rolled into one line."""

    description: str
    hours: Decimal
    rate: Decimal
    entry_rows: list[int] = field(default_factory=list)
    first_date: date | None = None
    last_date: date | None = None

    @property
    def amount(self) -> Decimal:
        # Round once, at the line level. Rounding each entry then summing
        # accumulates a cent of drift per entry.
        return money(self.hours * self.rate)


@dataclass
class Invoice:
    client: Client
    number: str
    issue_date: date
    due_date: date
    line_items: list[LineItem]
    period_start: date | None
    period_end: date | None
    currency_symbol: str
    currency: str
    terms_days: int
    notes: str = ""

    @property
    def total_hours(self) -> Decimal:
        return sum((li.hours for li in self.line_items), Decimal("0"))

    @property
    def subtotal(self) -> Decimal:
        return money(sum((li.amount for li in self.line_items), Decimal("0")))

    @property
    def tax_amount(self) -> Decimal:
        return money(self.subtotal * self.client.tax_rate)

    @property
    def total(self) -> Decimal:
        return money(self.subtotal + self.tax_amount)

    @property
    def period_label(self) -> str:
        if not self.period_start or not self.period_end:
            return ""
        if self.period_start == self.period_end:
            return self.period_start.strftime("%d %b %Y")
        if (self.period_start.year, self.period_start.month) == (
            self.period_end.year,
            self.period_end.month,
        ):
            return self.period_start.strftime("%B %Y")
        return (
            f"{self.period_start.strftime('%d %b %Y')} - "
            f"{self.period_end.strftime('%d %b %Y')}"
        )


def _group_key(entry: Entry, fields: tuple[str, ...]) -> tuple:
    # Rate is always part of the key: two lines at different rates must never
    # merge, or the invoice would show one rate and a total from another.
    return tuple(getattr(entry, f, "") or "" for f in fields) + (entry.rate,)


def _describe(entry: Entry, fields: tuple[str, ...]) -> str:
    parts = [str(getattr(entry, f, "") or "").strip() for f in fields]
    parts = [p for p in parts if p]
    return " - ".join(parts) if parts else "Professional services"


def build_invoices(
    entries: list[Entry],
    config: Config,
    issue_date: date | None = None,
    start_number: int = 1,
) -> list[Invoice]:
    """Group entries by client and produce one Invoice per client."""
    issue_date = issue_date or date.today()
    group_fields = tuple(config.invoice.group_by)

    by_client: dict[str, list[Entry]] = {}
    for entry in entries:
        by_client.setdefault(entry.client.key, []).append(entry)

    invoices: list[Invoice] = []
    for seq, client_key in enumerate(sorted(by_client), start=start_number):
        client_entries = by_client[client_key]
        client = client_entries[0].client

        grouped: dict[tuple, LineItem] = {}
        for entry in client_entries:
            key = _group_key(entry, group_fields)
            item = grouped.get(key)
            if item is None:
                item = LineItem(
                    description=_describe(entry, group_fields),
                    hours=Decimal("0"),
                    rate=entry.rate,
                )
                grouped[key] = item
            item.hours += entry.hours
            item.entry_rows.append(entry.row)
            if entry.work_date:
                if item.first_date is None or entry.work_date < item.first_date:
                    item.first_date = entry.work_date
                if item.last_date is None or entry.work_date > item.last_date:
                    item.last_date = entry.work_date

        line_items = sorted(grouped.values(), key=lambda li: li.description.casefold())

        dates = [e.work_date for e in client_entries if e.work_date]
        period_start = min(dates) if dates else None
        period_end = max(dates) if dates else None

        period_token = (period_start or issue_date).strftime("%Y-%m")
        number = config.invoice.number_format.format(
            prefix=config.invoice.prefix,
            period=period_token,
            seq=seq,
            year=(period_start or issue_date).year,
            month=(period_start or issue_date).month,
            client=client.key,
        )

        terms_days = config.terms_days(client)
        invoices.append(
            Invoice(
                client=client,
                number=number,
                issue_date=issue_date,
                due_date=issue_date + timedelta(days=terms_days),
                line_items=line_items,
                period_start=period_start,
                period_end=period_end,
                currency_symbol=config.invoice.currency_symbol,
                currency=config.invoice.currency,
                terms_days=terms_days,
                notes=client.notes or config.invoice.notes,
            )
        )
    return invoices
