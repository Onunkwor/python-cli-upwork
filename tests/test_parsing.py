from datetime import date
from decimal import Decimal

import pytest

from invoicer.parsing import format_hours, money, parse_date, parse_decimal, parse_hours


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("7.5", Decimal("7.5")),
        ("7:30", Decimal("7.5")),
        ("7h 30m", Decimal("7.5")),
        ("0:15", Decimal("0.25")),
        ("2h", Decimal("2")),
        ("45m", Decimal("0.75")),
        ("1,5", Decimal("1.5")),
        ("  3  ", Decimal("3")),
    ],
)
def test_parse_hours_accepts_every_tracker_format(raw, expected):
    assert parse_hours(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "abc", "half a day", "-"])
def test_parse_hours_rejects_junk(raw):
    assert parse_hours(raw) is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("120", Decimal("120")),
        ("$120.00", Decimal("120.00")),
        ("1,500.25", Decimal("1500.25")),
        ("1.500,25", Decimal("1500.25")),
        ("1,5", Decimal("1.5")),
        ("-20", Decimal("-20")),
    ],
)
def test_parse_decimal_handles_symbols_and_separators(raw, expected):
    assert parse_decimal(raw) == expected


@pytest.mark.parametrize(
    "raw", ["2026-07-14", "2026/07/14", "14/07/2026", "Jul 14, 2026", "2026-07-14T09:00:00"]
)
def test_parse_date_formats(raw):
    assert parse_date(raw) == date(2026, 7, 14)


def test_parse_date_rejects_junk():
    assert parse_date("last tuesday") is None


def test_money_rounds_half_up_not_bankers():
    # Decimal's default ROUND_HALF_EVEN gives 0.12 here, which disagrees with
    # the spreadsheet the client reconciles against.
    assert money(Decimal("0.125")) == Decimal("0.13")
    assert money(Decimal("2.345")) == Decimal("2.35")


def test_format_hours_trims_trailing_zeros():
    assert format_hours(Decimal("7.500")) == "7.5"
    assert format_hours(Decimal("7.25")) == "7.25"
    assert format_hours(Decimal("8.000")) == "8"
