"""Forgiving parsers for the messy values that time trackers export.

Money is Decimal everywhere. Floats are never used for currency: 0.1 + 0.2 is
not 0.3 in binary floating point, and an invoice that is a cent off is a
support call.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CENTS = Decimal("0.01")

# Accepted date layouts, tried in order. ISO first because most exports use it.
DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%b %d, %Y",
    "%d %b %Y",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
)

_HH_MM = re.compile(r"^(\d+):([0-5]\d)$")
_H_AND_M = re.compile(r"^(?:(\d+(?:\.\d+)?)\s*h)?\s*(?:(\d+(?:\.\d+)?)\s*m)?$", re.I)


def money(value: Decimal) -> Decimal:
    """Round to 2 decimal places, half-up -- the way humans expect money to round.

    Python's default is banker's rounding, which turns 0.125 into 0.12 and
    makes totals disagree with the spreadsheet the client checks them against.
    """
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


def parse_decimal(raw: str) -> Decimal | None:
    """Parse a number, tolerating currency symbols, thousands separators and
    European decimal commas. Returns None if it is not a number."""
    text = (raw or "").strip()
    if not text:
        return None
    text = re.sub(r"[^\d,.\-]", "", text)  # drop $, EUR, spaces, etc.
    if not text or text in {"-", ".", ","}:
        return None

    if "," in text and "." in text:
        # Whichever separator comes last is the decimal point.
        if text.rindex(",") > text.rindex("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        # "1,5" is a decimal comma; "1,500" is a thousands separator.
        whole, _, frac = text.rpartition(",")
        text = f"{whole}.{frac}" if len(frac) != 3 else text.replace(",", "")

    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def parse_hours(raw: str) -> Decimal | None:
    """Parse a duration as decimal hours.

    Handles the three shapes time trackers emit: 7.5, 7:30, and 7h 30m.
    """
    text = (raw or "").strip()
    if not text:
        return None

    clock = _HH_MM.match(text)
    if clock:
        hours, minutes = clock.groups()
        return Decimal(hours) + (Decimal(minutes) / Decimal(60))

    if re.search(r"[hm]", text, re.I):
        parts = _H_AND_M.match(text.replace(" ", ""))
        if parts and any(parts.groups()):
            hours, minutes = parts.groups()
            total = Decimal(hours or 0) + (Decimal(minutes or 0) / Decimal(60))
            return total

    return parse_decimal(text)


def parse_date(raw: str) -> date | None:
    """Parse a date using the common export layouts."""
    text = (raw or "").strip()
    if not text:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    # Last resort: ISO-8601 with a timezone or fractional seconds.
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def format_hours(value: Decimal, places: int = 2) -> str:
    """Trim trailing zeros so 7.50 prints as 7.5 but 7.25 keeps both digits."""
    quantized = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    text = format(quantized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"
