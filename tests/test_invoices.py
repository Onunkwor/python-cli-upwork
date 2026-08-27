from datetime import date
from decimal import Decimal

import pytest

from invoicer.config import Client, Company, Config, InvoiceSettings
from invoicer.invoice import build_invoices
from invoicer.timesheet import Entry


def make_config(**invoice_kwargs):
    clients = {
        "Acme Corp": Client(key="Acme Corp", rate=Decimal("100"), tax_rate=Decimal("0.20")),
        "Globex": Client(key="Globex", rate=Decimal("120"), payment_terms_days=30),
    }
    return Config(
        company=Company(name="Test Co"),
        invoice=InvoiceSettings(**invoice_kwargs),
        clients=clients,
    )


def entry(config, client_key, hours, rate=None, project="Site", task="Dev", day=1):
    client = config.clients[client_key]
    return Entry(
        row=2,
        client=client,
        hours=Decimal(str(hours)),
        rate=rate if rate is not None else client.rate,
        rate_overridden=rate is not None,
        work_date=date(2026, 7, day),
        project=project,
        task=task,
        description=task,
    )


def test_one_invoice_per_client():
    config = make_config()
    entries = [entry(config, "Acme Corp", 3), entry(config, "Globex", 4)]
    invoices = build_invoices(entries, config, issue_date=date(2026, 7, 31))
    assert [i.client.key for i in invoices] == ["Acme Corp", "Globex"]


def test_hours_are_summed_into_one_line_per_group():
    config = make_config(group_by=("project", "task"))
    entries = [
        entry(config, "Acme Corp", 2, project="Site", task="Dev"),
        entry(config, "Acme Corp", 3, project="Site", task="Dev"),
        entry(config, "Acme Corp", 1, project="Site", task="QA"),
    ]
    invoice = build_invoices(entries, config, issue_date=date(2026, 7, 31))[0]
    assert len(invoice.line_items) == 2
    assert invoice.total_hours == Decimal("6")


def test_lines_at_different_rates_never_merge():
    """Merging them would print one rate beside a total computed from another."""
    config = make_config(group_by=("project",))
    entries = [
        entry(config, "Acme Corp", 2, rate=Decimal("100")),
        entry(config, "Acme Corp", 2, rate=Decimal("150")),
    ]
    invoice = build_invoices(entries, config, issue_date=date(2026, 7, 31))[0]
    assert len(invoice.line_items) == 2
    assert invoice.subtotal == Decimal("500.00")


def test_totals_and_tax():
    config = make_config()
    invoice = build_invoices(
        [entry(config, "Acme Corp", "7.5")], config, issue_date=date(2026, 7, 31)
    )[0]
    assert invoice.subtotal == Decimal("750.00")
    assert invoice.tax_amount == Decimal("150.00")
    assert invoice.total == Decimal("900.00")


def test_client_without_tax_is_not_taxed():
    config = make_config()
    invoice = build_invoices([entry(config, "Globex", 2)], config, issue_date=date(2026, 7, 31))[0]
    assert invoice.tax_amount == Decimal("0.00")
    assert invoice.total == invoice.subtotal == Decimal("240.00")


def test_rounding_happens_once_per_line_not_per_entry():
    """20 entries of 0.005h drift by a cent each if rounded individually."""
    config = make_config(group_by=("project",))
    entries = [entry(config, "Globex", "0.005") for _ in range(20)]
    invoice = build_invoices(entries, config, issue_date=date(2026, 7, 31))[0]
    assert invoice.subtotal == Decimal("12.00")


def test_per_client_payment_terms_override_the_default():
    config = make_config(payment_terms_days=14)
    invoices = build_invoices(
        [entry(config, "Acme Corp", 1), entry(config, "Globex", 1)],
        config,
        issue_date=date(2026, 7, 31),
    )
    due = {i.client.key: i.due_date for i in invoices}
    assert due["Acme Corp"] == date(2026, 8, 14)
    assert due["Globex"] == date(2026, 8, 30)


def test_invoice_numbers_are_sequential_and_formatted():
    config = make_config(number_format="{prefix}-{period}-{seq:03d}", prefix="INV")
    invoices = build_invoices(
        [entry(config, "Acme Corp", 1), entry(config, "Globex", 1)],
        config,
        issue_date=date(2026, 7, 31),
        start_number=7,
    )
    assert [i.number for i in invoices] == ["INV-2026-07-007", "INV-2026-07-008"]


def test_period_label_reads_as_a_month_when_it_is_one():
    config = make_config()
    entries = [entry(config, "Acme Corp", 1, day=1), entry(config, "Acme Corp", 1, day=31)]
    invoice = build_invoices(entries, config, issue_date=date(2026, 7, 31))[0]
    assert invoice.period_label == "July 2026"


def test_line_item_description_falls_back_when_fields_are_empty():
    config = make_config(group_by=("project", "task"))
    e = entry(config, "Acme Corp", 1, project="", task="")
    invoice = build_invoices([e], config, issue_date=date(2026, 7, 31))[0]
    assert invoice.line_items[0].description == "Professional services"
