"""CatalogCopilot — Catalog-grounded Arabic/English sales assistant with commercial claim verification."""

__version__ = "0.1.0"

from .models import (
    CatalogItem,
    LeadInquiry,
    ClaimType,
    ClaimVerification,
    VerificationStatus,
    VerificationReport,
    ApprovalStatus,
    DraftMessage,
)
from .catalog import CatalogLoader, Catalog
from .verifier import CommercialClaimVerifier
from .generator import DraftGenerator
from .approval import ApprovalLedger
from .workflow import BatchWorkflowEngine

__all__ = [
    "CatalogItem",
    "LeadInquiry",
    "ClaimType",
    "ClaimVerification",
    "VerificationStatus",
    "VerificationReport",
    "ApprovalStatus",
    "DraftMessage",
    "CatalogLoader",
    "Catalog",
    "CommercialClaimVerifier",
    "DraftGenerator",
    "ApprovalLedger",
    "BatchWorkflowEngine",
]
