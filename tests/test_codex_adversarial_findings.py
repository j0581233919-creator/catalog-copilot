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

        # Write CSV on disk as if worker wrote it with full valid schema and matching batch_manifest_id
        fieldnames = [
            "batch_manifest_id", "draft_id", "lead_id", "customer_name",
            "contact_channel", "contact_target", "sku", "language", "subject",
            "body", "reviewed_by", "reviewed_at", "approval_status",
            "consent_to_contact", "consent_evidence"
        ]
        with open(csv_dest, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerow({
                "batch_manifest_id": manifest_id,
                "draft_id": "DRAFT-FAILSAFE-01",
                "lead_id": "LEAD-FAILSAFE-01",
                "customer_name": "Failsafe User",
                "contact_channel": "email",
                "contact_target": "failsafe@example.com",
                "sku": "SKU-A",
                "language": "en",
                "subject": "Ready",
                "body": "Your item SKU-A is ready.",
                "reviewed_by": reserved[0].reviewed_by or "Admin Reviewer",
                "reviewed_at": reserved[0].reviewed_at,
                "approval_status": "APPROVED",
                "consent_to_contact": "1",
                "consent_evidence": "Evidence #1100",
            })

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

    def test_codex_review3_finding1_customer_inquiry_cannot_authorize_unsupported_assertion(self):
        """Review 3 Finding 1: Inquiry text ('contains asbestos?') must NEVER authorize merchant assertion."""
        inquiry_lead = LeadInquiry(
            lead_id="LEAD-INQ-1",
            customer_name="Curious Customer",
            contact_channel="email",
            contact_target="curious@example.com",
            consent_to_contact=True,
            consent_evidence="Web form opt-in 2026-11-10",
            preferred_language="en",
            inquiry_text="Is it true SKU-A contains asbestos?",
            requested_sku="SKU-A",
        )

        # 1. Direct ungrounded assertion MUST fail-closed to UNCERTAIN
        report_unsupported = self.verifier.verify(
            lead=inquiry_lead,
            catalog=self.catalog,
            draft_text="SKU-A contains asbestos.",
            target_sku="SKU-A",
        )
        self.assertEqual(
            report_unsupported.status,
            VerificationStatus.UNCERTAIN,
            "Merchant asserting 'SKU-A contains asbestos.' based on inquiry question must be UNCERTAIN.",
        )
        self.assertTrue(report_unsupported.requires_human_intervention)

        # 2. Echoing the question inside explicit quotes with catalog-grounded answer MUST PASS
        report_quoted = self.verifier.verify(
            lead=inquiry_lead,
            catalog=self.catalog,
            draft_text="Regarding your question 'Is it true SKU-A contains asbestos?', Standard Item A is in stock for 100 SAR.",
            target_sku="SKU-A",
        )
        self.assertEqual(
            report_quoted.status,
            VerificationStatus.PASS,
            "Quoting customer inquiry with valid catalog facts must PASS.",
        )

    def test_codex_review3_finding2_foreign_file_collision_does_not_finalize_unpublished_draft(self):
        """Review 3 Finding 2: A foreign file collision at export destination must NOT mark drafts EXPORTED."""
        ledger = ApprovalLedger(db_path=self.db_path)
        engine = BatchWorkflowEngine(ledger=ledger)

        draft = DraftMessage(
            draft_id="DRAFT-COLLISION-1",
            lead_id="LEAD-COLLISION-1",
            customer_name="Collision Tester",
            contact_channel="email",
            contact_target="collision@example.com",
            sku="SKU-A",
            language="en",
            subject="Order",
            body="Standard Item A is in stock for 100 SAR.",
            verification_report=self.verifier.verify(
                lead=self.lead_consented,
                catalog=self.catalog,
                draft_text="Standard Item A is in stock for 100 SAR.",
                target_sku="SKU-A",
            ),
            consent_to_contact=True,
            consent_evidence="Opt-in #999",
        )
        ledger.save_draft(draft)
        ledger.approve("DRAFT-COLLISION-1", reviewer="Reviewer Collision")

        collision_dir = os.path.join(self.tmp_dir.name, "collision_dir")
        os.makedirs(collision_dir, exist_ok=True)
        foreign_dest = os.path.join(collision_dir, "foreign_export.csv")

        # Create a foreign file before export
        with open(foreign_dest, "w", encoding="utf-8") as f:
            f.write("foreign data that is not our export\n")

        # Attempting export without overwrite must raise FileExistsError
        with self.assertRaises(FileExistsError):
            engine.export_approved_to_csv(foreign_dest, overwrite=False)

        # Destination must still contain the foreign data unchanged
        with open(foreign_dest, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertEqual(content, "foreign data that is not our export\n")

        # The draft must NOT be marked EXPORTED, reservation must be released, and still available in queue
        db_draft = ledger.get_draft("DRAFT-COLLISION-1")
        self.assertEqual(db_draft.approval_status, ApprovalStatus.APPROVED)
        import sqlite3
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT reservation_token FROM drafts WHERE draft_id = ?", ("DRAFT-COLLISION-1",))
            row = cursor.fetchone()
            self.assertIsNone(row[0])

        approved_queue = ledger.get_approved_for_dispatch()
        self.assertEqual(len(approved_queue), 1)
        self.assertEqual(approved_queue[0].draft_id, "DRAFT-COLLISION-1")

    def test_codex_review3_finding3_unreserved_draft_cannot_be_marked_exported_by_arbitrary_token(self):
        """Review 3 Finding 3: mark_exported on an unreserved draft or token mismatch must return 0."""
        ledger = ApprovalLedger(db_path=self.db_path)

        draft = DraftMessage(
            draft_id="DRAFT-OWNERSHIP-1",
            lead_id="LEAD-OWNERSHIP-1",
            customer_name="Ownership Tester",
            contact_channel="email",
            contact_target="owner@example.com",
            sku="SKU-A",
            language="en",
            subject="Order",
            body="Standard Item A is in stock for 100 SAR.",
            verification_report=self.verifier.verify(
                lead=self.lead_consented,
                catalog=self.catalog,
                draft_text="Standard Item A is in stock for 100 SAR.",
                target_sku="SKU-A",
            ),
            consent_to_contact=True,
            consent_evidence="Opt-in #1000",
        )
        ledger.save_draft(draft)
        ledger.approve("DRAFT-OWNERSHIP-1", reviewer="Reviewer Ownership")

        # Draft is APPROVED but NOT reserved (reservation_token is NULL)
        count = ledger.mark_exported(["DRAFT-OWNERSHIP-1"], batch_manifest_id="stale")
        self.assertEqual(count, 0, "mark_exported on unreserved draft must return 0.")

        db_draft = ledger.get_draft("DRAFT-OWNERSHIP-1")
        self.assertEqual(db_draft.approval_status, ApprovalStatus.APPROVED)

        # Now reserve under legitimate token EXP-ACTIVE
        reserved = ledger.reserve_for_export(
            batch_manifest_id="EXP-ACTIVE",
            export_destination="/tmp/legit_export.csv",
        )
        self.assertEqual(len(reserved), 1)

        # Mismatched token must return 0 and NOT mark EXPORTED
        mismatch_count = ledger.mark_exported(["DRAFT-OWNERSHIP-1"], batch_manifest_id="EXP-WRONG")
        self.assertEqual(mismatch_count, 0)
        self.assertEqual(ledger.get_draft("DRAFT-OWNERSHIP-1").approval_status, ApprovalStatus.APPROVED)

        # Matching token must successfully transition to EXPORTED
        success_count = ledger.mark_exported(["DRAFT-OWNERSHIP-1"], batch_manifest_id="EXP-ACTIVE")
        self.assertEqual(success_count, 1)
        self.assertEqual(ledger.get_draft("DRAFT-OWNERSHIP-1").approval_status, ApprovalStatus.EXPORTED)

    def test_codex_review4_finding1_fully_quoted_unsupported_claim_fails_closed(self):
        """Review 4 Finding 1: Quoting an ungrounded claim like '"SKU-A contains asbestos."' must return UNCERTAIN."""
        inquiry_lead = LeadInquiry(
            lead_id="LEAD-INQ-Q",
            customer_name="Quoted Customer",
            contact_channel="email",
            contact_target="quoted@example.com",
            consent_to_contact=True,
            consent_evidence="Web form opt-in 2026-11-10",
            preferred_language="en",
            inquiry_text="Is it true SKU-A contains asbestos?",
            requested_sku="SKU-A",
        )

        # 1. Fully quoted ungrounded assertion MUST NOT bypass grounding!
        report_quoted = self.verifier.verify(
            lead=inquiry_lead,
            catalog=self.catalog,
            draft_text='"SKU-A contains asbestos."',
            target_sku="SKU-A",
        )
        self.assertEqual(
            report_quoted.status,
            VerificationStatus.UNCERTAIN,
            "Quotation marks must never bypass grounding of merchant assertions.",
        )

        # 2. Quoted text alone with no merchant response must fail closed
        report_alone = self.verifier.verify(
            lead=inquiry_lead,
            catalog=self.catalog,
            draft_text='"Is it true SKU-A contains asbestos?"',
            target_sku="SKU-A",
        )
        self.assertIn(
            report_alone.status,
            (VerificationStatus.UNCERTAIN, VerificationStatus.FAIL),
            "Quoting customer text with zero merchant response must fail closed.",
        )

    def test_codex_review4_finding2_stale_companion_manifest_collision_resolves_without_duplicate_dispatch(self):
        """Review 4 Finding 2: Pre-existing stale manifest must NOT cause published CSV to be released or duplicated."""
        ledger = ApprovalLedger(db_path=self.db_path)
        engine = BatchWorkflowEngine(ledger=ledger)

        draft = DraftMessage(
            draft_id="DRAFT-STALE-MF-1",
            lead_id="LEAD-STALE-MF-1",
            customer_name="Stale Manifest User",
            contact_channel="email",
            contact_target="stalemf@example.com",
            sku="SKU-A",
            language="en",
            subject="Ready",
            body="Standard Item A is in stock for 100 SAR.",
            verification_report=self.verifier.verify(
                lead=self.lead_consented,
                catalog=self.catalog,
                draft_text="Standard Item A is in stock for 100 SAR.",
                target_sku="SKU-A",
            ),
            consent_to_contact=True,
            consent_evidence="Opt-in #2020",
        )
        ledger.save_draft(draft)
        ledger.approve("DRAFT-STALE-MF-1", reviewer="Reviewer StaleMF")

        test_dir = os.path.join(self.tmp_dir.name, "stale_manifest_dir")
        os.makedirs(test_dir, exist_ok=True)
        csv_dest = os.path.join(test_dir, "dispatch.csv")
        manifest_dest = os.path.join(test_dir, "dispatch.manifest.json")

        # Simulate pre-existing stale companion manifest from an older run
        with open(manifest_dest, "w", encoding="utf-8") as mf:
            json.dump({"batch_manifest_id": "EXP-OLD-STALE-TOKEN", "record_count": 0}, mf)

        # Export without overwrite must detect manifest collision upfront
        with self.assertRaises(FileExistsError):
            engine.export_approved_to_csv(csv_dest, overwrite=False)

        # Now export with overwrite=True to cleanly resolve and replace the stale manifest
        count = engine.export_approved_to_csv(csv_dest, overwrite=True)
        self.assertEqual(count, 1)

        # Draft must be EXPORTED in ledger
        self.assertEqual(ledger.get_draft("DRAFT-STALE-MF-1").approval_status, ApprovalStatus.EXPORTED)

        # Manifest must now match the active batch manifest ID, not the old stale token
        with open(manifest_dest, "r", encoding="utf-8") as mf:
            mf_data = json.load(mf)
        self.assertNotEqual(mf_data["batch_manifest_id"], "EXP-OLD-STALE-TOKEN")

        # Export queue must now be empty (never duplicate)
        self.assertEqual(len(ledger.get_approved_for_dispatch()), 0)

    def test_codex_review4_finding3_accurate_audit_log_prior_status(self):
        """Review 4 Finding 3: Transitioning EDITED_AND_APPROVED must log exact previous_status in audit log."""
        ledger = ApprovalLedger(db_path=self.db_path)

        draft = DraftMessage(
            draft_id="DRAFT-AUDIT-EDIT",
            lead_id="LEAD-AUDIT-EDIT",
            customer_name="Audit Tester",
            contact_channel="email",
            contact_target="audit@example.com",
            sku="SKU-A",
            language="en",
            subject="Order",
            body="Initial Body",
            verification_report=self.verifier.verify(
                lead=self.lead_consented,
                catalog=self.catalog,
                draft_text="Standard Item A is in stock for 100 SAR.",
                target_sku="SKU-A",
            ),
            consent_to_contact=True,
            consent_evidence="Evidence #555",
        )
        ledger.save_draft(draft)
        ledger.edit_and_approve(
            draft_id="DRAFT-AUDIT-EDIT",
            new_body="Standard Item A is in stock for 100.00 SAR.",
            reviewer="Reviewer Editor",
            catalog=self.catalog,
        )

        db_draft = ledger.get_draft("DRAFT-AUDIT-EDIT")
        self.assertEqual(db_draft.approval_status, ApprovalStatus.EDITED_AND_APPROVED)

        # Reserve and mark exported
        tok = "EXP-AUDIT-TOK"
        ledger.reserve_for_export(batch_manifest_id=tok, export_destination="/tmp/audit.csv")
        updated = ledger.mark_exported(["DRAFT-AUDIT-EDIT"], batch_manifest_id=tok)
        self.assertEqual(updated, 1)

        # Inspect audit logs for exact previous_status
        audit_events = ledger.get_audit_history("DRAFT-AUDIT-EDIT")
        export_events = [e for e in audit_events if e["action"] == "EXPORTED"]
        self.assertEqual(len(export_events), 1)
        self.assertEqual(
            export_events[0]["previous_status"],
            ApprovalStatus.EDITED_AND_APPROVED.value,
            "Audit event must accurately record EDITED_AND_APPROVED as previous_status.",
        )

    def test_codex_review4_finding4_recovery_rejects_truncated_batch(self):
        """Review 4 Finding 4: Incomplete/truncated CSV missing expected drafts must NOT be recovered."""
        ledger = ApprovalLedger(db_path=self.db_path)
        engine = BatchWorkflowEngine(ledger=ledger)

        # Create two drafts
        for did in ["DRAFT-BATCH-1", "DRAFT-BATCH-2"]:
            d = DraftMessage(
                draft_id=did,
                lead_id=f"LEAD-{did}",
                customer_name="Batch User",
                contact_channel="email",
                contact_target="batch@example.com",
                sku="SKU-A",
                language="en",
                subject="Subj",
                body="Standard Item A is in stock for 100 SAR.",
                verification_report=self.verifier.verify(
                    lead=self.lead_consented,
                    catalog=self.catalog,
                    draft_text="Standard Item A is in stock for 100 SAR.",
                    target_sku="SKU-A",
                ),
                consent_to_contact=True,
                consent_evidence="Evidence #999",
            )
            ledger.save_draft(d)
            ledger.approve(did, reviewer="Admin Reviewer")

        test_dir = os.path.join(self.tmp_dir.name, "trunc_batch_dir")
        os.makedirs(test_dir, exist_ok=True)
        csv_dest = os.path.join(test_dir, "batch.csv")

        tok = "EXP-TRUNC-TOK"
        reserved = ledger.reserve_for_export(batch_manifest_id=tok, export_destination=csv_dest)
        self.assertEqual(len(reserved), 2)

        # Write truncated CSV containing ONLY DRAFT-BATCH-1 (missing DRAFT-BATCH-2)
        with open(csv_dest, "w", encoding="utf-8") as f:
            f.write(f"batch_manifest_id,draft_id,lead_id\n{tok},DRAFT-BATCH-1,LEAD-1\n")

        # Recovery must reject the truncated batch: neither finalize nor release it
        rec_stats = engine.recover_interrupted_exports(test_dir)
        self.assertEqual(len(rec_stats["recovered"]), 0, "Truncated batch must NOT be marked recovered.")
        self.assertEqual(len(rec_stats["released"]), 0, "Truncated batch must NOT be released back to queue.")

        # Both drafts must remain reserved under tok
        import sqlite3
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT draft_id, approval_status, reservation_token FROM drafts WHERE reservation_token = ?", (tok,))
            rows = cursor.fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0][1], ApprovalStatus.APPROVED.value)
            self.assertEqual(rows[1][1], ApprovalStatus.APPROVED.value)

    def test_codex_review5_finding1_attributed_assertion_fails_closed(self):
        """Review 5 Finding 1: Attributed quotation that strips interrogative meaning must return UNCERTAIN."""
        inquiry_lead = LeadInquiry(
            lead_id="LEAD-INQ-R5",
            customer_name="R5 Customer",
            contact_channel="email",
            contact_target="r5@example.com",
            consent_to_contact=True,
            consent_evidence="Web form opt-in 2026-11-10",
            preferred_language="en",
            inquiry_text="Is it true SKU-A contains asbestos?",
            requested_sku="SKU-A",
        )

        # 1. Attributed quotation turning question into an affirmative assertion MUST fail closed to UNCERTAIN
        draft_assertion = 'Regarding your question, "SKU-A contains asbestos". Standard Item A is in stock for 100 SAR.'
        report_assertion = self.verifier.verify(
            lead=inquiry_lead,
            catalog=self.catalog,
            draft_text=draft_assertion,
            target_sku="SKU-A",
        )
        self.assertEqual(
            report_assertion.status,
            VerificationStatus.UNCERTAIN,
            "Quoting customer question as an affirmative assertion without question mark must be UNCERTAIN.",
        )

        # 2. Declarative customer statement (no question mark) can NEVER be exempted as an inquiry quote
        declarative_lead = LeadInquiry(
            lead_id="LEAD-INQ-DECL",
            customer_name="Decl Customer",
            contact_channel="email",
            contact_target="decl@example.com",
            consent_to_contact=True,
            consent_evidence="Web form opt-in 2026-11-10",
            preferred_language="en",
            inquiry_text="SKU-A contains asbestos.",  # Declarative assertion!
            requested_sku="SKU-A",
        )
        report_decl = self.verifier.verify(
            lead=declarative_lead,
            catalog=self.catalog,
            draft_text='Regarding your question, "SKU-A contains asbestos." Standard Item A is in stock for 100 SAR.',
            target_sku="SKU-A",
        )
        self.assertEqual(
            report_decl.status,
            VerificationStatus.UNCERTAIN,
            "Quoting customer declarative assertion without interrogative inquiry must fail closed to UNCERTAIN.",
        )

        # 3. Preserving the customer's full question with interrogative mark MUST PASS
        draft_question = 'Regarding your question, "Is it true SKU-A contains asbestos?", Standard Item A is in stock for 100 SAR.'
        report_question = self.verifier.verify(
            lead=inquiry_lead,
            catalog=self.catalog,
            draft_text=draft_question,
            target_sku="SKU-A",
        )
        self.assertEqual(
            report_question.status,
            VerificationStatus.PASS,
            "Attributed quotation preserving customer's exact question must PASS.",
        )

    def test_codex_review5_finding2_recovery_rejects_duplicate_draft_ids_and_incomplete_schema(self):
        """Review 5 Finding 2: Recovery must reject duplicate draft IDs or incomplete schemas."""
        ledger = ApprovalLedger(db_path=self.db_path)
        engine = BatchWorkflowEngine(ledger=ledger)

        # Create two drafts
        for did in ["DRAFT-DUP-1", "DRAFT-DUP-2"]:
            d = DraftMessage(
                draft_id=did,
                lead_id=f"LEAD-{did}",
                customer_name="Dup User",
                contact_channel="email",
                contact_target="dup@example.com",
                sku="SKU-A",
                language="en",
                subject="Subj",
                body="Standard Item A is in stock for 100 SAR.",
                verification_report=self.verifier.verify(
                    lead=self.lead_consented,
                    catalog=self.catalog,
                    draft_text="Standard Item A is in stock for 100 SAR.",
                    target_sku="SKU-A",
                ),
                consent_to_contact=True,
                consent_evidence="Evidence #999",
            )
            ledger.save_draft(d)
            ledger.approve(did, reviewer="Admin Reviewer")

        test_dir = os.path.join(self.tmp_dir.name, "dup_batch_dir")
        os.makedirs(test_dir, exist_ok=True)
        csv_dest = os.path.join(test_dir, "batch.csv")

        tok = "EXP-DUP-TOK"
        reserved = ledger.reserve_for_export(batch_manifest_id=tok, export_destination=csv_dest)
        self.assertEqual(len(reserved), 2)

        # 1. Write CSV with duplicate draft ID DRAFT-DUP-1 twice and missing schema columns
        with open(csv_dest, "w", encoding="utf-8") as f:
            f.write(f"batch_manifest_id,draft_id\n{tok},DRAFT-DUP-1\n{tok},DRAFT-DUP-1\n{tok},DRAFT-DUP-2\n")

        rec_stats = engine.recover_interrupted_exports(test_dir)
        self.assertEqual(len(rec_stats["recovered"]), 0, "Duplicate draft IDs must NOT be recovered.")
        self.assertEqual(len(rec_stats["released"]), 0, "Ambiguous batch must NOT be released.")

        # 2. Write CSV with altered contact_channel (email -> sms)
        fieldnames = [
            "batch_manifest_id", "draft_id", "lead_id", "customer_name",
            "contact_channel", "contact_target", "sku", "language", "subject",
            "body", "reviewed_by", "reviewed_at", "approval_status",
            "consent_to_contact", "consent_evidence"
        ]
        with open(csv_dest, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for did in ["DRAFT-DUP-1", "DRAFT-DUP-2"]:
                w.writerow({
                    "batch_manifest_id": tok,
                    "draft_id": did,
                    "lead_id": f"LEAD-{did}",
                    "customer_name": "Dup User",
                    "contact_channel": "sms" if did == "DRAFT-DUP-1" else "email",  # Altered!
                    "contact_target": "dup@example.com",
                    "sku": "SKU-A",
                    "language": "en",
                    "subject": "Subj",
                    "body": "Standard Item A is in stock for 100 SAR.",
                    "reviewed_by": "Admin Reviewer",
                    "reviewed_at": "2026-11-10T00:00:00Z",
                    "approval_status": "APPROVED",
                    "consent_to_contact": "1",
                    "consent_evidence": "Evidence #999",
                })
        rec_altered = engine.recover_interrupted_exports(test_dir)
        self.assertEqual(len(rec_altered["recovered"]), 0, "Altered channel record must NOT be recovered.")

        # 3. Write CSV with duplicate headers
        with open(csv_dest, "w", encoding="utf-8") as f:
            f.write("batch_manifest_id,draft_id,draft_id\n" + f"{tok},DRAFT-DUP-1,DRAFT-DUP-1\n")
        rec_dup_hdr = engine.recover_interrupted_exports(test_dir)
        self.assertEqual(len(rec_dup_hdr["recovered"]), 0, "Duplicate CSV headers must NOT be recovered.")

        # 4. Write CSV with complete schema and matching records
        fieldnames = [
            "batch_manifest_id", "draft_id", "lead_id", "customer_name",
            "contact_channel", "contact_target", "sku", "language", "subject",
            "body", "reviewed_by", "reviewed_at", "approval_status",
            "consent_to_contact", "consent_evidence"
        ]
        with open(csv_dest, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            res_map = {r.draft_id: r for r in reserved}
            for did in ["DRAFT-DUP-1", "DRAFT-DUP-2"]:
                w.writerow({
                    "batch_manifest_id": tok,
                    "draft_id": did,
                    "lead_id": f"LEAD-{did}",
                    "customer_name": "Dup User",
                    "contact_channel": "email",
                    "contact_target": "dup@example.com",
                    "sku": "SKU-A",
                    "language": "en",
                    "subject": "Subj",
                    "body": "Standard Item A is in stock for 100 SAR.",
                    "reviewed_by": "Admin Reviewer",
                    "reviewed_at": res_map[did].reviewed_at,
                    "approval_status": "APPROVED",
                    "consent_to_contact": "1",
                    "consent_evidence": "Evidence #999",
                })

        # Now recovery must succeed and finalize the batch
        rec_stats2 = engine.recover_interrupted_exports(test_dir)
        self.assertEqual(len(rec_stats2["recovered"]), 1)
        self.assertIn(tok, rec_stats2["recovered"])

    def test_codex_review6_finding1_mixed_assertion_question_cannot_bypass_grounding(self):
        """Review 6 Finding 1: A question mark must not exempt an entire mixed assertion from grounding."""
        # 1. Inquiry mixes declarative assertion with question
        lead_mixed = LeadInquiry(
            lead_id="LEAD-MIXED-1",
            customer_name="Ahmed Ali",
            contact_channel="email",
            contact_target="ahmed@example.com",
            preferred_language="en",
            inquiry_text="SKU-A contains asbestos. Is it available?",
            consent_to_contact=True,
            consent_evidence="Web form checkbox #888",
        )
        # Draft quotes the mixed sentence (declarative assertion + question)
        draft_mixed = 'Regarding your question, "SKU-A contains asbestos. Is it available?", Standard Item A is in stock for 100 SAR.'
        report_mixed = self.verifier.verify(
            lead=lead_mixed,
            catalog=self.catalog,
            draft_text=draft_mixed,
            target_sku="SKU-A",
        )
        self.assertEqual(report_mixed.status, VerificationStatus.UNCERTAIN)
        self.assertTrue(any("asbestos" in str(c.claimed_value or "").lower() or "asbestos" in str(c.notes or "").lower() for c in report_mixed.checks))

        # 2. Quoting only the pure interrogative question clause passes
        draft_interrogative_only = 'Regarding your question, "Is it available?", Standard Item A is in stock for 100 SAR.'
        report_interrogative = self.verifier.verify(
            lead=lead_mixed,
            catalog=self.catalog,
            draft_text=draft_interrogative_only,
            target_sku="SKU-A",
        )
        self.assertEqual(report_interrogative.status, VerificationStatus.PASS)

        # 3. Pure interrogative question in inquiry preserving question format passes
        lead_pure_question = LeadInquiry(
            lead_id="LEAD-PURE-Q",
            customer_name="Ahmed Ali",
            contact_channel="email",
            contact_target="ahmed@example.com",
            preferred_language="en",
            inquiry_text="Is it true SKU-A contains asbestos?",
            consent_to_contact=True,
            consent_evidence="Web form checkbox #888",
        )
        draft_pure_q = 'Regarding your question, "Is it true SKU-A contains asbestos?", Standard Item A is in stock for 100 SAR.'
        report_pure_q = self.verifier.verify(
            lead=lead_pure_question,
            catalog=self.catalog,
            draft_text=draft_pure_q,
            target_sku="SKU-A",
        )
        self.assertEqual(report_pure_q.status, VerificationStatus.PASS)

        # 4. Comma-separated, conjunction-separated, slash-separated, and subordinate mixed assertions MUST return UNCERTAIN
        from dataclasses import replace
        for mixed_q in [
            'SKU-A contains asbestos, is it available?',
            'SKU-A contains asbestos and is it available?',
            'SKU-A contains asbestos؟ هل هو متوفر؟',
            'المنتج يحتوي أسبستوس، هل هو متوفر؟',
            'Is it available although SKU-A contains asbestos?',
            'Can you confirm availability since SKU-A contains asbestos?',
            'Is it available / SKU-A contains asbestos?',
            'Is it available as SKU-A contains asbestos?',
            'Is it available—SKU-A contains asbestos?',
            'Is it available -SKU-A contains asbestos?',
            'Is it available given SKU-A contains asbestos?',
            'Is it available whilst SKU-A contains asbestos?',
            'Is it available because SKU-A contains asbestos?',
            'Is it available when SKU-A contains asbestos?',
            'Is it available despite the fact SKU-A contains asbestos?',
            'Is it available ＆ SKU-A contains asbestos?',
            'هل هو متوفر لأن المنتج يحتوي أسبستوس؟',
            'هل هو متوفر مع أن المنتج يحتوي أسبستوس؟',
        ]:
            l_mix = replace(lead_mixed, inquiry_text=mixed_q)
            d_mix = f'Regarding your question, "{mixed_q}", Standard Item A is in stock for 100 SAR.'
            rep_mix = self.verifier.verify(lead=l_mix, catalog=self.catalog, draft_text=d_mix, target_sku="SKU-A")
            self.assertEqual(rep_mix.status, VerificationStatus.UNCERTAIN, f"Mixed quote '{mixed_q}' must fail closed as UNCERTAIN.")

    def test_codex_review6_finding2_recovery_rejects_extra_fields_and_altered_approval_status(self):
        """Review 6 Finding 2: Recovery must reject rows with extra fields, altered approval_status, or altered timestamps."""
        ledger = ApprovalLedger(db_path=self.db_path)
        engine = BatchWorkflowEngine(ledger=ledger)

        draft = DraftMessage(
            draft_id="DRAFT-REV6-01",
            lead_id="LEAD-REV6-01",
            customer_name="Audit Tester",
            contact_channel="email",
            contact_target="audit@example.com",
            sku="SKU-A",
            language="en",
            subject="Item Update",
            body="Standard Item A is in stock for 100 SAR.",
            verification_report=self.verifier.verify(
                lead=self.lead_consented,
                catalog=self.catalog,
                draft_text="Standard Item A is in stock for 100 SAR.",
                target_sku="SKU-A",
            ),
            consent_to_contact=True,
            consent_evidence="Evidence #Rev6",
        )
        ledger.save_draft(draft)
        ledger.approve("DRAFT-REV6-01", reviewer="Security Reviewer")

        test_dir = os.path.join(self.tmp_dir.name, "rev6_batch_dir")
        os.makedirs(test_dir, exist_ok=True)
        csv_dest = os.path.join(test_dir, "batch.csv")

        tok = "EXP-REV6-TOK"
        reserved = ledger.reserve_for_export(batch_manifest_id=tok, export_destination=csv_dest)
        self.assertEqual(len(reserved), 1)
        res_draft = reserved[0]

        valid_row_dict = {
            "batch_manifest_id": tok,
            "draft_id": res_draft.draft_id,
            "lead_id": res_draft.lead_id,
            "customer_name": res_draft.customer_name,
            "contact_channel": res_draft.contact_channel,
            "contact_target": res_draft.contact_target,
            "sku": res_draft.sku or "",
            "language": res_draft.language,
            "subject": res_draft.subject,
            "body": res_draft.body,
            "reviewed_by": res_draft.reviewed_by or "",
            "reviewed_at": res_draft.reviewed_at or "",
            "approval_status": "APPROVED",
            "consent_to_contact": "1",
            "consent_evidence": res_draft.consent_evidence,
        }

        # Sub-test A: Row has an extra appended field -> Must be rejected by recovery
        with open(csv_dest, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(list(valid_row_dict.keys()))
            writer.writerow(list(valid_row_dict.values()) + ["MALICIOUS_EXTRA_FIELD"])

        rec_extra = engine.recover_interrupted_exports(test_dir)
        self.assertEqual(len(rec_extra["recovered"]), 0, "Row with extra fields must NOT be recovered.")

        # Sub-test B: Row has approval_status changed to REJECTED -> Must be rejected by recovery
        with open(csv_dest, "w", encoding="utf-8", newline="") as f:
            altered_row = dict(valid_row_dict)
            altered_row["approval_status"] = "REJECTED"
            writer = csv.DictWriter(f, fieldnames=list(valid_row_dict.keys()))
            writer.writeheader()
            writer.writerow(altered_row)

        rec_rejected = engine.recover_interrupted_exports(test_dir)
        self.assertEqual(len(rec_rejected["recovered"]), 0, "Row with REJECTED status must NOT be recovered.")

        # Sub-test C: Row has altered reviewed_at timestamp -> Must be rejected by recovery
        with open(csv_dest, "w", encoding="utf-8", newline="") as f:
            altered_row = dict(valid_row_dict)
            altered_row["reviewed_at"] = "1999-01-01T00:00:00Z"
            writer = csv.DictWriter(f, fieldnames=list(valid_row_dict.keys()))
            writer.writeheader()
            writer.writerow(altered_row)

        rec_time = engine.recover_interrupted_exports(test_dir)
        self.assertEqual(len(rec_time["recovered"]), 0, "Row with altered reviewed_at must NOT be recovered.")

        # Sub-test C2: Row has whitespace-padded timestamp -> Must be rejected by strict byte equality
        with open(csv_dest, "w", encoding="utf-8", newline="") as f:
            altered_row = dict(valid_row_dict)
            altered_row["reviewed_at"] = f" {res_draft.reviewed_at} "
            writer = csv.DictWriter(f, fieldnames=list(valid_row_dict.keys()))
            writer.writeheader()
            writer.writerow(altered_row)

        rec_ws = engine.recover_interrupted_exports(test_dir)
        self.assertEqual(len(rec_ws["recovered"]), 0, "Row with whitespace-padded reviewed_at must NOT be recovered.")

        # Sub-test D: Valid uncorrupted row matching reserved draft exactly -> Must be recovered
        with open(csv_dest, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(valid_row_dict.keys()))
            writer.writeheader()
            writer.writerow(valid_row_dict)

        rec_valid = engine.recover_interrupted_exports(test_dir)
        self.assertEqual(len(rec_valid["recovered"]), 1)
        self.assertIn(tok, rec_valid["recovered"])

    def test_codex_review10_truth_evaluating_questions_must_not_conceal_ungrounded_assertions(self):
        """Review 10 Finding: Truth-evaluating questions must fail-closed as UNCERTAIN if they contain secondary clauses,
        conditionals, temporal connectives, or coordinate assertions; pure inquiries must return PASS."""
        from dataclasses import replace

        adversarial_probes = [
            'Is it true SKU-A contains asbestos if SKU-A is toxic?',
            'Is it true SKU-A contains asbestos after SKU-A became toxic?',
            'Is it true SKU-A contains asbestos because SKU-A is toxic?',
            'Is it true SKU-A contains asbestos although SKU-A is toxic?',
            'Is it true SKU-A contains asbestos ＆ SKU-A is toxic?',
            'Is it true SKU-A contains asbestos and SKU-A is toxic?',
            'هل صحيح أن SKU-A يحتوي اسبستوس إذا المنتج سام؟',
            'هل صحيح أن SKU-A يحتوي اسبستوس والمنتج سام؟',
            'هل صحيح أن SKU-A يحتوي اسبستوس بعد أن أصبح ساماً؟',
            'Is it true SKU-A contains asbestos? SKU-A is toxic?',
        ]

        for probe in adversarial_probes:
            lead = replace(self.lead_consented, inquiry_text=probe)
            draft = f'Regarding your question, "{probe}", Standard Item A is in stock for 100 SAR.'
            report = self.verifier.verify(lead=lead, catalog=self.catalog, draft_text=draft, target_sku="SKU-A")
            self.assertEqual(
                report.status,
                VerificationStatus.UNCERTAIN,
                f"Adversarial probe '{probe}' must fail closed as UNCERTAIN.",
            )

        pure_probes = [
            'Is it available?',
            'What is the price of SKU-A?',
            'Is it true SKU-A contains asbestos?',
            'Is it true that the product contains asbestos?',
            'Can you confirm whether SKU-A is waterproof?',
            'هل الصنف متوفر؟',
            'هل صحيح أن SKU-A يحتوي اسبستوس؟',
            'هل صحيح أن المنتج سام؟',
            'هل صحيح أن SKU-A غير أصلي؟',
        ]

        for probe in pure_probes:
            lead = replace(self.lead_consented, inquiry_text=probe)
            draft = f'Regarding your question, "{probe}", Standard Item A is in stock for 100 SAR.'
            report = self.verifier.verify(lead=lead, catalog=self.catalog, draft_text=draft, target_sku="SKU-A")
            self.assertEqual(
                report.status,
                VerificationStatus.PASS,
                f"Pure inquiry '{probe}' must PASS.",
            )


if __name__ == "__main__":
    unittest.main()
