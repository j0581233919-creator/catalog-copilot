"""Adversarial regression test suite verifying fixes for Codex independent acceptance review findings."""

import unittest
import tempfile
import os
import json
import csv
from datetime import datetime, timezone, timedelta
from catalog_copilot.models import (
    CatalogItem,
    LeadInquiry,
    DraftMessage,
    ApprovalStatus,
    VerificationStatus,
    ClaimType,
)
from catalog_copilot.catalog import Catalog
from catalog_copilot.verifier import CommercialClaimVerifier
from catalog_copilot.approval import ApprovalLedger
from catalog_copilot.workflow import BatchWorkflowEngine


class TestCodexAdversarialFindings(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_adversarial.db")
        self.verifier = CommercialClaimVerifier()

        # Standard catalog with basic item
        self.catalog = Catalog()
        self.item_a = CatalogItem(
            sku="SKU-A",
            title_en="Standard Item A",
            title_ar="المنتج القياسي أ",
            price=100.0,
            currency="SAR",
            stock_quantity=10,
            is_active=True,
            discount_percent=0.0,
        )
        self.catalog.add_item(self.item_a)

        self.lead_consented = LeadInquiry(
            lead_id="LEAD-ADV-1",
            customer_name="Adversarial Tester",
            contact_channel="email",
            contact_target="tester@example.com",
            consent_to_contact=True,
            consent_evidence="Web opt-in checkbox 2026-11-09T10:00:00Z target:tester@example.com",
            preferred_language="en",
            inquiry_text="Tell me about item A",
            requested_sku="SKU-A",
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_finding_1_unsupported_specification_claim_fails_closed(self):
        """Finding 1: 'A is waterproof.' without catalog support must return UNCERTAIN, not PASS."""
        draft_text = "Hello, SKU-A is waterproof."
        report = self.verifier.verify(
            lead=self.lead_consented,
            catalog=self.catalog,
            draft_text=draft_text,
            target_sku="SKU-A",
        )
        self.assertEqual(
            report.status,
            VerificationStatus.UNCERTAIN,
            "Unsupported specification claim 'waterproof' must return UNCERTAIN.",
        )
        self.assertTrue(report.requires_human_intervention)
        ungrounded = [c for c in report.checks if c.claim_type == ClaimType.UNGROUNDED_CLAIM]
        self.assertTrue(len(ungrounded) > 0)
        self.assertTrue(any("waterproof" in c.claimed_value.lower() for c in ungrounded))

    def test_finding_1_arbitrary_unsupported_assertions_fail_closed(self):
        """Finding 1: 'contains asbestos' and 'weighs 5 kg' must return UNCERTAIN."""
        for assertion in ["SKU-A contains asbestos.", "SKU-A weighs 5 kg."]:
            report = self.verifier.verify(
                lead=self.lead_consented,
                catalog=self.catalog,
                draft_text=assertion,
                target_sku="SKU-A",
            )
            self.assertEqual(
                report.status,
                VerificationStatus.UNCERTAIN,
                f"Assertion '{assertion}' must return UNCERTAIN.",
            )
            self.assertTrue(report.requires_human_intervention)

    def test_finding_1_arabic_unsupported_specification_fails_closed(self):
        """Finding 1 (Arabic): 'مقاوم للماء' without catalog support must return UNCERTAIN."""
        arabic_lead = LeadInquiry(
            lead_id="LEAD-ADV-AR",
            customer_name="فهد",
            contact_channel="whatsapp",
            contact_target="+966501112233",
            consent_to_contact=True,
            consent_evidence="WhatsApp opt-in #1234 target:+966501112233",
            preferred_language="ar",
            inquiry_text="مواصفات المنتج أ",
            requested_sku="SKU-A",
        )
        draft_text = "مرحباً فهد، المنتج أ متوفر لدينا وهو مقاوم للماء بسعر 100 ريال."
        report = self.verifier.verify(
            lead=arabic_lead,
            catalog=self.catalog,
            draft_text=draft_text,
            target_sku="SKU-A",
        )
        self.assertEqual(report.status, VerificationStatus.UNCERTAIN)
        self.assertTrue(any(c.claim_type == ClaimType.UNGROUNDED_CLAIM for c in report.checks))

    def test_finding_2_malformed_offer_date_fails_closed(self):
        """Finding 2: Invalid date string in offer validity must fail closed (raise or return FAIL)."""
        # When creating CatalogItem with invalid date format
        with self.assertRaises(ValueError):
            CatalogItem(
                sku="SKU-BAD-DATE",
                title_en="Bad Date",
                title_ar="تاريخ غير صالح",
                price=100.0,
                currency="SAR",
                discount_percent=20.0,
                offer_valid_until="invalid",
            )

        # In verifier: if item has invalid date bypassed into catalog, verify must return FAIL
        item_bypass = CatalogItem(
            sku="SKU-BYPASS",
            title_en="Bypass Item",
            title_ar="منتج تجاوز",
            price=100.0,
            currency="SAR",
            discount_percent=20.0,
        )
        object.__setattr__(item_bypass, "offer_valid_until", "invalid")
        cat = Catalog()
        cat.add_item(item_bypass)

        report = self.verifier.verify(
            lead=self.lead_consented,
            catalog=cat,
            draft_text="SKU-BYPASS costs 80 SAR with 20% discount",
            target_sku="SKU-BYPASS",
        )
        self.assertEqual(report.status, VerificationStatus.FAIL)
        self.assertTrue(any("Invalid offer validity date format" in r for r in report.reasons))

    def test_finding_2_date_only_format_compares_cleanly_without_type_error(self):
        """Finding 2: Date-only '2020-01-01' compares with UTC datetime without TypeError and fails expired offer."""
        item_date_only = CatalogItem(
            sku="SKU-EXPIRED-DATE",
            title_en="Expired Date Only",
            title_ar="تاريخ منتهي",
            price=100.0,
            currency="SAR",
            discount_percent=20.0,
            offer_valid_until="2020-01-01",
        )
        cat = Catalog()
        cat.add_item(item_date_only)

        report = self.verifier.verify(
            lead=self.lead_consented,
            catalog=cat,
            draft_text="SKU-EXPIRED-DATE costs 80 SAR with 20% discount",
            target_sku="SKU-EXPIRED-DATE",
        )
        self.assertEqual(report.status, VerificationStatus.FAIL)
        self.assertTrue(any("Expired promotion" in r for r in report.reasons))

    def test_finding_2_future_offer_start_fails_closed(self):
        """Finding 2: Future offer_valid_from date marks promotion as FAIL before start."""
        future_date = (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()
        item_future = CatalogItem(
            sku="SKU-FUTURE",
            title_en="Future Promo",
            title_ar="عرض مستقبلي",
            price=100.0,
            currency="SAR",
            discount_percent=15.0,
            offer_valid_from=future_date,
        )
        cat = Catalog()
        cat.add_item(item_future)

        report = self.verifier.verify(
            lead=self.lead_consented,
            catalog=cat,
            draft_text="SKU-FUTURE costs 85 SAR with 15% discount",
            target_sku="SKU-FUTURE",
        )
        self.assertEqual(report.status, VerificationStatus.FAIL)
        self.assertTrue(any("Inactive promotion" in r for r in report.reasons))

    def test_finding_3_unknown_sku_does_not_raise_unbound_local_error(self):
        """Finding 3: Unknown SKU 'MISSING' with text 'Hello' returns FAIL without UnboundLocalError."""
        empty_catalog = Catalog()
        report = self.verifier.verify(
            lead=self.lead_consented,
            catalog=empty_catalog,
            draft_text="Hello",
            target_sku="MISSING",
        )
        self.assertEqual(report.status, VerificationStatus.FAIL)
        self.assertTrue(any("Invalid SKU 'MISSING'" in r for r in report.reasons))

    def test_finding_4_export_reservation_and_queue_protection(self):
        """Finding 4: Export uses durable lease reservation and protects destination files."""
        ledger = ApprovalLedger(db_path=self.db_path)
        engine = BatchWorkflowEngine(ledger=ledger)

        # Seed approved draft
        draft = DraftMessage(
            draft_id="DRAFT-RES-01",
            lead_id="LEAD-RES-01",
            customer_name="Reservation User",
            contact_channel="email",
            contact_target="res@example.com",
            sku="SKU-A",
            language="en",
            subject="Ready",
            body="Your item SKU-A is ready.",
            verification_report=self.verifier.verify(
                lead=self.lead_consented,
                catalog=self.catalog,
                draft_text="Your item SKU-A is in stock.",
                target_sku="SKU-A",
            ),
            consent_to_contact=True,
            consent_evidence="Documented consent token #9921",
        )
        ledger.save_draft(draft)
        ledger.approve("DRAFT-RES-01", reviewer="Admin Reviewer")

        out_csv = os.path.join(self.tmp_dir.name, "first_export.csv")
        count = engine.export_approved_to_csv(out_csv)
        self.assertEqual(count, 1)
        self.assertTrue(os.path.exists(out_csv))
        manifest_path = os.path.join(self.tmp_dir.name, "first_export.manifest.json")
        self.assertTrue(os.path.exists(manifest_path))

        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest_data = json.load(f)
            self.assertEqual(manifest_data["record_count"], 1)
            self.assertEqual(manifest_data["draft_ids"], ["DRAFT-RES-01"])

        # Attempting to re-export to the same existing path without overwrite must raise FileExistsError
        with self.assertRaises(FileExistsError):
            engine.export_approved_to_csv(out_csv, overwrite=False)

    def test_interrupted_export_never_duplicates_messages(self):
        """Findings 2 & 3: An interruption after CSV publication must never cause message duplication on retry or recovery."""
        ledger = ApprovalLedger(db_path=self.db_path)
        engine = BatchWorkflowEngine(ledger=ledger)

        draft = DraftMessage(
            draft_id="DRAFT-FAILSAFE-01",
            lead_id="LEAD-FAILSAFE-01",
            customer_name="Failsafe User",
            contact_channel="email",
            contact_target="failsafe@example.com",
            sku="SKU-A",
            language="en",
            subject="Ready",
            body="Your item SKU-A is ready.",
            verification_report=self.verifier.verify(
                lead=self.lead_consented,
                catalog=self.catalog,
                draft_text="Your item SKU-A is in stock.",
                target_sku="SKU-A",
            ),
            consent_to_contact=True,
            consent_evidence="Evidence #1100",
        )
        ledger.save_draft(draft)
        ledger.approve("DRAFT-FAILSAFE-01", reviewer="Admin Reviewer")

        interrupted_dir = os.path.join(self.tmp_dir.name, "interrupted_dir")
        os.makedirs(interrupted_dir, exist_ok=True)
        csv_dest = os.path.join(interrupted_dir, "batch_one.csv")

        # Simulate interruption: manually reserve and publish CSV directly, simulating crash before DB commit
        manifest_id = "EXP-TEST-CRASH"
        reserved = ledger.reserve_for_export(batch_manifest_id=manifest_id, export_destination=csv_dest)
        self.assertEqual(len(reserved), 1)

        # Write CSV on disk as if worker wrote it
        with open(csv_dest, "w", encoding="utf-8") as f:
            f.write("draft_id,lead_id\nDRAFT-FAILSAFE-01,LEAD-FAILSAFE-01\n")

        # Now simulate crash recovery:
        rec_stats = engine.recover_interrupted_exports(interrupted_dir)
        self.assertIn(manifest_id, rec_stats["recovered"], "Published CSV must be reconciled as recovered.")
        self.assertEqual(len(rec_stats["released"]), 0, "Published export must not be released back to queue.")

        # Retry to another destination must yield 0 records (no duplicates!)
        retry_csv = os.path.join(interrupted_dir, "batch_two.csv")
        retry_count = engine.export_approved_to_csv(retry_csv)
        self.assertEqual(retry_count, 0, "No drafts should be re-exported after publication interruption.")

    def test_scoped_recovery_protects_other_directories(self):
        """Finding 3: Recovery in directory A must not disrupt or release reservations in directory B."""
        ledger = ApprovalLedger(db_path=self.db_path)
        engine = BatchWorkflowEngine(ledger=ledger)

        draft = DraftMessage(
            draft_id="DRAFT-DIR-B",
            lead_id="LEAD-DIR-B",
            customer_name="Dir B User",
            contact_channel="email",
            contact_target="b@example.com",
            sku="SKU-A",
            language="en",
            subject="Ready",
            body="Your item SKU-A is ready.",
            verification_report=self.verifier.verify(
                lead=self.lead_consented,
                catalog=self.catalog,
                draft_text="Your item SKU-A is in stock.",
                target_sku="SKU-A",
            ),
            consent_to_contact=True,
            consent_evidence="Evidence #2200",
        )
        ledger.save_draft(draft)
        ledger.approve("DRAFT-DIR-B", reviewer="Admin Reviewer")

        dir_a = os.path.join(self.tmp_dir.name, "dir_a")
        dir_b = os.path.join(self.tmp_dir.name, "dir_b")
        os.makedirs(dir_a, exist_ok=True)
        os.makedirs(dir_b, exist_ok=True)

        dest_b = os.path.join(dir_b, "export_b.csv")
        token_b = "EXP-DIR-B-LIVE"
        reserved = ledger.reserve_for_export(batch_manifest_id=token_b, export_destination=dest_b)
        self.assertEqual(len(reserved), 1)

        # Recover dir_a: must not touch dir_b's reservation!
        rec_a = engine.recover_interrupted_exports(dir_a)
        self.assertEqual(len(rec_a["recovered"]), 0)
        self.assertEqual(len(rec_a["released"]), 0)

        # Confirm draft in dir_b is still safely reserved under token_b in SQLite
        import sqlite3
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT reservation_token FROM drafts WHERE draft_id = ?", ("DRAFT-DIR-B",))
            row = cursor.fetchone()
            self.assertEqual(row[0], token_b)

    def test_finding_5_missing_consent_evidence_fails_closed(self):
        """Finding 5: consent_to_contact=True without consent_evidence must fail verification."""
        lead_no_evidence = LeadInquiry(
            lead_id="LEAD-NO-EVID",
            customer_name="Fabrication Tester",
            contact_channel="email",
            contact_target="fab@example.com",
            consent_to_contact=True,
            consent_evidence="",  # Missing evidence!
            preferred_language="en",
            inquiry_text="Help",
            requested_sku="SKU-A",
        )
        # Ensure models.py did not fabricate dummy evidence
        self.assertEqual(lead_no_evidence.consent_evidence, "")

        report = self.verifier.verify(
            lead=lead_no_evidence,
            catalog=self.catalog,
            draft_text="Hello, SKU-A is in stock.",
            target_sku="SKU-A",
        )
        self.assertEqual(
            report.status,
            VerificationStatus.FAIL,
            "Lead with consent=True but missing evidence must fail verification.",
        )
        self.assertTrue(any("Missing auditable opt-in evidence" in r for r in report.reasons))


if __name__ == "__main__":
    unittest.main()
