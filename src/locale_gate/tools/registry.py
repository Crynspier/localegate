"""Tool registry, manifests, strict canonical schemas, digest computation, and capability tokens (§5.4)."""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import re
import secrets
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from locale_gate.canonical.types import PARSER_VERSION
from locale_gate.contracts.enums import (
    BusinessApprovalStatus,
    ExpenseCategory,
    ReasonCode,
    ToolName,
)

ACTION_DIGEST_DOMAIN = "locale-gate:action-digest:v1"

# Deterministic mapping of Croatian & English category evidence quotes to ExpenseCategory
CATEGORY_EVIDENCE_MAP: dict[str, ExpenseCategory] = {
    "travel": ExpenseCategory.TRAVEL,
    "putovanje": ExpenseCategory.TRAVEL,
    "putni": ExpenseCategory.TRAVEL,
    "putni trošak": ExpenseCategory.TRAVEL,
    "putni trosak": ExpenseCategory.TRAVEL,
    "prijevoz": ExpenseCategory.TRAVEL,
    "meals": ExpenseCategory.MEALS,
    "meal": ExpenseCategory.MEALS,
    "hrana": ExpenseCategory.MEALS,
    "obrok": ExpenseCategory.MEALS,
    "ručak": ExpenseCategory.MEALS,
    "rucak": ExpenseCategory.MEALS,
    "večera": ExpenseCategory.MEALS,
    "vecera": ExpenseCategory.MEALS,
    "lodging": ExpenseCategory.LODGING,
    "hotel": ExpenseCategory.LODGING,
    "smještaj": ExpenseCategory.LODGING,
    "smjestaj": ExpenseCategory.LODGING,
    "noćenje": ExpenseCategory.LODGING,
    "nocenje": ExpenseCategory.LODGING,
    "office_supplies": ExpenseCategory.OFFICE_SUPPLIES,
    "office supplies": ExpenseCategory.OFFICE_SUPPLIES,
    "office": ExpenseCategory.OFFICE_SUPPLIES,
    "uredski materijal": ExpenseCategory.OFFICE_SUPPLIES,
    "uredski pribor": ExpenseCategory.OFFICE_SUPPLIES,
    "ured": ExpenseCategory.OFFICE_SUPPLIES,
    "software": ExpenseCategory.SOFTWARE,
    "softver": ExpenseCategory.SOFTWARE,
    "licenca": ExpenseCategory.SOFTWARE,
    "pretplata": ExpenseCategory.SOFTWARE,
    "client_entertainment": ExpenseCategory.CLIENT_ENTERTAINMENT,
    "client entertainment": ExpenseCategory.CLIENT_ENTERTAINMENT,
    "reprezentacija": ExpenseCategory.CLIENT_ENTERTAINMENT,
    "other": ExpenseCategory.OTHER,
    "ostalo": ExpenseCategory.OTHER,
}


def sanitize_display_text(raw: str, max_len: int = 240) -> str:
    """Strip control characters, outer quotation marks, and collapse whitespace."""
    cleaned = re.sub(r"[\x00-\x1f\x7f]+", " ", raw).strip()
    if len(cleaned) >= 2 and (
        (cleaned[0] == "'" and cleaned[-1] == "'")
        or (cleaned[0] == '"' and cleaned[-1] == '"')
        or (cleaned[0] == "„" and cleaned[-1] in {"“", '"'})
        or (cleaned[0] == "“" and cleaned[-1] == "”")
    ):
        cleaned = cleaned[1:-1].strip()
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len]
    return unicodedata.normalize("NFC", cleaned)


def html_safe_text(raw: str) -> str:
    """Return HTML-escaped string for safe rendering diagnostics."""
    return html.escape(sanitize_display_text(raw), quote=True)


def parse_expense_category(raw_quote: str) -> tuple[ExpenseCategory | None, ReasonCode | None]:
    """Deterministically map a category quote to the ExpenseCategory allowlist."""
    normalized = re.sub(r"\s+", " ", raw_quote.strip().lower()).strip("().")
    normalized = re.sub(
        r"^(?:under\s+category|kategorija|category)\s*:?\s*", "", normalized
    ).strip()
    if normalized in CATEGORY_EVIDENCE_MAP:
        return CATEGORY_EVIDENCE_MAP[normalized], None
    return None, ReasonCode.CATEGORY_INVALID


