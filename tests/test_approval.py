"""Tests for ApprovalLedger and human review workflow."""

import unittest
import tempfile
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

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_save_and_retrieve_draft(self):
        draft = DraftMessage(
            draft_id="D-001",
            lead_id="L-10",
            customer_name="Mona",
            contact_channel="email",
            contact_target="mona@example.com",
            sku="PHONE-01",
            language="en",
            subject="Phone inquiry",
            body="Smartphone is in stock for 1200.00 SAR.",
            verification_report=self.valid_report,
        )
        self.ledger.save_draft(draft)

        retrieved = self.ledger.get_draft("D-001")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.customer_name, "Mona")
        self.assertEqual(retrieved.approval_status, ApprovalStatus.PENDING_REVIEW)

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
        )
        self.ledger.save_draft(draft)

        approved = self.ledger.approve("D-002", reviewer="Agent Sarah", notes="Verified customer")
        self.assertEqual(approved.approval_status, ApprovalStatus.APPROVED)
        self.assertEqual(approved.reviewed_by, "Agent Sarah")
        self.assertEqual(approved.reviewer_notes, "Verified customer")
        self.assertIsNotNone(approved.reviewed_at)

        # Check in DB
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
        )
        self.ledger.save_draft(draft)

        with self.assertRaises(ValueError) as ctx:
            self.ledger.approve("D-003", reviewer="Agent Sarah")
        self.assertIn("Failed commercial claim", str(ctx.exception))

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
        )
        self.ledger.save_draft(draft)

        rejected = self.ledger.reject("D-004", reviewer="Agent Sarah", reason="Customer requested credit terms not supported")
        self.assertEqual(rejected.approval_status, ApprovalStatus.REJECTED)
        self.assertEqual(rejected.reviewer_notes, "Customer requested credit terms not supported")

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
        )
        self.ledger.save_draft(draft)

        # Edit with truthful details
        valid_edit = "Dear Layla, Smartphone 128GB (PHONE-01) is in stock for 1200.00 SAR."
        edited = self.ledger.edit_and_approve("D-005", new_body=valid_edit, reviewer="Manager", catalog=self.catalog)
        self.assertEqual(edited.approval_status, ApprovalStatus.EDITED_AND_APPROVED)
        self.assertEqual(edited.body, valid_edit)

        # Edit with price hallucination -> must raise ValueError!
        invalid_edit = "Dear Layla, Smartphone 128GB (PHONE-01) is in stock for only 300.00 SAR!"
        with self.assertRaises(ValueError) as ctx:
            self.ledger.edit_and_approve("D-005", new_body=invalid_edit, reviewer="Manager", catalog=self.catalog)
        self.assertIn("failed deterministic verification", str(ctx.exception))

    def test_dispatch_queue(self):
        draft1 = DraftMessage(
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
        )
        self.ledger.save_draft(draft1)
        self.ledger.approve("D-DISP-1", reviewer="Reviewer A")

        approved_list = self.ledger.get_approved_for_dispatch()
        self.assertEqual(len(approved_list), 1)
        self.assertEqual(approved_list[0].draft_id, "D-DISP-1")

        self.ledger.mark_dispatched("D-DISP-1")
        self.assertEqual(len(self.ledger.get_approved_for_dispatch()), 0)


if __name__ == "__main__":
    unittest.main()
