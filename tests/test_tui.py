"""Comprehensive behavioral tests for Human Review Terminal UI (CC-002)."""

import io
import os
import tempfile
import unittest

from catalog_copilot.models import (
    CatalogItem,
    LeadInquiry,
    DraftMessage,
    ApprovalStatus,
    VerificationStatus,
    VerificationReport,
    ClaimVerification,
    ClaimType,
)
from catalog_copilot.catalog import Catalog
from catalog_copilot.approval import ApprovalLedger
from catalog_copilot.tui import HumanReviewTUI, TerminalFormatter
from catalog_copilot.cli import main


class TestTerminalFormatter(unittest.TestCase):

    def test_colors_disabled(self):
        fmt = TerminalFormatter(use_color=False)
        self.assertEqual(fmt.bold("test"), "test")
        self.assertEqual(fmt.green("pass"), "pass")
        self.assertEqual(fmt.red("fail"), "fail")
        self.assertEqual(fmt.yellow("warn"), "warn")
        self.assertEqual(fmt.badge(VerificationStatus.PASS), "[PASS]")
        self.assertEqual(fmt.badge(VerificationStatus.FAIL), "[FAIL]")
        self.assertEqual(fmt.badge(VerificationStatus.UNCERTAIN), "[UNCERTAIN]")

    def test_colors_enabled(self):
        fmt = TerminalFormatter(use_color=True)
        self.assertIn("\033[32m", fmt.green("pass"))
        self.assertIn("\033[31m", fmt.red("fail"))
        self.assertIn("\033[1m", fmt.bold("bold"))
        self.assertIn("\033[32m", fmt.badge(VerificationStatus.PASS))
        self.assertIn("\033[31m", fmt.badge(VerificationStatus.FAIL))
        self.assertIn("\033[33m", fmt.badge(VerificationStatus.UNCERTAIN))


