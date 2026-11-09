"""Tests for CommercialClaimVerifier."""

import unittest
from catalog_copilot.models import (
    CatalogItem,
    LeadInquiry,
    VerificationStatus,
    ClaimType,
)
from catalog_copilot.catalog import Catalog
from catalog_copilot.verifier import CommercialClaimVerifier


class TestCommercialClaimVerifier(unittest.TestCase):

    def setUp(self):
        self.catalog = Catalog()
        self.item_in_stock = CatalogItem(
            sku="SKU-COFFEE-01",
            title_en="Arabica Coffee 1kg",
            title_ar="قهوة أرابيكا 1 كجم",
            price=100.0,
            currency="SAR",
            stock_quantity=20,
            is_active=True,
            discount_percent=10.0,
            discount_price=90.0,
        )
        self.item_out_of_stock = CatalogItem(
            sku="SKU-COFFEE-02",
            title_en="Robusta Coffee 1kg",
            title_ar="قهوة روبوستا 1 كجم",
            price=80.0,
            currency="SAR",
            stock_quantity=0,
            is_active=True,
        )
        self.catalog.add_item(self.item_in_stock)
        self.catalog.add_item(self.item_out_of_stock)
        self.verifier = CommercialClaimVerifier()

        self.authorized_lead = LeadInquiry(
            lead_id="L-1",
            customer_name="Ali",
            contact_channel="email",
            contact_target="ali@example.com",
            consent_to_contact=True,
            preferred_language="en",
            inquiry_text="How much is the Arabica Coffee?",
            requested_sku="SKU-COFFEE-01",
        )

    def test_valid_claim_passes(self):
        draft_text = "Hello Ali, SKU-COFFEE-01 is currently in stock at 90.00 SAR with a 10% discount."
        report = self.verifier.verify(
            lead=self.authorized_lead,
            catalog=self.catalog,
            draft_text=draft_text,
            target_sku="SKU-COFFEE-01",
        )
        self.assertEqual(report.status, VerificationStatus.PASS)
        self.assertFalse(report.requires_human_intervention)
        self.assertEqual(len(report.reasons), 0)

    def test_price_hallucination_fails(self):
        # Claiming 50 SAR instead of 90 or 100 SAR
        draft_text = "Hello Ali, SKU-COFFEE-01 is available for only 50.00 SAR!"
        report = self.verifier.verify(
            lead=self.authorized_lead,
            catalog=self.catalog,
            draft_text=draft_text,
            target_sku="SKU-COFFEE-01",
        )
        self.assertEqual(report.status, VerificationStatus.FAIL)
        self.assertTrue(report.requires_human_intervention)
        price_checks = [c for c in report.checks if c.claim_type == ClaimType.PRICE]
        self.assertTrue(any(not c.is_valid for c in price_checks))
        self.assertIn("Price mismatch", report.reasons[0])

    def test_stock_mismatch_fails_when_promising_out_of_stock(self):
        lead = LeadInquiry(
            lead_id="L-2",
            customer_name="Omar",
            contact_channel="email",
            contact_target="omar@example.com",
            consent_to_contact=True,
            preferred_language="en",
            inquiry_text="Robusta please",
            requested_sku="SKU-COFFEE-02",
        )
        draft_text = "Good news Omar, SKU-COFFEE-02 is in stock and available for 80 SAR!"
        report = self.verifier.verify(
            lead=lead,
            catalog=self.catalog,
            draft_text=draft_text,
            target_sku="SKU-COFFEE-02",
        )
        self.assertEqual(report.status, VerificationStatus.FAIL)
        stock_checks = [c for c in report.checks if c.claim_type == ClaimType.STOCK_AVAILABILITY]
        self.assertTrue(any(not c.is_valid for c in stock_checks))
        self.assertTrue(any("Stock mismatch" in r for r in report.reasons))

    def test_unauthorized_discount_fails(self):
        draft_text = "Hello Ali, SKU-COFFEE-01 is available with a 30% discount at 90 SAR."
        report = self.verifier.verify(
            lead=self.authorized_lead,
            catalog=self.catalog,
            draft_text=draft_text,
            target_sku="SKU-COFFEE-01",
        )
        self.assertEqual(report.status, VerificationStatus.FAIL)
        discount_checks = [c for c in report.checks if c.claim_type == ClaimType.DISCOUNT_OFFER]
        self.assertTrue(any(not c.is_valid for c in discount_checks))

    def test_unsolicited_message_fails_on_missing_consent(self):
        unauthorized_lead = LeadInquiry(
            lead_id="L-3",
            customer_name="Unauthorized Lead",
            contact_channel="phone",
            contact_target="+966555555555",
            consent_to_contact=False,  # NO CONSENT
            preferred_language="en",
            inquiry_text="Inquiry",
            requested_sku="SKU-COFFEE-01",
        )
        draft_text = "Hello, SKU-COFFEE-01 is available for 100 SAR in stock."
        report = self.verifier.verify(
            lead=unauthorized_lead,
            catalog=self.catalog,
            draft_text=draft_text,
            target_sku="SKU-COFFEE-01",
        )
        self.assertEqual(report.status, VerificationStatus.FAIL)
        consent_check = [c for c in report.checks if c.claim_type == ClaimType.CONSENT][0]
        self.assertFalse(consent_check.is_valid)
        self.assertTrue(any("Consent violation" in r for r in report.reasons))

    def test_arabic_draft_verification(self):
        arabic_lead = LeadInquiry(
            lead_id="L-4",
            customer_name="سعد",
            contact_channel="whatsapp",
            contact_target="+966500000000",
            consent_to_contact=True,
            preferred_language="ar",
            inquiry_text="سعر القهوة",
            requested_sku="SKU-COFFEE-01",
        )
        draft_text = "مرحباً سعد، المنتج متوفر لدينا حالياً بسعر 90.00 ريال مع خصم 10%."
        report = self.verifier.verify(
            lead=arabic_lead,
            catalog=self.catalog,
            draft_text=draft_text,
            target_sku="SKU-COFFEE-01",
        )
        self.assertEqual(report.status, VerificationStatus.PASS)

    def test_currency_mismatch_fails(self):
        draft_text = "Hello Ali, SKU-COFFEE-01 is available for 100 USD."
        report = self.verifier.verify(
            lead=self.authorized_lead,
            catalog=self.catalog,
            draft_text=draft_text,
            target_sku="SKU-COFFEE-01",
        )
        self.assertEqual(report.status, VerificationStatus.FAIL)
        curr_checks = [c for c in report.checks if c.claim_type == ClaimType.CURRENCY]
        self.assertTrue(any(not c.is_valid for c in curr_checks))

    def test_unidentified_sku_fails_closed(self):
        draft_text = "Special price: 100 SAR, in stock today!"
        lead_no_sku = LeadInquiry(
            lead_id="L-5",
            customer_name="Guest",
            contact_channel="email",
            contact_target="guest@example.com",
            consent_to_contact=True,
            preferred_language="en",
            inquiry_text="I want cheap stuff",
            requested_sku=None,
        )
        report = self.verifier.verify(
            lead=lead_no_sku,
            catalog=self.catalog,
            draft_text=draft_text,
            target_sku=None,
        )
        self.assertEqual(report.status, VerificationStatus.UNCERTAIN)
        self.assertTrue(report.requires_human_intervention)


if __name__ == "__main__":
    unittest.main()
