"""Tests for ApprovalLedger and human review workflow."""

import unittest
import tempfile
import sqlite3
import os
from catalog_copilot.models import (
    CatalogItem,
    LeadInquiry,
    DraftMessage,
    ApprovalStatus,
    VerificationReport,
    VerificationStatus,
)
from catalog_copilot.catalog import Catalog
from catalog_copilot.approval import ApprovalLedger


class TestApprovalLedger(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_ledger.db")
        self.ledger = ApprovalLedger(db_path=self.db_path)

        self.catalog = Catalog()
        self.catalog.add_item(CatalogItem(
            sku="PHONE-01",
            title_en="Smartphone 128GB",
            title_ar="هاتف ذكي 128 جيجا",
            price=1200.0,
            currency="SAR",
            stock_quantity=5,
            is_active=True,
        ))

        self.valid_report = VerificationReport(
            status=VerificationStatus.PASS,
            checks=[],
            reasons=[],
            requires_human_intervention=False,
        )

        self.failed_report = VerificationReport(
            status=VerificationStatus.FAIL,
            checks=[],
            reasons=["Consent missing", "Price mismatch"],
            requires_human_intervention=True,
        )

        self.uncertain_report = VerificationReport(
            status=VerificationStatus.UNCERTAIN,
            checks=[],
            reasons=["Ungrounded commercial claim"],
            requires_human_intervention=True,
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_save_draft_enforces_pending_review_invariant(self):
        """CRITICAL REGRESSION: A caller cannot insert an already APPROVED draft to bypass review."""
        draft_with_fake_approval = DraftMessage(
            draft_id="D-BYPASS",
            lead_id="L-BYPASS",
            customer_name="Bypass User",
            contact_channel="email",
            contact_target="bypass@example.com",
            sku="PHONE-01",
            language="en",
            subject="Bypass",
            body="Sneaky draft",
            verification_report=self.valid_report,
            consent_to_contact=True,
            approval_status=ApprovalStatus.APPROVED,  # Caller attempts to pre-approve!
        )
        self.ledger.save_draft(draft_with_fake_approval)

        # Must be stored as PENDING_REVIEW
        retrieved = self.ledger.get_draft("D-BYPASS")
        self.assertEqual(retrieved.approval_status, ApprovalStatus.PENDING_REVIEW)

        # Must NOT be exportable without explicit human approve() call!
        dispatchable = self.ledger.get_approved_for_dispatch()
        self.assertEqual(len(dispatchable), 0)

    def test_audit_log_immutability_triggers(self):
        """MEDIUM REGRESSION: Attempts to UPDATE or DELETE audit_log rows must be rejected by SQLite triggers."""
        draft = DraftMessage(
            draft_id="D-IMMUT",
            lead_id="L-IMMUT",
            customer_name="Audit User",
            contact_channel="email",
            contact_target="audit@example.com",
            sku="PHONE-01",
            language="en",
            subject="Test",
            body="Body",
            verification_report=self.valid_report,
            consent_to_contact=True,
        )
        self.ledger.save_draft(draft)

        conn = sqlite3.connect(self.db_path)
        try:
            # Attempt direct UPDATE on audit_log
            with self.assertRaises(sqlite3.DatabaseError) as ctx:
                conn.execute("UPDATE audit_log SET action = 'FORGED' WHERE draft_id = 'D-IMMUT'")
            self.assertIn("immutable", str(ctx.exception).lower())

            # Attempt direct DELETE on audit_log
            with self.assertRaises(sqlite3.DatabaseError) as ctx2:
                conn.execute("DELETE FROM audit_log WHERE draft_id = 'D-IMMUT'")
            self.assertIn("immutable", str(ctx2.exception).lower())
        finally:
            conn.close()

    def test_schema_migration_preserves_data(self):
        """MEDIUM REGRESSION: Schema migration adds consent columns to legacy databases safely."""
        legacy_db_path = os.path.join(self.tmp_dir.name, "legacy.db")
        conn = sqlite3.connect(legacy_db_path)
        # Create legacy table without consent columns
        conn.execute("""
            CREATE TABLE drafts (
                draft_id TEXT PRIMARY KEY,
                lead_id TEXT NOT NULL,
                customer_name TEXT,
                contact_channel TEXT NOT NULL,
                contact_target TEXT NOT NULL,
                sku TEXT,
                language TEXT NOT NULL,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                verification_status TEXT NOT NULL,
                verification_report_json TEXT NOT NULL,
                approval_status TEXT NOT NULL,
                reviewer_notes TEXT,
                reviewed_by TEXT,
                reviewed_at TEXT,
                is_dispatched INTEGER DEFAULT 0,
                dispatched_at TEXT,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("INSERT INTO drafts VALUES ('OLD-1', 'L1', 'Old Customer', 'email', 'o@ex.com', 'SKU-1', 'en', 'sub', 'body', 'PASS', '{\"status\": \"PASS\", \"checks\": []}', 'PENDING_REVIEW', NULL, NULL, NULL, 0, NULL, '2026-01-01')")
        conn.commit()
        conn.close()

        # Opening with ApprovalLedger should run schema migration
        migrated_ledger = ApprovalLedger(db_path=legacy_db_path)
        old_draft = migrated_ledger.get_draft("OLD-1")
        self.assertIsNotNone(old_draft)
        self.assertEqual(old_draft.draft_id, "OLD-1")
        self.assertFalse(old_draft.consent_to_contact)

    def test_approve_draft(self):
        draft = DraftMessage(
            draft_id="D-002",
            lead_id="L-11",
            customer_name="Tariq",
            contact_channel="chat",
            contact_target="tariq_chat",
            sku="PHONE-01",
            language="ar",
            subject="Phone inquiry",
            body="متوفر لدينا بسعر 1200.00 SAR.",
            verification_report=self.valid_report,
            consent_to_contact=True,
        )
        self.ledger.save_draft(draft)

        approved = self.ledger.approve("D-002", reviewer="Agent Sarah", notes="Verified customer")
        self.assertEqual(approved.approval_status, ApprovalStatus.APPROVED)
        self.assertEqual(approved.reviewed_by, "Agent Sarah")
        self.assertEqual(approved.reviewer_notes, "Verified customer")
        self.assertIsNotNone(approved.reviewed_at)

        db_draft = self.ledger.get_draft("D-002")
        self.assertEqual(db_draft.approval_status, ApprovalStatus.APPROVED)

    def test_cannot_approve_failed_draft(self):
        draft = DraftMessage(
            draft_id="D-003",
            lead_id="L-12",
            customer_name="Spam Lead",
            contact_channel="sms",
            contact_target="+966555555555",
            sku="PHONE-01",
            language="ar",
            subject="Spam",
            body="Hello",
            verification_report=self.failed_report,
            consent_to_contact=False,
        )
        self.ledger.save_draft(draft)

        with self.assertRaises(ValueError) as ctx:
            self.ledger.approve("D-003", reviewer="Agent Sarah")
        self.assertIn("Customer has not granted opt-in contact consent", str(ctx.exception))

    def test_cannot_approve_uncertain_draft(self):
        draft = DraftMessage(
            draft_id="D-UNCERT",
            lead_id="L-99",
            customer_name="Uncertain Lead",
            contact_channel="email",
            contact_target="uncert@example.com",
            sku="PHONE-01",
            language="en",
            subject="Warranty question",
            body="Comes with lifetime warranty",
            verification_report=self.uncertain_report,
            consent_to_contact=True,
        )
        self.ledger.save_draft(draft)

        with self.assertRaises(ValueError) as ctx:
            self.ledger.approve("D-UNCERT", reviewer="Agent Sarah")
        self.assertIn("Only verified PASS drafts may be approved", str(ctx.exception))

    def test_reject_draft(self):
        draft = DraftMessage(
            draft_id="D-004",
            lead_id="L-13",
            customer_name="Ahmed",
            contact_channel="email",
            contact_target="ahmed@example.com",
            sku="PHONE-01",
            language="en",
            subject="Special deal request",
            body="Priced at 1200.00 SAR.",
            verification_report=self.valid_report,
            consent_to_contact=True,
        )
        self.ledger.save_draft(draft)

        rejected = self.ledger.reject("D-004", reviewer="Agent Sarah", reason="Customer requested credit terms not supported")
        self.assertEqual(rejected.approval_status, ApprovalStatus.REJECTED)
        self.assertEqual(rejected.reviewer_notes, "Customer requested credit terms not supported")

        with self.assertRaises(ValueError) as ctx:
            self.ledger.approve("D-004", reviewer="Agent Sarah")
        self.assertIn("must be 'PENDING_REVIEW'", str(ctx.exception))

    def test_edit_and_approve_with_verification(self):
        draft = DraftMessage(
            draft_id="D-005",
            lead_id="L-14",
            customer_name="Layla",
            contact_channel="email",
            contact_target="layla@example.com",
            sku="PHONE-01",
            language="en",
            subject="Phone",
            body="Available now.",
            verification_report=self.valid_report,
            consent_to_contact=True,
        )
        self.ledger.save_draft(draft)

        valid_edit = "Dear Layla, Smartphone 128GB (PHONE-01) is in stock for 1200.00 SAR."
        edited = self.ledger.edit_and_approve("D-005", new_body=valid_edit, reviewer="Manager", catalog=self.catalog)
        self.assertEqual(edited.approval_status, ApprovalStatus.EDITED_AND_APPROVED)
        self.assertEqual(edited.body, valid_edit)

        draft2 = DraftMessage(
            draft_id="D-006",
            lead_id="L-15",
            customer_name="Ali",
            contact_channel="email",
            contact_target="ali@example.com",
            sku="PHONE-01",
            language="en",
            subject="Phone",
            body="Available now.",
            verification_report=self.valid_report,
            consent_to_contact=True,
        )
        self.ledger.save_draft(draft2)
        invalid_edit = "Dear Ali, Smartphone 128GB (PHONE-01) is in stock for only 300.00 SAR!"
        with self.assertRaises(ValueError) as ctx:
            self.ledger.edit_and_approve("D-006", new_body=invalid_edit, reviewer="Manager", catalog=self.catalog)
        self.assertIn("failed deterministic verification", str(ctx.exception))

    def test_edit_cannot_bypass_no_consent(self):
        draft_no_consent = DraftMessage(
            draft_id="D-NOCONSENT",
            lead_id="L-NOCONSENT",
            customer_name="No Consent User",
            contact_channel="email",
            contact_target="noconsent@example.com",
            sku="PHONE-01",
            language="en",
            subject="Cold lead",
            body="Available now.",
            verification_report=self.failed_report,
            consent_to_contact=False,
        )
        self.ledger.save_draft(draft_no_consent)

        with self.assertRaises(ValueError) as ctx:
            self.ledger.edit_and_approve(
                "D-NOCONSENT",
                new_body="Dear User, Smartphone 128GB (PHONE-01) is in stock for 1200.00 SAR.",
                reviewer="Manager",
                catalog=self.catalog,
            )
        self.assertIn("Customer has not granted opt-in contact consent", str(ctx.exception))

    def test_save_draft_cannot_overwrite_finalized_records(self):
        draft = DraftMessage(
            draft_id="D-FIN",
            lead_id="L-FIN",
            customer_name="Fin User",
            contact_channel="email",
            contact_target="fin@example.com",
            sku="PHONE-01",
            language="en",
            subject="Test",
            body="Body",
            verification_report=self.valid_report,
            consent_to_contact=True,
        )
        self.ledger.save_draft(draft)
        self.ledger.approve("D-FIN", reviewer="Supervisor")

        with self.assertRaises(ValueError) as ctx:
            self.ledger.save_draft(draft)
        self.assertIn("already finalized", str(ctx.exception))

    def test_dispatch_queue_filters_unconsented_and_unverified(self):
        draft_valid = DraftMessage(
            draft_id="D-DISP-1",
            lead_id="L-20",
            customer_name="Nasser",
            contact_channel="email",
            contact_target="n@ex.com",
            sku="PHONE-01",
            language="ar",
            subject="Subj",
            body="متوفر بسعر 1200.00 SAR",
            verification_report=self.valid_report,
            consent_to_contact=True,
        )
        self.ledger.save_draft(draft_valid)
        self.ledger.approve("D-DISP-1", reviewer="Reviewer A")

        approved_list = self.ledger.get_approved_for_dispatch()
        self.assertEqual(len(approved_list), 1)
        self.assertEqual(approved_list[0].draft_id, "D-DISP-1")

        self.ledger.mark_exported(["D-DISP-1"], batch_manifest_id="TEST-MANIFEST-1")
        self.assertEqual(len(self.ledger.get_approved_for_dispatch()), 0)


if __name__ == "__main__":
    unittest.main()
