"""Data models for CatalogCopilot."""

from __future__ import annotations
import math
import re
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone


class ClaimType(str, Enum):
    CONSENT = "CONSENT"
    SKU_EXISTENCE = "SKU_EXISTENCE"
    PRICE = "PRICE"
    STOCK_AVAILABILITY = "STOCK_AVAILABILITY"
    DISCOUNT_OFFER = "DISCOUNT_OFFER"
    CURRENCY = "CURRENCY"
    UNGROUNDED_CLAIM = "UNGROUNDED_CLAIM"


class VerificationStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNCERTAIN = "UNCERTAIN"


class ApprovalStatus(str, Enum):
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EDITED_AND_APPROVED = "EDITED_AND_APPROVED"
    EXPORTED = "EXPORTED"
    DISPATCHED = "DISPATCHED"


@dataclass
class CatalogItem:
    sku: str
    title_en: str
    title_ar: str
    price: float
    currency: str = "SAR"
    stock_quantity: int = 0
    is_active: bool = True
    discount_percent: float = 0.0
    discount_price: Optional[float] = None
    offer_description_en: Optional[str] = None
    offer_description_ar: Optional[str] = None
    offer_valid_from: Optional[str] = None   # ISO timestamp string or YYYY-MM-DD
    offer_valid_until: Optional[str] = None  # ISO timestamp string or YYYY-MM-DD
    category: str = ""

    def __post_init__(self):
        self.sku = str(self.sku).strip().upper()
        self.price = float(self.price)
        if math.isnan(self.price) or math.isinf(self.price):
            raise ValueError(f"SKU '{self.sku}' has non-finite price: {self.price}")

        self.currency = str(self.currency).strip().upper()
        self.stock_quantity = int(self.stock_quantity)
        self.discount_percent = float(self.discount_percent or 0.0)
        if math.isnan(self.discount_percent) or math.isinf(self.discount_percent):
            raise ValueError(f"SKU '{self.sku}' has non-finite discount percent: {self.discount_percent}")

        if self.discount_price is not None:
            self.discount_price = float(self.discount_price)
            if math.isnan(self.discount_price) or math.isinf(self.discount_price):
                raise ValueError(f"SKU '{self.sku}' has non-finite discount price: {self.discount_price}")

        if self.offer_valid_from:
            self.offer_valid_from = self._validate_and_normalize_date(self.offer_valid_from, "offer_valid_from")
        if self.offer_valid_until:
            self.offer_valid_until = self._validate_and_normalize_date(self.offer_valid_until, "offer_valid_until")

    def _validate_and_normalize_date(self, val: str, field_name: str) -> str:
        s = str(val).strip()
        if not s:
            return ""
        try:
            if len(s) == 10 and s.count("-") == 2:
                dt = datetime.fromisoformat(s)
            else:
                dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except Exception as e:
            raise ValueError(
                f"SKU '{self.sku}' has invalid {field_name} format: '{val}'. Expected valid ISO date or timestamp."
            ) from e

    @property
    def effective_price(self) -> float:
        """Returns effective selling price after applying explicit discount price or discount percentage."""
        if self.discount_price is not None and self.discount_price >= 0:
            return round(self.discount_price, 2)
        if self.discount_percent > 0:
            return round(self.price * (1.0 - (self.discount_percent / 100.0)), 2)
        return round(self.price, 2)

    @property
    def in_stock(self) -> bool:
        """True if active and positive physical inventory exists."""
        return self.is_active and self.stock_quantity > 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class LeadInquiry:
    lead_id: str
    customer_name: str
    contact_channel: str
    contact_target: str
    consent_to_contact: bool
    preferred_language: str
    inquiry_text: str
    requested_sku: Optional[str] = None
    consent_evidence: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self):
        self.lead_id = str(self.lead_id).strip()
        if not self.lead_id:
            raise ValueError("Lead record is missing required 'lead_id'.")

        self.customer_name = str(self.customer_name).strip()
        self.contact_channel = str(self.contact_channel).strip().lower()
        if self.contact_channel not in ("email", "whatsapp", "sms", "chat", "phone"):
            raise ValueError(f"Unsupported contact channel: '{self.contact_channel}'.")

        self.contact_target = str(self.contact_target).strip()
        if not self.contact_target:
            raise ValueError(f"Lead '{self.lead_id}' has empty contact target.")

        # Validate channel-specific contact format
        if self.contact_channel == "email":
            if "@" not in self.contact_target or "." not in self.contact_target.split("@")[-1]:
                raise ValueError(f"Lead '{self.lead_id}' has invalid email target: '{self.contact_target}'.")
        elif self.contact_channel in ("whatsapp", "sms", "phone"):
            digits = re.sub(r"\D", "", self.contact_target)
            if len(digits) < 7:
                raise ValueError(f"Lead '{self.lead_id}' has invalid phone/number target: '{self.contact_target}'.")

        # Strict documented boolean parsing for consent
        if isinstance(self.consent_to_contact, bool):
            pass
        elif isinstance(self.consent_to_contact, str):
            norm = self.consent_to_contact.strip().lower()
            if norm in ("true", "1", "yes", "y"):
                self.consent_to_contact = True
            elif norm in ("false", "0", "no", "n", ""):
                self.consent_to_contact = False
            else:
                raise ValueError(f"Invalid consent value '{self.consent_to_contact}'. Must be an explicit documented boolean.")
        elif isinstance(self.consent_to_contact, (int, float)) and self.consent_to_contact in (0, 1):
            self.consent_to_contact = bool(self.consent_to_contact)
        else:
            raise ValueError(f"Invalid consent type {type(self.consent_to_contact)}. Must be an explicit documented boolean.")

        # Documented consent evidence: preserve actual provenance without fabricating proof
        self.consent_evidence = str(self.consent_evidence or "").strip()

        lang = str(self.preferred_language).strip().lower()
        self.preferred_language = "ar" if lang in ("ar", "arabic", "ar-sa", "ar-eg", "عربي") else "en"
        if self.requested_sku:
            self.requested_sku = str(self.requested_sku).strip().upper()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ClaimVerification:
    claim_type: ClaimType
    claimed_value: Any
    catalog_value: Any
    is_valid: bool
    notes: str

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["claim_type"] = self.claim_type.value
        return data


