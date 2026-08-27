"""Configuration: company details, per-client rates, and CSV column mapping.

The column mapping matters more than it looks. Every time tracker names its
export columns differently ("Duration (h)" vs "Hours" vs "Time"), so the
mapping lives in config rather than in code -- pointing the tool at a new
export is a config edit, not a code change.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from .errors import ConfigError
from .parsing import parse_decimal

# Logical field -> the header names we will auto-detect if not configured.
DEFAULT_COLUMNS = {
    "date": ["date", "day", "start date", "started", "date started"],
    "client": ["client", "customer", "account", "company", "client name"],
    "project": ["project", "job", "matter", "project name"],
    "task": ["task", "activity", "category"],
    "description": ["description", "notes", "note", "comment", "details", "memo"],
    "hours": ["hours", "duration", "duration (h)", "time", "time (h)", "decimal hours"],
    "rate": ["rate", "hourly rate", "billable rate", "unit price"],
    "person": ["person", "user", "member", "employee", "staff", "who"],
}

REQUIRED_FIELDS = ("client", "hours")


@dataclass(frozen=True)
class Company:
    name: str = "Your Company"
    address_lines: tuple[str, ...] = ()
    email: str = ""
    phone: str = ""
    tax_id: str = ""
    logo: str = ""


@dataclass(frozen=True)
class Client:
    """One billable client. `key` is the canonical name printed on the invoice."""

    key: str
    rate: Decimal | None = None
    bill_to: tuple[str, ...] = ()
    email: str = ""
    tax_rate: Decimal = Decimal("0")
    tax_label: str = "Tax"
    payment_terms_days: int | None = None
    aliases: tuple[str, ...] = ()
    notes: str = ""


@dataclass(frozen=True)
class InvoiceSettings:
    prefix: str = "INV"
    number_format: str = "{prefix}-{period}-{seq:03d}"
    payment_terms_days: int = 14
    currency: str = "USD"
    currency_symbol: str = "$"
    notes: str = ""
    footer: str = ""
    group_by: tuple[str, ...] = ("project", "description")
    hours_decimals: int = 2


@dataclass
class Config:
    company: Company = field(default_factory=Company)
    invoice: InvoiceSettings = field(default_factory=InvoiceSettings)
    clients: dict[str, Client] = field(default_factory=dict)
    columns: dict[str, str] = field(default_factory=dict)
    source: Path | None = None

    def __post_init__(self) -> None:
        # Alias -> canonical key, lowercased, so "ACME Corp." finds "Acme Corp".
        self._lookup: dict[str, str] = {}
        for key, client in self.clients.items():
            self._lookup[_norm(key)] = key
            for alias in client.aliases:
                self._lookup[_norm(alias)] = key

    def find_client(self, raw_name: str) -> Client | None:
        """Resolve a CSV client value to a configured client, via alias if needed."""
        key = self._lookup.get(_norm(raw_name))
        return self.clients.get(key) if key else None

    def terms_days(self, client: Client) -> int:
        return (
            client.payment_terms_days
            if client.payment_terms_days is not None
            else self.invoice.payment_terms_days
        )


def _norm(name: str) -> str:
    """Normalise a client name for matching: case, spacing and trailing dots."""
    return " ".join((name or "").split()).strip(" .,").casefold()


def _as_decimal(value, label: str) -> Decimal:
    if isinstance(value, (int, float, str)):
        parsed = parse_decimal(str(value))
        if parsed is not None:
            return parsed
    raise ConfigError(f"{label} must be a number, got {value!r}")


def _as_tuple(value, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return tuple(value)
    raise ConfigError(f"{label} must be a string or a list of strings")


def load_config(path: Path) -> Config:
    """Read and validate the TOML config file."""
    if not path.exists():
        raise ConfigError(
            f"Config file not found: {path}\n"
            f"Create a starter one with:  invoicer init-config --from-csv <your.csv>"
        )
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Could not read {path}: {exc}") from exc

    company_raw = data.get("company", {})
    company = Company(
        name=company_raw.get("name", "Your Company"),
        address_lines=_as_tuple(company_raw.get("address_lines"), "company.address_lines"),
        email=company_raw.get("email", ""),
        phone=company_raw.get("phone", ""),
        tax_id=company_raw.get("tax_id", ""),
        logo=company_raw.get("logo", ""),
    )

    inv_raw = data.get("invoice", {})
    defaults = InvoiceSettings()
    invoice = InvoiceSettings(
        prefix=inv_raw.get("prefix", defaults.prefix),
        number_format=inv_raw.get("number_format", defaults.number_format),
        payment_terms_days=int(inv_raw.get("payment_terms_days", defaults.payment_terms_days)),
        currency=inv_raw.get("currency", defaults.currency),
        currency_symbol=inv_raw.get("currency_symbol", defaults.currency_symbol),
        notes=inv_raw.get("notes", ""),
        footer=inv_raw.get("footer", ""),
        group_by=_as_tuple(inv_raw.get("group_by"), "invoice.group_by") or defaults.group_by,
        hours_decimals=int(inv_raw.get("hours_decimals", defaults.hours_decimals)),
    )

    clients: dict[str, Client] = {}
    for key, raw in (data.get("clients") or {}).items():
        if not isinstance(raw, dict):
            raise ConfigError(
                f'clients."{key}" must be a table, for example:\n'
                f'  [clients."{key}"]\n  rate = 120.00'
            )
        rate = raw.get("rate")
        clients[key] = Client(
            key=key,
            rate=_as_decimal(rate, f'clients."{key}".rate') if rate is not None else None,
            bill_to=_as_tuple(raw.get("bill_to"), f'clients."{key}".bill_to'),
            email=raw.get("email", ""),
            tax_rate=_as_decimal(raw.get("tax_rate", 0), f'clients."{key}".tax_rate'),
            tax_label=raw.get("tax_label", "Tax"),
            payment_terms_days=raw.get("payment_terms_days"),
            aliases=_as_tuple(raw.get("aliases"), f'clients."{key}".aliases'),
            notes=raw.get("notes", ""),
        )

    columns = {str(k): str(v) for k, v in (data.get("columns") or {}).items()}
    unknown = set(columns) - set(DEFAULT_COLUMNS)
    if unknown:
        raise ConfigError(
            f"Unknown field(s) in [columns]: {', '.join(sorted(unknown))}. "
            f"Valid fields: {', '.join(sorted(DEFAULT_COLUMNS))}"
        )

    return Config(company=company, invoice=invoice, clients=clients, columns=columns, source=path)
