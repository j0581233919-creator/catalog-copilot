"""End-to-end and adversarial test suite for Arabic dialect normalization,
fuzzy SKU resolution, and deterministic verification guardrails.
"""

import unittest
import tempfile
import shutil
from pathlib import Path

from catalog_copilot.models import (
    CatalogItem,
    LeadInquiry,
    VerificationStatus,
    ApprovalStatus,
)
from catalog_copilot.catalog import Catalog
from catalog_copilot.generator import DraftGenerator
from catalog_copilot.verifier import CommercialClaimVerifier
from catalog_copilot.approval import ApprovalLedger
from catalog_copilot.chatwoot import (
    ChatwootWebhookHandler,
    ChatwootApiClient,
    MockChatwootTransport,
)


class TestDialectAndFuzzyE2E(unittest.TestCase):
    """End-to-end integration and adversarial tests for CC-004."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / "test_dialect.db"
        self.ledger = ApprovalLedger(db_path=self.db_path)
        self.verifier = CommercialClaimVerifier()
        self.generator = DraftGenerator(verifier=self.verifier)

        # Ingest representative bilingual catalog
        self.item_iphone_pro = CatalogItem(
            sku="APL-IPH-15PRO",
            title_en="Apple iPhone 15 Pro Max 256GB",
            title_ar="آبل آيفون 15 برو ماكس 256 جيجابايت",
            price=4500.0,
            stock_quantity=12,
            category="Smartphones",
        )
        self.item_iphone_base = CatalogItem(
            sku="APL-IPH-15BASE",
            title_en="Apple iPhone 15 128GB",
            title_ar="آبل آيفون 15 سعة 128 جيجابايت",
            price=3200.0,
            stock_quantity=5,
            category="Smartphones",
        )
        self.item_samsung = CatalogItem(
            sku="SAM-GAL-S24",
            title_en="Samsung Galaxy S24 Ultra",
            title_ar="سامسونج جالاكسي اس 24 الترا",
            price=4000.0,
            stock_quantity=8,
            discount_percent=10.0,
            discount_price=3600.0,
            offer_description_en="10% Seasonal Discount",
            offer_description_ar="خصم موسمي 10%",
            category="Smartphones",
        )
        self.item_dell = CatalogItem(
            sku="DEL-XPS-15",
            title_en="Dell XPS 15 Laptop Intel Core i7",
            title_ar="لابتوب ديل اكس بي اس 15 انتل كور اي 7",
            price=6500.0,
            stock_quantity=4,
            category="Computers",
        )
        self.catalog = Catalog([
            self.item_iphone_pro,
            self.item_iphone_base,
            self.item_samsung,
            self.item_dell,
        ])

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_egyptian_dialect_inquiry_resolves_and_verifies_pass(self):
        """Egyptian dialect inquiry must be classified, resolved to SKU, verified, and saved to DB."""
        lead = LeadInquiry(
            lead_id="LEAD-EGY-001",
            customer_name="أحمد محمود",
            contact_channel="whatsapp",
            contact_target="+201012345678",
            consent_to_contact=True,
            consent_evidence="WhatsApp opt-in checkbox",
            preferred_language="ar",
            inquiry_text="ازيك يا باشا، عايز اعرف بكام لابتوب ديل اكس بي اس 15 ده لو سمحت وموجود منه؟",
        )

        draft = self.generator.generate_draft(lead=lead, catalog=self.catalog)

        # 1. Verification and metadata checks
        self.assertEqual(draft.sku, "DEL-XPS-15")
        self.assertEqual(draft.detected_dialect, "EGYPTIAN")
        self.assertIn("PRICE_INQUIRY", draft.query_intents)
        self.assertIn("STOCK_INQUIRY", draft.query_intents)
        self.assertEqual(draft.verification_report.status, VerificationStatus.PASS)

        # 2. Content checks
        self.assertIn("6500.00 SAR", draft.body)
        self.assertIn("DEL-XPS-15", draft.body)

        # 3. Persistence and approval workflow
        self.ledger.save_draft(draft)
        retrieved = self.ledger.get_draft(draft.draft_id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.detected_dialect, "EGYPTIAN")
        self.assertIn("PRICE_INQUIRY", retrieved.query_intents)

        approved = self.ledger.approve(draft_id=draft.draft_id, reviewer="Supervisor Omar")
        self.assertEqual(approved.approval_status, ApprovalStatus.APPROVED)

    def test_gulf_dialect_inquiry_with_discount_and_stock(self):
        """Gulf dialect inquiry asking for offer and stock must reflect discounted catalog facts."""
        lead = LeadInquiry(
            lead_id="LEAD-GLF-002",
            customer_name="سعود الشمري",
            contact_channel="whatsapp",
            contact_target="+966501234567",
            consent_to_contact=True,
            consent_evidence="Web store checkout opt-in",
            preferred_language="ar",
            inquiry_text="هلا والله لاهنت، كم سعر سامسونج جالاكسي اس 24 الترا الحين وهل متوفر وفيه خصم؟",
        )

        draft = self.generator.generate_draft(lead=lead, catalog=self.catalog)

        self.assertEqual(draft.sku, "SAM-GAL-S24")
        self.assertEqual(draft.detected_dialect, "GULF")
        self.assertIn("PRICE_INQUIRY", draft.query_intents)
        self.assertIn("OFFER_INQUIRY", draft.query_intents)
        self.assertEqual(draft.verification_report.status, VerificationStatus.PASS)

        # Effective discounted price must be quoted
        self.assertIn("3600.00 SAR", draft.body)
        self.assertIn("10%", draft.body)

    def test_levantine_dialect_inquiry_resolves_accurately(self):
        """Levantine dialect inquiry must be recognized and grounded in catalog facts."""
        lead = LeadInquiry(
            lead_id="LEAD-LEV-003",
            customer_name="رامي حداد",
            contact_channel="whatsapp",
            contact_target="+962791234567",
            consent_to_contact=True,
            consent_evidence="Customer initiated chat with opt-in",
            preferred_language="ar",
            inquiry_text="يسعد مساك لو بتريد، بدي اعرف قديش سعر لابتوب ديل اكس بي اس 15 وشكرا الك كتير",
        )

        draft = self.generator.generate_draft(lead=lead, catalog=self.catalog)

        self.assertEqual(draft.sku, "DEL-XPS-15")
        self.assertEqual(draft.detected_dialect, "LEVANTINE")
        self.assertIn("PRICE_INQUIRY", draft.query_intents)
        self.assertEqual(draft.verification_report.status, VerificationStatus.PASS)
        self.assertIn("6500.00 SAR", draft.body)

    def test_ambiguous_inquiry_generates_clarification_draft(self):
        """An ambiguous inquiry matching multiple products closely must generate a clarification draft."""
        lead = LeadInquiry(
            lead_id="LEAD-AMB-004",
            customer_name="خالد عبد العزيز",
            contact_channel="email",
            contact_target="khaled@example.com",
            consent_to_contact=True,
            consent_evidence="Signed newsletter agreement",
            preferred_language="ar",
            inquiry_text="عايز اعرف بكام الايفون 15 ده؟",
        )

        draft = self.generator.generate_draft(lead=lead, catalog=self.catalog)

        # Invariant: Must NOT guess between Pro and Base
        self.assertIsNone(draft.sku)
        self.assertIn("APL-IPH-15PRO", draft.body)
        self.assertIn("APL-IPH-15BASE", draft.body)
        self.assertIn("توضيح", draft.subject)

        # Verification must PASS because no ungrounded price was promised
        self.assertEqual(draft.verification_report.status, VerificationStatus.PASS)

    def test_adversarial_hallucinated_price_fails_verification(self):
        """If a corrupted custom generator attempts to promise a bargained price, verifier must FAIL it."""
        lead = LeadInquiry(
            lead_id="LEAD-ADV-005",
            customer_name="مشتري تفاوضي",
            contact_channel="whatsapp",
            contact_target="+201198765432",
            consent_to_contact=True,
            consent_evidence="Opt-in form",
            preferred_language="ar",
            inquiry_text="عايز لابتوب ديل بسعر 1000 ريال بس",
        )

        # Malicious/buggy custom callable attempting to promise 1000 SAR instead of catalog 6500 SAR
        def fake_llm(base_draft: str, ctx: dict) -> str:
            return "مرحباً، يسعدنا إفادتكم بأن لابتوب ديل متوفر لدينا حالياً بسعر 1000.00 SAR فقط!"

        draft = self.generator.generate_draft(
            lead=lead,
            catalog=self.catalog,
            custom_llm_callable=fake_llm,
        )

        # Verification MUST catch the discrepancy and fail closed!
        self.assertNotEqual(draft.verification_report.status, VerificationStatus.PASS)
        self.assertIn(draft.verification_report.status, (VerificationStatus.FAIL, VerificationStatus.UNCERTAIN))

    def test_unconsented_lead_in_dialect_strictly_blocked(self):
        """Lead in dialect without opt-in consent must FAIL verification and be blocked from approval."""
        lead = LeadInquiry(
            lead_id="LEAD-NOCONSENT-006",
            customer_name="مستخدم بدون موافقة",
            contact_channel="whatsapp",
            contact_target="+966551234567",
            consent_to_contact=False,  # NO CONSENT
            consent_evidence="",
            preferred_language="ar",
            inquiry_text="ابغى اعرف كم سعر سامسونج جالاكسي اس 24",
        )

        draft = self.generator.generate_draft(lead=lead, catalog=self.catalog)
        self.assertEqual(draft.verification_report.status, VerificationStatus.FAIL)
        self.assertIn("Consent violation", " ".join(draft.verification_report.reasons))

        self.ledger.save_draft(draft)
        with self.assertRaises(ValueError):
            self.ledger.approve(draft_id=draft.draft_id, reviewer="Supervisor")

    def test_chatwoot_webhook_with_dialectal_customer_message(self):
        """Chatwoot webhook handler must handle dialectal inquiries, annotate private notes, and support approval."""
        transport = MockChatwootTransport()
        api_client = ChatwootApiClient(
            base_url="https://app.chatwoot.com",
            api_access_token="test_token",
            account_id=1,
            transport=transport,
        )
        handler = ChatwootWebhookHandler(
            catalog=self.catalog,
            ledger=self.ledger,
            api_client=api_client,
        )

        payload = {
            "event": "message_created",
            "message_type": "incoming",
            "content": "ازيك يا فندم، عايز اعرف بكام لابتوب ديل اكس بي اس 15 ده لو سمحت وموجود؟",
            "id": 901,
            "conversation": {
                "id": 501,
                "meta": {
                    "sender": {
                        "name": "محمود سمير",
                        "email": "mahmoud@example.com",
                        "phone_number": "+201099998888",
                    }
                },
                "custom_attributes": {
                    "consent_to_contact": True,
                    "consent_evidence": "Chatwoot Widget Consent Checkbox",
                },
            },
        }

        # 1. Process incoming customer message
        res = handler.process_webhook(payload)
        self.assertEqual(res["status"], "draft_created")
        self.assertEqual(res["verification_status"], "PASS")

        # Private note should have been sent containing dialect info
        self.assertEqual(len(transport.sent_messages), 1)
        private_note_body = transport.sent_messages[0]["payload"]["content"]
        self.assertIn("EGYPTIAN", private_note_body)
        self.assertIn("DEL-XPS-15", private_note_body)

        draft_id = res["draft_id"]

        # 2. Agent approves via Chatwoot command
        agent_payload = {
            "event": "message_created",
            "message_type": "outgoing",
            "private": True,
            "content": f"/approve {draft_id}",
            "id": 902,
            "sender": {"type": "user", "name": "Sarah Agent"},
            "conversation": {"id": 501},
        }

        approve_res = handler.process_webhook(agent_payload)
        self.assertEqual(approve_res["status"], "approved_and_dispatched")

        # Outgoing public reply sent to customer
        public_calls = [c for c in transport.sent_messages if not c["payload"]["private"]]
        self.assertEqual(len(public_calls), 1)
        self.assertIn("6500.00 SAR", public_calls[0]["payload"]["content"])


if __name__ == "__main__":
    unittest.main()
