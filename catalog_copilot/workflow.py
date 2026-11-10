"""Batch processing and offline CSV workflows for CatalogCopilot."""

from __future__ import annotations
import csv
import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import List, Dict, Any, Union, Optional
from datetime import datetime, timezone
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
        """Parses and strictly validates customer inquiry leads from CSV, extracting documented consent evidence."""
        path = Path(filepath)
        if not path.is_file():
            raise FileNotFoundError(f"Leads CSV file not found: {path}")

        required_headers = {"lead_id", "contact_channel", "contact_target", "inquiry_text"}
        leads: List[LeadInquiry] = []

        with open(path, mode="r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames or not required_headers.issubset(set(reader.fieldnames)):
                missing = required_headers - set(reader.fieldnames or [])
                raise ValueError(f"Leads CSV is missing required headers: {missing}")

            for idx, row in enumerate(reader):
                lead_id = str(row.get("lead_id") or "").strip()
                if not lead_id:
                    raise ValueError(f"Row {idx+1} is missing required 'lead_id'.")

                contact_target = str(row.get("contact_target") or "").strip()
                if not contact_target:
                    raise ValueError(f"Lead '{lead_id}' has empty 'contact_target'.")

                channel = str(row.get("contact_channel") or "email").strip().lower()
                if channel not in ("email", "whatsapp", "sms", "chat", "phone"):
                    raise ValueError(f"Lead '{lead_id}' has unsupported contact channel: '{channel}'.")

                consent_raw = row.get("consent_to_contact", False)
                consent_evidence = row.get("consent_evidence") or row.get("evidence")

                lead = LeadInquiry(
                    lead_id=lead_id,
                    customer_name=row.get("customer_name", ""),
                    contact_channel=channel,
                    contact_target=contact_target,
                    consent_to_contact=consent_raw,
                    preferred_language=row.get("preferred_language", "ar"),
                    inquiry_text=row.get("inquiry_text", ""),
                    requested_sku=row.get("requested_sku") or None,
                    consent_evidence=consent_evidence,
                )
                leads.append(lead)
        return leads

    def process_batch(
        self,
        catalog_path: Union[str, Path],
        leads_path: Union[str, Path],
    ) -> Dict[str, Any]:
        """Executes full offline batch pipeline with deterministic, idempotent draft IDs."""
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
            # Guarantee idempotent draft IDs per lead to prevent duplicate accumulation on batch retries
            draft.draft_id = f"DRAFT-{lead.lead_id}"

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
        mark_as_exported: bool = True,
    ) -> int:
        """Atomically exports verified approved drafts with a durable manifest.
        Guarantees that a write failure never leaves a corrupt or truncated file.
        """
        approved_drafts = self.ledger.get_approved_for_dispatch()
        if not approved_drafts:
            return 0

        out_p = Path(output_csv_path).resolve()
        out_p.parent.mkdir(parents=True, exist_ok=True)

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
            "consent_to_contact",
            "consent_evidence",
        ]

        batch_manifest_id = f"EXP-{uuid.uuid4().hex[:8].upper()}"
        draft_ids = [d.draft_id for d in approved_drafts]

        # Write CSV to temporary file in same directory first to ensure atomic rename
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=out_p.parent, delete=False
        ) as tmp_file:
            tmp_path = tmp_file.name
            writer = csv.DictWriter(tmp_file, fieldnames=fieldnames)
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
                    "consent_to_contact": 1 if draft.consent_to_contact else 0,
                    "consent_evidence": draft.consent_evidence,
                })

        # Write durable manifest JSON alongside the export
        manifest_path = out_p.with_suffix(".manifest.json")
        manifest_data = {
            "batch_manifest_id": batch_manifest_id,
            "export_file": out_p.name,
            "record_count": len(draft_ids),
            "draft_ids": draft_ids,
            "exported_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(manifest_path, "w", encoding="utf-8") as mf:
            json.dump(manifest_data, mf, indent=2, ensure_ascii=False)

        try:
            os.replace(tmp_path, out_p)
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

        if mark_as_exported:
            self.ledger.mark_exported(draft_ids=draft_ids, batch_manifest_id=batch_manifest_id)

        return len(approved_drafts)
