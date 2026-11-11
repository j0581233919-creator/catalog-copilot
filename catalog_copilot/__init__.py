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
from .tui import HumanReviewTUI, TerminalFormatter
from .chatwoot import (
    ChatwootApiClient,
    ChatwootWebhookHandler,
    MockChatwootTransport,
    create_chatwoot_server,
)
from .dialect import (
    ArabicDialect,
    QueryIntent,
    DialectalQueryAnalysis,
    parse_dialectal_inquiry,
    detect_arabic_dialect,
    clean_elongation,
    isolate_product_search_tokens,
)
from .fuzzy import (
    MatchType,
    FuzzyCandidate,
    ResolutionResult,
    FuzzyCatalogMatcher,
)

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
    "HumanReviewTUI",
    "TerminalFormatter",
    "ChatwootApiClient",
    "ChatwootWebhookHandler",
    "MockChatwootTransport",
    "create_chatwoot_server",
    "ArabicDialect",
    "QueryIntent",
    "DialectalQueryAnalysis",
    "parse_dialectal_inquiry",
    "detect_arabic_dialect",
    "clean_elongation",
    "isolate_product_search_tokens",
    "MatchType",
    "FuzzyCandidate",
    "ResolutionResult",
    "FuzzyCatalogMatcher",
]