class SubmitExpenseCanonicalArgs(BaseModel):
    """Strict canonical arguments for `submit_expense`.

    Contract specifications (§4.1 & §4.2):
    - `amount`: Authoritative invoice grand total (final gross payable amount including applicable taxes/VAT and tips).
      Subtotals or tax components are rejected when an explicit grand total is present.
    - `category`: Optional field with schema default `None` (unclassified). In downstream business approval,
      omitted categories are mapped to `ExpenseCategory.OTHER`. Models adhering to prompt rules prohibiting
      invention of unstated facts safely omit this field.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    amount: str = Field(pattern=r"^\d+(?:\.\d{1,3})?$")
    currency: str = Field(min_length=3, max_length=3)
    expense_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    business_approval_status: BusinessApprovalStatus = Field(
        default=BusinessApprovalStatus.PENDING_MANAGER_APPROVAL
    )
    description: str | None = Field(default=None, max_length=240)
    category: ExpenseCategory | None = Field(
        default=None,
        description="Optional expense category; defaults to None (unclassified / mapped to 'other' in routing).",
    )

    @field_validator("amount")
    @classmethod
    def validate_positive_amount(cls, v: str) -> str:
        dec = Decimal(v)
        if dec <= Decimal("0"):
            raise ValueError("Expense amount must be strictly positive")
        return v

    def to_canonical_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "amount": self.amount,
            "currency": self.currency,
            "expense_date": self.expense_date,
            "business_approval_status": self.business_approval_status.value,
        }
        if self.description is not None:
            out["description"] = self.description
        if self.category is not None:
            out["category"] = self.category.value
        return out


class ScheduleEventCanonicalArgs(BaseModel):
    """Strict canonical arguments for `schedule_event`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = Field(min_length=1, max_length=160)
    start_datetime: str
    end_datetime: str
    timezone: str = Field(min_length=1, max_length=64)
    duration_minutes: int | None = Field(default=None, gt=0, le=10080)

    @model_validator(mode="after")
    def validate_temporal_order(self) -> ScheduleEventCanonicalArgs:
        start_dt = datetime.fromisoformat(self.start_datetime)
        end_dt = datetime.fromisoformat(self.end_datetime)
        if end_dt <= start_dt:
            raise ValueError("Event end_datetime must be strictly after start_datetime")
        return self

    def to_canonical_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "title": self.title,
            "start_datetime": self.start_datetime,
            "end_datetime": self.end_datetime,
            "timezone": self.timezone,
        }
        if self.duration_minutes is not None:
            out["duration_minutes"] = self.duration_minutes
        return out


@dataclass(frozen=True)
class ToolManifest:
    """Declarative metadata and field policy for an allowlisted tool."""

    name: ToolName
    description: str
    required_fields: tuple[str, ...]
    optional_fields: tuple[str, ...]
    requires_user_confirmation: bool = True

    @property
    def all_allowed_fields(self) -> frozenset[str]:
        return frozenset(self.required_fields) | frozenset(self.optional_fields)


SUBMIT_EXPENSE_MANIFEST = ToolManifest(
    name=ToolName.SUBMIT_EXPENSE,
    description=(
        "Create a simulated expense record and place it in pending_manager_approval state. "
        "The 'amount' field represents the invoice grand total (gross amount including taxes/fees). "
        "Supply exact verbatim quotations for amount, expense_date, and optional currency, "
        "description, or category (which defaults to unclassified/other if omitted)."
    ),
    required_fields=("amount", "expense_date"),
    optional_fields=("currency", "description", "category"),
    requires_user_confirmation=True,
)

SCHEDULE_EVENT_MANIFEST = ToolManifest(
    name=ToolName.SCHEDULE_EVENT,
    description=(
        "Schedule a simulated calendar event. Supply exact verbatim quotations for title, "
        "start_datetime, and either end_datetime or duration."
    ),
    required_fields=("title", "start_datetime"),
    optional_fields=("end_datetime", "duration"),
    requires_user_confirmation=True,
)