class TestHumanReviewTUI(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_tui.db")
        self.ledger = ApprovalLedger(db_path=self.db_path)

        self.catalog = Catalog()
        self.catalog.add_item(CatalogItem(
            sku="COFFEE-ESP-01",
            title_en="Italian Espresso Beans 1kg",
            title_ar="بن اسبريسو إيطالي فاخر 1 كجم",
            price=120.0,
            currency="SAR",
            stock_quantity=45,
            is_active=True,
        ))
        self.catalog.add_item(CatalogItem(
            sku="PHONE-128",
            title_en="Smartphone 128GB",
            title_ar="هاتف ذكي 128 جيجا",
            price=1200.0,
            currency="SAR",
            stock_quantity=10,
            is_active=True,
        ))

        # Sample valid draft
        self.valid_report = VerificationReport(
            status=VerificationStatus.PASS,
            checks=[
                ClaimVerification(claim_type=ClaimType.CONSENT, claimed_value=True, catalog_value=True, is_valid=True, notes="Opt-in consent verified."),
                ClaimVerification(claim_type=ClaimType.SKU_EXISTENCE, claimed_value="COFFEE-ESP-01", catalog_value="COFFEE-ESP-01", is_valid=True, notes="SKU found."),
                ClaimVerification(claim_type=ClaimType.PRICE, claimed_value=120.0, catalog_value=120.0, is_valid=True, notes="Price matches."),
            ],
            reasons=[],
            requires_human_intervention=False,
        )
        self.draft_valid = DraftMessage(
            draft_id="draft_tui_001",
            lead_id="lead_tui_001",
            customer_name="Amina Mansoor",
            contact_channel="whatsapp",
            contact_target="+966501234567",
            sku="COFFEE-ESP-01",
            language="ar",
            subject="استفسار بن اسبريسو",
            body="مرحباً أمينة، بن اسبريسو إيطالي فاخر متوفر بسعر 120.00 SAR.",
            consent_to_contact=True,
            consent_evidence="optin:web_20261110",
            verification_report=self.valid_report,
            approval_status=ApprovalStatus.PENDING_REVIEW,
            inquiry_text="هل لديكم بن اسبريسو وكم سعره؟",
        )
        self.ledger.save_draft(self.draft_valid)

        # Sample draft with price issue
        self.uncertain_report = VerificationReport(
            status=VerificationStatus.UNCERTAIN,
            checks=[
                ClaimVerification(claim_type=ClaimType.CONSENT, claimed_value=True, catalog_value=True, is_valid=True, notes="Opt-in consent verified."),
                ClaimVerification(claim_type=ClaimType.PRICE, claimed_value=99.0, catalog_value=120.0, is_valid=False, notes="Price mismatch."),
            ],
            reasons=["Stated price 99 does not match catalog 120.00 SAR."],
            requires_human_intervention=True,
        )
        self.draft_uncertain = DraftMessage(
            draft_id="draft_tui_002",
            lead_id="lead_tui_002",
            customer_name="سلطان الحربي",
            contact_channel="email",
            contact_target="sultan@example.sa",
            sku="PHONE-128",
            language="ar",
            subject="استفسار هاتف",
            body="مرحباً سلطان، هاتف ذكي 128 جيجا متوفر بسعر 999 SAR.",
            consent_to_contact=True,
            consent_evidence="optin:web_20261110",
            verification_report=self.uncertain_report,
            approval_status=ApprovalStatus.PENDING_REVIEW,
            inquiry_text="كم سعر الهاتف؟",
        )
        self.ledger.save_draft(self.draft_uncertain)

        # Sample draft with no consent
        self.failed_report = VerificationReport(
            status=VerificationStatus.FAIL,
            checks=[
                ClaimVerification(claim_type=ClaimType.CONSENT, claimed_value=False, catalog_value=True, is_valid=False, notes="No consent."),
            ],
            reasons=["Missing opt-in contact consent."],
            requires_human_intervention=True,
        )
        self.draft_no_consent = DraftMessage(
            draft_id="draft_tui_003",
            lead_id="lead_tui_003",
            customer_name="No Consent User",
            contact_channel="sms",
            contact_target="+966550000000",
            sku="COFFEE-ESP-01",
            language="ar",
            subject="عرض غير مطلوب",
            body="مرحباً، لدينا قهوة بسعر 120 SAR.",
            consent_to_contact=False,
            consent_evidence=None,
            verification_report=self.failed_report,
            approval_status=ApprovalStatus.PENDING_REVIEW,
            inquiry_text="عرض",
        )
        self.ledger.save_draft(self.draft_no_consent)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_format_draft_view_renders_inquiry_and_context(self):
        tui = HumanReviewTUI(ledger=self.ledger, catalog=self.catalog, use_color=False)
        view = tui.format_draft_view(self.draft_valid, 0, 3)

        self.assertIn("CATALOGCOPILOT HUMAN REVIEW [Draft 1 of 3]", view)
        self.assertIn("draft_tui_001", view)
        self.assertIn("lead_tui_001 (Amina Mansoor)", view)
        self.assertIn("whatsapp -> +966501234567", view)
        self.assertIn("COFFEE-ESP-01", view)
        self.assertIn("120.00 SAR", view)
        self.assertIn("[OPT-IN CONFIRMED]", view)
        self.assertIn('"هل لديكم بن اسبريسو وكم سعره؟"', view)
        self.assertIn("PROPOSED OUTBOUND DRAFT MESSAGE", view)
        self.assertIn("COMMERCIAL CLAIM VERIFICATION REPORT:", view)
        self.assertIn("[PASS]", view)

    def test_format_draft_view_no_consent_banner(self):
        tui = HumanReviewTUI(ledger=self.ledger, catalog=self.catalog, use_color=False)
        view = tui.format_draft_view(self.draft_no_consent, 2, 3)

        self.assertIn("[NO CONSENT / REVOKED - OUTREACH PROHIBITED]", view)
        self.assertIn("Missing opt-in contact consent.", view)

    def test_approve_valid_draft(self):
        input_s = io.StringIO("Approved by Tariq\n")
        output_s = io.StringIO()
        tui = HumanReviewTUI(
            ledger=self.ledger,
            catalog=self.catalog,
            reviewer="Supervisor Tariq",
            input_stream=input_s,
            output_stream=output_s,
            use_color=False,
        )

        success = tui.handle_approve(self.draft_valid)
        self.assertTrue(success)
        self.assertEqual(self.draft_valid.approval_status, ApprovalStatus.APPROVED)

        stored = self.ledger.get_draft("draft_tui_001")
        self.assertEqual(stored.approval_status, ApprovalStatus.APPROVED)
        self.assertEqual(stored.reviewed_by, "Supervisor Tariq")
        self.assertEqual(stored.reviewer_notes, "Approved by Tariq")

    def test_approve_blocked_by_consent_guardrail(self):
        input_s = io.StringIO("\n")
        output_s = io.StringIO()
        tui = HumanReviewTUI(
            ledger=self.ledger,
            catalog=self.catalog,
            reviewer="Supervisor Tariq",
            input_stream=input_s,
            output_stream=output_s,
            use_color=False,
        )

        success = tui.handle_approve(self.draft_no_consent)
        self.assertFalse(success)
        self.assertIn("[APPROVAL BLOCKED] Outreach strictly prohibited: customer has no opt-in consent!", output_s.getvalue())

        stored = self.ledger.get_draft("draft_tui_003")
        self.assertEqual(stored.approval_status, ApprovalStatus.PENDING_REVIEW)

    def test_approve_blocked_by_uncertain_verification(self):
        input_s = io.StringIO("\n")
        output_s = io.StringIO()
        tui = HumanReviewTUI(
            ledger=self.ledger,
            catalog=self.catalog,
            reviewer="Supervisor Tariq",
            input_stream=input_s,
            output_stream=output_s,
            use_color=False,
        )

        success = tui.handle_approve(self.draft_uncertain)
        self.assertFalse(success)
        self.assertIn("[APPROVAL BLOCKED] Draft verification status is 'UNCERTAIN'", output_s.getvalue())

        stored = self.ledger.get_draft("draft_tui_002")
        self.assertEqual(stored.approval_status, ApprovalStatus.PENDING_REVIEW)

    def test_reject_draft_with_mandatory_reason(self):
        input_s = io.StringIO("Out of stock until next quarter\n")
        output_s = io.StringIO()
        tui = HumanReviewTUI(
            ledger=self.ledger,
            catalog=self.catalog,
            reviewer="Supervisor Tariq",
            input_stream=input_s,
            output_stream=output_s,
            use_color=False,
        )

        success = tui.handle_reject(self.draft_valid)
        self.assertTrue(success)
        self.assertEqual(self.draft_valid.approval_status, ApprovalStatus.REJECTED)

        stored = self.ledger.get_draft("draft_tui_001")
        self.assertEqual(stored.approval_status, ApprovalStatus.REJECTED)
        self.assertEqual(stored.reviewer_notes, "Out of stock until next quarter")

    def test_reject_cancelled_when_reason_empty_or_cancel(self):
        input_s = io.StringIO(":cancel\n")
        output_s = io.StringIO()
        tui = HumanReviewTUI(
            ledger=self.ledger,
            catalog=self.catalog,
            reviewer="Supervisor Tariq",
            input_stream=input_s,
            output_stream=output_s,
            use_color=False,
        )

        success = tui.handle_reject(self.draft_valid)
        self.assertFalse(success)
        self.assertIn("Rejection cancelled: reason is mandatory.", output_s.getvalue())

        stored = self.ledger.get_draft("draft_tui_001")
        self.assertEqual(stored.approval_status, ApprovalStatus.PENDING_REVIEW)

    def test_edit_and_approve_successful_reverification(self):
        # Valid edit: correct price 1200.00 SAR
        new_text = "مرحباً سلطان، هاتف ذكي 128 جيجا متوفر لدينا بسعر 1200.00 SAR. فريق المبيعات.\n.\nCorrected price\n"
        input_s = io.StringIO(new_text)
        output_s = io.StringIO()
        tui = HumanReviewTUI(
            ledger=self.ledger,
            catalog=self.catalog,
            reviewer="Supervisor Tariq",
            input_stream=input_s,
            output_stream=output_s,
            use_color=False,
        )

        success = tui.handle_edit(self.draft_uncertain)
        self.assertTrue(success)
        self.assertEqual(self.draft_uncertain.approval_status, ApprovalStatus.EDITED_AND_APPROVED)

        stored = self.ledger.get_draft("draft_tui_002")
        self.assertEqual(stored.approval_status, ApprovalStatus.EDITED_AND_APPROVED)
        self.assertEqual(stored.verification_report.status, VerificationStatus.PASS)

    def test_edit_rejected_when_reverification_fails(self):
        # Invalid edit: hallucinates 500 SAR price
        new_text = "مرحباً سلطان، هاتف ذكي 128 جيجا متوفر لدينا بسعر 500.00 SAR.\n.\n"
        input_s = io.StringIO(new_text)
        output_s = io.StringIO()
        tui = HumanReviewTUI(
            ledger=self.ledger,
            catalog=self.catalog,
            reviewer="Supervisor Tariq",
            input_stream=input_s,
            output_stream=output_s,
            use_color=False,
        )

        success = tui.handle_edit(self.draft_uncertain)
        self.assertFalse(success)
        self.assertIn("[GUARDRAIL REJECTION] Edit failed deterministic verification", output_s.getvalue())

        stored = self.ledger.get_draft("draft_tui_002")
        self.assertEqual(stored.approval_status, ApprovalStatus.PENDING_REVIEW)

    def test_edit_blocked_on_unconsented_lead(self):
        input_s = io.StringIO("any edit\n.\n")
        output_s = io.StringIO()
        tui = HumanReviewTUI(
            ledger=self.ledger,
            catalog=self.catalog,
            reviewer="Supervisor Tariq",
            input_stream=input_s,
            output_stream=output_s,
            use_color=False,
        )

        success = tui.handle_edit(self.draft_no_consent)
        self.assertFalse(success)
        self.assertIn("[EDIT BLOCKED] Customer has not granted contact consent.", output_s.getvalue())

    def test_edit_cancelled_with_keyword(self):
        input_s = io.StringIO(":cancel\n")
        output_s = io.StringIO()
        tui = HumanReviewTUI(
            ledger=self.ledger,
            catalog=self.catalog,
            reviewer="Supervisor Tariq",
            input_stream=input_s,
            output_stream=output_s,
            use_color=False,
        )

        success = tui.handle_edit(self.draft_uncertain)
        self.assertFalse(success)
        self.assertIn("Edit cancelled.", output_s.getvalue())

    def test_run_navigation_and_quit(self):
        # Sequence:
        # 1. On draft 1: press 'N' (next) -> moves to draft 2
        # 2. On draft 2: press 'P' (prev) -> moves to draft 1
        # 3. On draft 1: press 'P' -> already at first draft
        # 4. Press 'Q' (quit)
        input_s = io.StringIO("N\nP\nP\nQ\n")
        output_s = io.StringIO()
        tui = HumanReviewTUI(
            ledger=self.ledger,
            catalog=self.catalog,
            reviewer="Supervisor Tariq",
            input_stream=input_s,
            output_stream=output_s,
            use_color=False,
        )

        exit_code = tui.run()
        self.assertEqual(exit_code, 0)
        output_val = output_s.getvalue()
        self.assertIn("Already at the first draft.", output_val)
        self.assertIn("REVIEW SESSION SUMMARY", output_val)

    def test_run_graceful_on_eof(self):
        input_s = io.StringIO("")  # immediate EOF
        output_s = io.StringIO()
        tui = HumanReviewTUI(
            ledger=self.ledger,
            catalog=self.catalog,
            reviewer="Supervisor Tariq",
            input_stream=input_s,
            output_stream=output_s,
            use_color=False,
        )

        exit_code = tui.run()
        self.assertEqual(exit_code, 0)
        self.assertIn("Review session terminated.", output_s.getvalue())

    def test_ensure_reviewer_identity_prompts_when_missing(self):
        input_s = io.StringIO("\n  \nAuditor Zayd\n")
        output_s = io.StringIO()
        tui = HumanReviewTUI(
            ledger=self.ledger,
            catalog=self.catalog,
            reviewer=None,
            input_stream=input_s,
            output_stream=output_s,
            use_color=False,
        )

        identity = tui.ensure_reviewer_identity()
        self.assertEqual(identity, "Auditor Zayd")
        self.assertEqual(tui.reviewer, "Auditor Zayd")

    def test_cli_tui_command_integration(self):
        import subprocess
        # Test running catalog-copilot tui --db ... with piping Q
        res = subprocess.run(
            [
                "python3",
                "-m",
                "catalog_copilot.cli",
                "tui",
                "--db",
                self.db_path,
                "--reviewer",
                "Auditor CLI",
            ],
            input="Q\n",
            capture_output=True,
            text=True,
            cwd=str(self.catalog_copilot_root if hasattr(self, "catalog_copilot_root") else os.path.dirname(os.path.dirname(__file__))),
        )
        self.assertEqual(res.returncode, 0)
        self.assertIn("CATALOGCOPILOT HUMAN REVIEW", res.stdout)
        self.assertIn("REVIEW SESSION SUMMARY", res.stdout)

    def test_cli_review_interactive_flag(self):
        import subprocess
        res = subprocess.run(
            [
                "python3",
                "-m",
                "catalog_copilot.cli",
                "review",
                "--interactive",
                "--db",
                self.db_path,
                "--reviewer",
                "Auditor CLI",
            ],
            input="Q\n",
            capture_output=True,
            text=True,
            cwd=str(self.catalog_copilot_root if hasattr(self, "catalog_copilot_root") else os.path.dirname(os.path.dirname(__file__))),
        )
        self.assertEqual(res.returncode, 0)
        self.assertIn("CATALOGCOPILOT HUMAN REVIEW", res.stdout)


if __name__ == "__main__":
    unittest.main()
