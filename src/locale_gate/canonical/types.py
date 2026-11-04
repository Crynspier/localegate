"""Canonical value representations produced exclusively by deterministic parsers."""

from __future__ import annotations

import importlib.metadata
import zoneinfo
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

PARSER_VERSION = "1.1.0"


def get_tzdb_provenance() -> dict[str, Any]:
    """Return deterministic timezone database provenance (`tzdata` version and `zoneinfo.TZPATH`)."""
    try:
        tzdata_ver = importlib.metadata.version("tzdata")
    except importlib.metadata.PackageNotFoundError:
        tzdata_ver = "system_tzdb"
    return {
        "tzdata_package_version": tzdata_ver,
        "tzpath": list(zoneinfo.TZPATH),
        "source": "iana_tzdata_python_zoneinfo",
    }


def get_tzdb_version_string() -> str:
    """Return concise tzdata version string for CanonicalDateTime provenance."""
    try:
        return f"tzdata-{importlib.metadata.version('tzdata')}"
    except importlib.metadata.PackageNotFoundError:
        return "system_tzdb"


@dataclass(frozen=True)
class CanonicalMoney:
    """Verified, fixed-point financial value. Never serialized as float."""

    amount: Decimal
    amount_str: str
    currency: str | None
    minor_units: int
    original_quote: str
    locale: str
    rule_id: str
    parser_version: str = PARSER_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "amount": self.amount_str,
            "currency": self.currency,
            "minor_units": self.minor_units,
            "original_quote": self.original_quote,
            "locale": self.locale,
            "rule_id": self.rule_id,
            "parser_version": self.parser_version,
        }


@dataclass(frozen=True)
class CanonicalDate:
    """Verified calendar date in ISO 8601 (YYYY-MM-DD) format."""

    value: date
    iso_date: str
    original_quote: str
    locale: str
    rule_id: str
    parser_version: str = PARSER_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "iso_date": self.iso_date,
            "original_quote": self.original_quote,
            "locale": self.locale,
            "rule_id": self.rule_id,
            "parser_version": self.parser_version,
        }


@dataclass(frozen=True)
class CanonicalDateTime:
    """Verified timezone-aware datetime with RFC 3339 offset, IANA zone, tzdb provenance, and DST fold."""

    value: datetime
    rfc3339: str
    utc_rfc3339: str
    iana_timezone: str
    fold: int
    local_wall_iso: str
    original_quote: str
    locale: str
    rule_id: str
    parser_version: str = PARSER_VERSION
    tzdb_version: str = get_tzdb_version_string()

    def to_dict(self) -> dict[str, Any]:
        return {
            "rfc3339": self.rfc3339,
            "utc_rfc3339": self.utc_rfc3339,
            "iana_timezone": self.iana_timezone,
            "fold": self.fold,
            "local_wall_iso": self.local_wall_iso,
            "original_quote": self.original_quote,
            "locale": self.locale,
            "rule_id": self.rule_id,
            "parser_version": self.parser_version,
            "tzdb_version": self.tzdb_version,
        }


@dataclass(frozen=True)
class CanonicalDuration:
    """Verified positive duration in minutes and ISO 8601 duration string."""

    minutes: int
    iso_duration: str
    original_quote: str
    locale: str
    rule_id: str
    parser_version: str = PARSER_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "minutes": self.minutes,
            "iso_duration": self.iso_duration,
            "original_quote": self.original_quote,
            "locale": self.locale,
            "rule_id": self.rule_id,
            "parser_version": self.parser_version,
        }
