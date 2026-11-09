"""Batch processing and offline CSV workflows for CatalogCopilot."""

from __future__ import annotations
import csv
from pathlib import Path
from typing import List, Dict, Any, Union, Optional
from .models import (
    LeadInquiry,
    DraftMessage,
    VerificationStatus,
    ApprovalStatus,
)
from .catalog import CatalogLoader, Catalog
from .generator import DraftGenerator
from .verifier import CommercialClaimVerifier
from .approval import ApprovalLedger


class BatchWorkflowEngine:
    """Executes offline batch workflows: leads ingestion, draft generation, verification, and dispatch export."""

    def __init__(self, ledger: Optional[ApprovalLedger] = None):
        self.verifier = CommercialClaimVerifier()
        self.generator = DraftGenerator(verifier=self.verifier)
        self.ledger = ledger or ApprovalLedger()

    @staticmethod
    def parse_leads_csv(filepath: Union[str, Path]) -> List[LeadInquiry]:
        """Parses and validates authorized synthetic or real customer inquiry leads from CSV."""
        path = Path(filepath)
        if not path.is_file():
            raise FileNotFoundError(f"Leads CSV file not found: {path}")

        leads: List[LeadInquiry] = []
        with open(path, mode="r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                lead = LeadInquiry(
                    lead_id=row.get("lead_id", ""),
                    customer_name=row.get("customer_name", ""),
                    contact_channel=row.get("contact_channel", "email"),
                    contact_target=row.get("contact_target", ""),
                    consent_to_contact=row.get("consent_to_contact", False),
                    preferred_language=row.get("preferred_language", "ar"),
                    inquiry_text=row.get("inquiry_text", ""),
                    requested_sku=row.get("requested_sku") or None,
                )
                leads.append(lead)
        return leads

    def process_batch(
        self,
        catalog_path: Union[str, Path],
        leads_path: Union[str, Path],
    ) -> Dict[str, Any]:
        """Executes full offline batch pipeline: Ingestion -> Generation -> Verification -> Ledger Storage."""
        cat_p = Path(catalog_path)
        if cat_p.suffix.lower() == ".json":
            catalog = CatalogLoader.load_json(cat_p)
        else:
            catalog = CatalogLoader.load_csv(cat_p)

        leads = self.parse_leads_csv(leads_path)

        results = {
            "total_leads": len(leads),
            "passed_verification": 0,
            "failed_verification": 0,
            "uncertain_verification": 0,
            "drafts_queued": 0,
            "draft_ids": [],
        }

        for lead in leads:
            draft = self.generator.generate_draft(lead=lead, catalog=catalog)
            self.ledger.save_draft(draft)
            results["draft_ids"].append(draft.draft_id)
            results["drafts_queued"] += 1

            status = draft.verification_report.status
            if status == VerificationStatus.PASS:
                results["passed_verification"] += 1
            elif status == VerificationStatus.FAIL:
                results["failed_verification"] += 1
            else:
                results["uncertain_verification"] += 1

        return results

    def export_approved_to_csv(
        self,
        output_csv_path: Union[str, Path],
        mark_as_dispatched: bool = True,
    ) -> int:
        """Exports approved drafts to a dispatch CSV for CRM or email delivery queues."""
        approved_drafts = self.ledger.get_approved_for_dispatch()
        out_p = Path(output_csv_path)

        fieldnames = [
            "draft_id",
            "lead_id",
            "customer_name",
            "contact_channel",
            "contact_target",
            "sku",
            "language",
            "subject",
            "body",
            "reviewed_by",
            "reviewed_at",
            "approval_status",
        ]

        with open(out_p, mode="w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for draft in approved_drafts:
                writer.writerow({
                    "draft_id": draft.draft_id,
                    "lead_id": draft.lead_id,
                    "customer_name": draft.customer_name,
                    "contact_channel": draft.contact_channel,
                    "contact_target": draft.contact_target,
                    "sku": draft.sku or "",
                    "language": draft.language,
                    "subject": draft.subject,
                    "body": draft.body,
                    "reviewed_by": draft.reviewed_by or "",
                    "reviewed_at": draft.reviewed_at or "",
                    "approval_status": draft.approval_status.value,
                })
                if mark_as_dispatched:
                    self.ledger.mark_dispatched(draft.draft_id)

        return len(approved_drafts)
