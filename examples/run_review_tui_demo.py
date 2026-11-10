#!/usr/bin/env python3
"""Interactive / Demonstration runner for CatalogCopilot Human Review TUI (CC-002).

Demonstrates:
1. ANSI-formatted review interface displaying customer context, inquiry, and claim checks.
2. Safe human approval of fully verified drafts.
3. Guardrail blocking approval of unconsented or non-PASS drafts.
4. Inline editing with deterministic real-time re-verification against the catalog.
5. Rejection with mandatory audit trail logging.
"""

from __future__ import annotations
import os
import sys
import tempfile
import io
from pathlib import Path

# Add repo root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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
from catalog_copilot.tui import HumanReviewTUI


def create_demo_data(ledger: ApprovalLedger, catalog: Catalog) -> None:
    # Item 1: Valid & consented lead
    valid_report = VerificationReport(
        status=VerificationStatus.PASS,
        checks=[
            ClaimVerification(claim_type=ClaimType.CONSENT, claimed_value=True, catalog_value=True, is_valid=True, notes="Explicit opt-in checkout consent."),
            ClaimVerification(claim_type=ClaimType.SKU_EXISTENCE, claimed_value="COFFEE-ESP-01", catalog_value="COFFEE-ESP-01", is_valid=True, notes="Resolved COFFEE-ESP-01 in active catalog."),
            ClaimVerification(claim_type=ClaimType.PRICE, claimed_value=120.0, catalog_value=120.0, is_valid=True, notes="Stated price 120.00 matches catalog 120.00 SAR."),
            ClaimVerification(claim_type=ClaimType.STOCK_AVAILABILITY, claimed_value="in_stock", catalog_value=45, is_valid=True, notes="In stock (45 units)."),
            ClaimVerification(claim_type=ClaimType.CURRENCY, claimed_value="SAR", catalog_value="SAR", is_valid=True, notes="Currency SAR aligned."),
            ClaimVerification(claim_type=ClaimType.UNGROUNDED_CLAIM, claimed_value=None, catalog_value=None, is_valid=True, notes="No ungrounded assertions."),
        ],
        reasons=[],
        requires_human_intervention=False,
    )
    draft1 = DraftMessage(
        draft_id="draft_demo_001",
        lead_id="lead_101",
        customer_name="Fatima Al-Zahrani",
        contact_channel="whatsapp",
        contact_target="+966501112233",
        sku="COFFEE-ESP-01",
        language="ar",
        subject="بخصوص استفسارك عن بن الاسبريسو الفاخر",
        body="مرحباً فاطمة،\nيسعدنا إعلامك بتوفر بن اسبريسو إيطالي فاخر (رمز: COFFEE-ESP-01) بسعر 120.00 SAR.\nالكمية متوفرة في المستودع.\nشكراً لتواصلك معنا.",
        consent_to_contact=True,
        consent_evidence="optin:web_checkout_20261101",
        verification_report=valid_report,
        approval_status=ApprovalStatus.PENDING_REVIEW,
        inquiry_text="هل متوفر عندكم بن اسبريسو إيطالي؟ وبكم الكيلو؟",
    )
    ledger.save_draft(draft1)

    # Item 2: Needs edit (price discrepancy in generated draft)
    uncertain_report = VerificationReport(
        status=VerificationStatus.UNCERTAIN,
        checks=[
            ClaimVerification(claim_type=ClaimType.CONSENT, claimed_value=True, catalog_value=True, is_valid=True, notes="Opt-in contact granted."),
            ClaimVerification(claim_type=ClaimType.SKU_EXISTENCE, claimed_value="PHONE-128", catalog_value="PHONE-128", is_valid=True, notes="Resolved PHONE-128 in catalog."),
            ClaimVerification(claim_type=ClaimType.PRICE, claimed_value=999.0, catalog_value=1200.0, is_valid=False, notes="Draft mentions 999 SAR but catalog price is 1200.00 SAR."),
        ],
        reasons=["Stated price 999 does not match catalog price 1200.00 SAR."],
        requires_human_intervention=True,
    )
    draft2 = DraftMessage(
        draft_id="draft_demo_002",
        lead_id="lead_102",
        customer_name="سلطان الحربي",
        contact_channel="email",
        contact_target="sultan@example.sa",
        sku="PHONE-128",
        language="ar",
        subject="استفسار عن الهاتف الذكي 128 جيجا",
        body="مرحباً سلطان،\nالهاتف الذكي 128 جيجا متوفر بسعر خاص 999 SAR.\nمع تحيات فريق المبيعات.",
        consent_to_contact=True,
        consent_evidence="optin:newsletter_footer_20261020",
        verification_report=uncertain_report,
        approval_status=ApprovalStatus.PENDING_REVIEW,
        inquiry_text="السلام عليكم، كم سعر الجوال الـ 128 جيجا؟",
    )
    ledger.save_draft(draft2)

    # Item 3: No consent (privacy guardrail violation)
    failed_report = VerificationReport(
        status=VerificationStatus.FAIL,
        checks=[
            ClaimVerification(claim_type=ClaimType.CONSENT, claimed_value=False, catalog_value=True, is_valid=False, notes="Customer explicitly refused marketing consent."),
        ],
        reasons=["Customer has not granted opt-in contact consent. Outreach strictly prohibited."],
        requires_human_intervention=True,
    )
    draft3 = DraftMessage(
        draft_id="draft_demo_003",
        lead_id="lead_103",
        customer_name="Rami Saleh",
        contact_channel="sms",
        contact_target="+966559988776",
        sku="COFFEE-ESP-01",
        language="ar",
        subject="عرض تجاري",
        body="مرحباً رامي، لدينا قهوة اسبريسو بسعر 120 ريال.",
        consent_to_contact=False,
        consent_evidence=None,
        verification_report=failed_report,
        approval_status=ApprovalStatus.PENDING_REVIEW,
        inquiry_text="اسبريسو",
    )
    ledger.save_draft(draft3)