TOOL_REGISTRY: dict[str, ToolManifest] = {
    ToolName.SUBMIT_EXPENSE.value: SUBMIT_EXPENSE_MANIFEST,
    ToolName.SCHEDULE_EVENT.value: SCHEDULE_EVENT_MANIFEST,
}


def get_tool_manifest(tool_name: str | None) -> ToolManifest | None:
    """Return the ToolManifest for a registered tool name, or None."""
    if not tool_name:
        return None
    return TOOL_REGISTRY.get(tool_name)


def compute_canonical_action_digest(
    request_id: str,
    tool_name: str,
    canonical_arguments: dict[str, Any],
    *,
    locale: str | None = None,
    timezone: str | None = None,
    policy_profile: str = "research_simulation_v1",
    parser_version: str = PARSER_VERSION,
) -> str:
    """Compute a domain-separated deterministic SHA-256 digest over the canonical action (§5.4)."""
    payload: dict[str, Any] = {
        "domain": ACTION_DIGEST_DOMAIN,
        "request_id": request_id,
        "tool_name": tool_name,
        "canonical_arguments": canonical_arguments,
        "policy_profile": policy_profile,
        "parser_version": parser_version,
    }
    if locale is not None:
        payload["locale"] = locale
    if timezone is not None:
        payload["timezone"] = timezone
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def generate_confirmation_capability_token() -> tuple[str, str]:
    """Generate a cryptographically random capability token and its SHA-256 storage hash (§5.4)."""
    raw_token = f"lgc_{secrets.token_urlsafe(24)}"
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    return raw_token, token_hash


def verify_confirmation_capability_token(raw_token: str, stored_token_hash: str) -> bool:
    """Verify a candidate confirmation token against its stored SHA-256 hash in constant time."""
    candidate_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    return hmac.compare_digest(candidate_hash, stored_token_hash)


def openai_compatible_tool_schemas(
    allowed_tools: tuple[str, ...] = ("submit_expense", "schedule_event"),
) -> list[dict[str, Any]]:
    """Build OpenAI-compatible function-calling schemas requiring exact EvidenceQuote objects."""
    evidence_prop = {
        "type": "object",
        "additionalProperties": False,
        "required": ["quote"],
        "properties": {
            "quote": {
                "type": "string",
                "description": "Exact verbatim substring copied from the user request.",
            },
            "context_excerpt": {
                "type": ["string", "null"],
                "description": "Optional verbatim surrounding substring when quote appears multiple times.",
            },
        },
    }

    tools: list[dict[str, Any]] = []
    if "submit_expense" in allowed_tools:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": "submit_expense",
                    "description": SUBMIT_EXPENSE_MANIFEST.description,
                    "parameters": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["amount", "expense_date"],
                        "properties": {
                            "amount": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["evidence"],
                                "properties": {"evidence": evidence_prop},
                            },
                            "currency": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["evidence"],
                                "properties": {"evidence": evidence_prop},
                            },
                            "expense_date": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["evidence"],
                                "properties": {"evidence": evidence_prop},
                            },
                            "description": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["evidence"],
                                "properties": {"evidence": evidence_prop},
                            },
                            "category": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["evidence"],
                                "properties": {"evidence": evidence_prop},
                            },
                        },
                    },
                },
            }
        )
    if "schedule_event" in allowed_tools:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": "schedule_event",
                    "description": SCHEDULE_EVENT_MANIFEST.description,
                    "parameters": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["title", "start_datetime"],
                        "properties": {
                            "title": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["evidence"],
                                "properties": {"evidence": evidence_prop},
                            },
                            "start_datetime": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["evidence"],
                                "properties": {"evidence": evidence_prop},
                            },
                            "end_datetime": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["evidence"],
                                "properties": {"evidence": evidence_prop},
                            },
                            "duration": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["evidence"],
                                "properties": {"evidence": evidence_prop},
                            },
                        },
                    },
                },
            }
        )
    return tools
