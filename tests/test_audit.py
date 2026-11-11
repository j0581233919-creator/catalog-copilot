"""Tests for cryptographic audit export, receipt generation, and verification in CatalogCopilot."""

from __future__ import annotations
import json
import os
import tempfile
import unittest
from pathlib import Path

from catalog_copilot.models import (
    CatalogItem,
    LeadInquiry,
    DraftMessage,
    VerificationReport,
    VerificationStatus,
    ApprovalStatus,
)
from catalog_copilot.approval import ApprovalLedger
from catalog_copilot.audit import (
    AuditReceipt,
    AuditExporter,
    verify_audit_export,
    compute_canonical_draft_hash,
    compute_records_chain_hash,
)


class TestAuditExportAndVerification(unittest.TestCase):
    """Verifies tamper-evident export creation, receipt signing, and verification routines."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / "test_audit.db"
        self.ledger = ApprovalLedger(db_path=self.db_path)
        self.exporter = AuditExporter(ledger=self.ledger)

        # Create sample drafts
        self.draft1 = DraftMessage(
            draft_id="DRAFT-AUD-001",
            lead_id="LEAD-101",
            customer_name="سعيد الغامدي",
            contact_channel="whatsapp",
            contact_target="+966501112233",
            sku="TECH-PHN-01",
            language="ar",
            subject="استفسار بخصوص الهاتف",
            body="مرحباً سعيد، المنتج متوفر بسعر 3499.00 SAR.",
            verification_report=VerificationReport(
                status=VerificationStatus.PASS,
                checks=[],
            ),
            consent_to_contact=True,
            consent_evidence="Explicit checkout consent opt-in",
            approval_status=ApprovalStatus.PENDING_REVIEW,
        )
        self.ledger.save_draft(self.draft1)
        self.ledger.approve(self.draft1.draft_id, reviewer="Supervisor Sara", notes="Approved for dispatch")

        self.draft2 = DraftMessage(
            draft_id="DRAFT-AUD-002",
            lead_id="LEAD-102",
            customer_name="=cmd|' /C evil.exe'",  # Formula injection attempt in customer name!
            contact_channel="email",
            contact_target="john@example.com",
            sku="TECH-EAR-02",
            language="en",
            subject="Regarding your inquiry",
            body="Dear John, Earbuds are available for 499.00 SAR.",
            verification_report=VerificationReport(
                status=VerificationStatus.PASS,
                checks=[],
            ),
            consent_to_contact=True,
            consent_evidence="Web inbound form optin",
            approval_status=ApprovalStatus.PENDING_REVIEW,
        )
        self.ledger.save_draft(self.draft2)
        self.ledger.reject(self.draft2.draft_id, reviewer="Auditor Khalid", reason="Suspicious customer name payload")

    def test_canonical_draft_hash_determinism(self):
        h1 = compute_canonical_draft_hash(self.draft1)
        h2 = compute_canonical_draft_hash(self.draft1)
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)

        # Changing an attribute must alter the hash
        draft_copy = DraftMessage.from_dict(self.draft1.to_dict())
        draft_copy.language = "en"
        h3 = compute_canonical_draft_hash(draft_copy)
        self.assertNotEqual(h1, h3)

    def test_records_chain_hash_is_order_independent(self):
        chain1 = compute_records_chain_hash([self.draft1, self.draft2])
        chain2 = compute_records_chain_hash([self.draft2, self.draft1])
        self.assertEqual(chain1, chain2)

    def test_export_audit_csv_and_verify_success(self):
        out_csv = Path(self.temp_dir) / "audit_export.csv"
        out_p, receipt_p, receipt = self.exporter.export_audit_trail(
            output_path=out_csv,
            export_format="csv",
            exported_by="Compliance Officer",
        )

        self.assertTrue(out_p.exists())
        self.assertTrue(receipt_p.exists())
        self.assertEqual(receipt.record_count, 2)
        self.assertEqual(receipt.exported_by, "Compliance Officer")
        self.assertEqual(len(receipt.file_sha256), 64)

        # Verify against export file and database
        result = verify_audit_export(
            export_file_path=out_p,
            receipt_file_path=receipt_p,
            db_path=self.db_path,
        )
        self.assertTrue(result.is_valid, f"Verification failed with errors: {result.errors}")
        self.assertEqual(len(result.errors), 0)
        self.assertEqual(result.details["actual_record_count"], 2)

    def test_export_audit_json_and_verify_success(self):
        out_json = Path(self.temp_dir) / "audit_export.json"
        out_p, receipt_p, receipt = self.exporter.export_audit_trail(
            output_path=out_json,
            export_format="json",
            exported_by="Chief Auditor",
        )

        self.assertTrue(out_p.exists())
        self.assertTrue(receipt_p.exists())
        self.assertEqual(receipt.record_count, 2)

        result = verify_audit_export(
            export_file_path=out_p,
            receipt_file_path=receipt_p,
            db_path=self.db_path,
        )
        self.assertTrue(result.is_valid)

    def test_tampered_file_fails_verification(self):
        out_csv = Path(self.temp_dir) / "audit_tamper.csv"
        out_p, receipt_p, _ = self.exporter.export_audit_trail(
            output_path=out_csv,
            export_format="csv",
        )

        # Tamper: modify 1 byte in the export file
        with open(out_p, "a", encoding="utf-8") as f:
            f.write("\nTAMPERED_ROW,EXTRA_DATA\n")

        result = verify_audit_export(
            export_file_path=out_p,
            receipt_file_path=receipt_p,
            db_path=self.db_path,
        )
        self.assertFalse(result.is_valid)
        self.assertTrue(any("FILE INTEGRITY FAILURE" in err for err in result.errors))

    def test_tampered_receipt_hash_fails_verification(self):
        out_csv = Path(self.temp_dir) / "audit_receipt_tamper.csv"
        out_p, receipt_p, _ = self.exporter.export_audit_trail(
            output_path=out_csv,
            export_format="csv",
        )

        # Tamper receipt JSON: alter expected file SHA-256
        with open(receipt_p, "r", encoding="utf-8") as f:
            receipt_dict = json.load(f)
        receipt_dict["file_sha256"] = "0000000000000000000000000000000000000000000000000000000000000000"
        with open(receipt_p, "w", encoding="utf-8") as f:
            json.dump(receipt_dict, f, indent=2)

        result = verify_audit_export(
            export_file_path=out_p,
            receipt_file_path=receipt_p,
            db_path=self.db_path,
        )
        self.assertFalse(result.is_valid)
        self.assertTrue(any("FILE INTEGRITY FAILURE" in err for err in result.errors))

    def test_csv_injection_cell_is_sanitized_in_audit_export(self):
        out_csv = Path(self.temp_dir) / "audit_injection_check.csv"
        out_p, _, _ = self.exporter.export_audit_trail(
            output_path=out_csv,
            export_format="csv",
        )

        # Verify that '=cmd|' /C evil.exe'' was exported with leading single quote escaping
        with open(out_p, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("'=cmd|' /C evil.exe'", content)


if __name__ == "__main__":
    unittest.main()