def run_demo(auto_scripted: bool = False) -> int:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "demo_queue.db")
        ledger = ApprovalLedger(db_path=db_path)

        catalog = Catalog()
        catalog.add_item(CatalogItem(
            sku="COFFEE-ESP-01",
            title_en="Italian Espresso Beans 1kg",
            title_ar="بن اسبريسو إيطالي فاخر 1 كجم",
            price=120.0,
            currency="SAR",
            stock_quantity=45,
            is_active=True,
        ))
        catalog.add_item(CatalogItem(
            sku="PHONE-128",
            title_en="Smartphone 128GB",
            title_ar="هاتف ذكي 128 جيجا",
            price=1200.0,
            currency="SAR",
            stock_quantity=10,
            is_active=True,
        ))

        create_demo_data(ledger, catalog)

        if auto_scripted:
            print("--- Running Scripted Non-Interactive Demonstration ---")
            # Actions:
            # Draft 1 (valid): [A] Approve with notes
            # Draft 2 (price error): [E] Edit with correct price (1200.00 SAR) and submit '.' -> re-verified PASS
            # Draft 3 (no consent): [A] Try approve -> BLOCKED by guardrail -> [R] Reject with reason
            # [Q] Quit
            simulated_inputs = (
                "A\n"                # Action: Approve draft 1
                "Approved after customer confirmation\n"  # notes
                "E\n"                # Action: Edit draft 2
                "مرحباً سلطان، هاتف ذكي 128 جيجا متوفر لدينا بسعر 1200.00 SAR. فريق المبيعات.\n"  # corrected text
                ".\n"                # finish edit
                "Corrected price to catalog value 1200 SAR\n"  # edit note
                "A\n"                # Action: Attempt approve draft 3 (blocked)
                "R\n"                # Action: Reject draft 3
                "Opt-in consent missing from customer registration\n"  # rejection reason
                "Q\n"                # Quit
            )
            input_stream = io.StringIO(simulated_inputs)
            output_stream = io.StringIO()
            tui = HumanReviewTUI(
                ledger=ledger,
                catalog=catalog,
                reviewer="Supervisor Omar",
                input_stream=input_stream,
                output_stream=output_stream,
                use_color=False,
            )
            exit_code = tui.run()
            output_val = output_stream.getvalue()
            print(output_val)

            # Assert outcomes in DB
            d1 = ledger.get_draft("draft_demo_001")
            d2 = ledger.get_draft("draft_demo_002")
            d3 = ledger.get_draft("draft_demo_003")
            assert d1.approval_status == ApprovalStatus.APPROVED, f"Expected APPROVED, got {d1.approval_status}"
            assert d2.approval_status == ApprovalStatus.EDITED_AND_APPROVED, f"Expected EDITED_AND_APPROVED, got {d2.approval_status}"
            assert d3.approval_status == ApprovalStatus.REJECTED, f"Expected REJECTED, got {d3.approval_status}"
            print("\n>>> Scripted demonstration passed all commercial guardrails with 100% adherence! <<<")
            return exit_code
        else:
            tui = HumanReviewTUI(ledger=ledger, catalog=catalog)
            return tui.run()


if __name__ == "__main__":
    auto = "--auto" in sys.argv or "--test" in sys.argv
    sys.exit(run_demo(auto_scripted=auto))