@dataclass
class VerificationReport:
    status: VerificationStatus
    checks: List[ClaimVerification] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    requires_human_intervention: bool = True
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "checks": [c.to_dict() for c in self.checks],
            "reasons": self.reasons,
            "requires_human_intervention": self.requires_human_intervention,
            "generated_at": self.generated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> VerificationReport:
        checks = [
            ClaimVerification(
                claim_type=ClaimType(c["claim_type"]),
                claimed_value=c["claimed_value"],
                catalog_value=c["catalog_value"],
                is_valid=c["is_valid"],
                notes=c.get("notes", ""),
            )
            for c in data.get("checks", [])
        ]
        return cls(
            status=VerificationStatus(data["status"]),
            checks=checks,
            reasons=data.get("reasons", []),
            requires_human_intervention=data.get("requires_human_intervention", True),
            generated_at=data.get("generated_at", ""),
        )


@dataclass
class DraftMessage:
    draft_id: str
    lead_id: str
    customer_name: str
    contact_channel: str
    contact_target: str
    sku: Optional[str]
    language: str
    subject: str
    body: str
    verification_report: VerificationReport
    consent_to_contact: bool = False
    consent_evidence: str = ""
    approval_status: ApprovalStatus = ApprovalStatus.PENDING_REVIEW
    reviewer_notes: Optional[str] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    inquiry_text: str = ""
    detected_dialect: Optional[str] = None
    query_intents: List[str] = field(default_factory=list)
    fuzzy_match_notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "draft_id": self.draft_id,
            "lead_id": self.lead_id,
            "customer_name": self.customer_name,
            "contact_channel": self.contact_channel,
            "contact_target": self.contact_target,
            "sku": self.sku,
            "language": self.language,
            "subject": self.subject,
            "body": self.body,
            "verification_report": self.verification_report.to_dict(),
            "consent_to_contact": self.consent_to_contact,
            "consent_evidence": self.consent_evidence,
            "approval_status": self.approval_status.value,
            "reviewer_notes": self.reviewer_notes,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": self.reviewed_at,
            "created_at": self.created_at,
            "inquiry_text": self.inquiry_text,
            "detected_dialect": self.detected_dialect,
            "query_intents": self.query_intents,
            "fuzzy_match_notes": self.fuzzy_match_notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DraftMessage:
        return cls(
            draft_id=data["draft_id"],
            lead_id=data["lead_id"],
            customer_name=data["customer_name"],
            contact_channel=data["contact_channel"],
            contact_target=data["contact_target"],
            sku=data.get("sku"),
            language=data["language"],
            subject=data["subject"],
            body=data["body"],
            verification_report=VerificationReport.from_dict(data["verification_report"]),
            consent_to_contact=bool(data.get("consent_to_contact", False)),
            consent_evidence=data.get("consent_evidence", ""),
            approval_status=ApprovalStatus(data.get("approval_status", ApprovalStatus.PENDING_REVIEW.value)),
            reviewer_notes=data.get("reviewer_notes"),
            reviewed_by=data.get("reviewed_by"),
            reviewed_at=data.get("reviewed_at"),
            created_at=data.get("created_at", ""),
            inquiry_text=data.get("inquiry_text", ""),
            detected_dialect=data.get("detected_dialect"),
            query_intents=data.get("query_intents") or [],
            fuzzy_match_notes=data.get("fuzzy_match_notes"),
        )
