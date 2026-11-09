"""Bilingual catalog-grounded message draft generator for CatalogCopilot."""

from __future__ import annotations
import uuid
from typing import Optional, Callable, Dict, Any
from .models import (
    CatalogItem,
    LeadInquiry,
    DraftMessage,
    ApprovalStatus,
    VerificationReport,
    VerificationStatus,
)
from .catalog import Catalog
from .verifier import CommercialClaimVerifier


class DraftGenerator:
    """Generates bilingual sales drafts strictly grounded in catalog facts."""

    def __init__(self, verifier: Optional[CommercialClaimVerifier] = None):
        self.verifier = verifier or CommercialClaimVerifier()

    def generate_draft(
        self,
        lead: LeadInquiry,
        catalog: Catalog,
        custom_llm_callable: Optional[Callable[[str, Dict[str, Any]], str]] = None,
    ) -> DraftMessage:
        """Produces a grounded draft in the customer's preferred language (AR/EN)."""
        draft_id = f"DRAFT-{uuid.uuid4().hex[:8].upper()}"
        sku = lead.requested_sku

        item: Optional[CatalogItem] = None
        if sku:
            item = catalog.get_item(sku)

        # If SKU was not explicitly set in lead, attempt search by inquiry text
        if item is None and lead.inquiry_text:
            matches = catalog.search(lead.inquiry_text, lang=lead.preferred_language, limit=1)
            if matches:
                item = matches[0]
                sku = item.sku

        # Generate bilingual content
        if lead.preferred_language == "ar":
            subject, body = self._render_arabic_draft(lead, item)
        else:
            subject, body = self._render_english_draft(lead, item)

        # Allow pluggable LLM enhancement if provided, but enforce ground truth template by default
        if custom_llm_callable is not None and item is not None:
            context = {
                "customer_name": lead.customer_name,
                "preferred_language": lead.preferred_language,
                "inquiry_text": lead.inquiry_text,
                "item": item.to_dict(),
            }
            try:
                llm_output = custom_llm_callable(body, context)
                if llm_output and llm_output.strip():
                    body = llm_output.strip()
            except Exception:
                # Fail-safe: preserve grounded template if custom callable fails
                pass

        # Deterministically verify the generated draft before queuing
        report = self.verifier.verify(
            lead=lead,
            catalog=catalog,
            draft_text=body,
            target_sku=item.sku if item else None,
        )

        return DraftMessage(
            draft_id=draft_id,
            lead_id=lead.lead_id,
            customer_name=lead.customer_name,
            contact_channel=lead.contact_channel,
            contact_target=lead.contact_target,
            sku=item.sku if item else None,
            language=lead.preferred_language,
            subject=subject,
            body=body,
            verification_report=report,
            approval_status=ApprovalStatus.PENDING_REVIEW,
        )

    def _render_arabic_draft(self, lead: LeadInquiry, item: Optional[CatalogItem]) -> tuple[str, str]:
        """Renders Modern Standard Arabic draft grounded in verified catalog data."""
        name = lead.customer_name or "عميلنا العزيز"

        if item is None:
            subject = "بخصوص استفساركم التجاري"
            body = (
                f"مرحباً {name}،\n\n"
                f"نشكركم على تواصلكم واهتمامكم بمنتجاتنا. لقد استلمنا استفساركم: '{lead.inquiry_text}'.\n"
                f"يرجى تزويدنا برمز المنتج المطلوب أو تفاصيل إضافية لنتمكن من خدمتكم بالدقة المطلوبة.\n\n"
                f"مع أطيب التحيات،\nفريق المبيعات"
            )
            return subject, body

        subject = f"استفسار بخصوص {item.title_ar} ({item.sku})"

        if not item.in_stock:
            body = (
                f"مرحباً {name}،\n\n"
                f"نشكركم على اهتمامكم بمنتج {item.title_ar} (رمز المنتج: {item.sku}).\n"
                f"نود إفادتكم بأن هذا المنتج غير متوفر حالياً في مستودعاتنا، ونعمل على إعادة توفيره قريباً.\n"
                f"يمكننا إشعاركم فور توفر الشحنة القادمة إذا رغبتم في ذلك.\n\n"
                f"مع خالص الشكر والتقدير،\nفريق المبيعات"
            )
            return subject, body

        # In-stock item
        price_str = f"{item.effective_price:.2f} {item.currency}"
        offer_str = ""
        if item.discount_percent > 0:
            offer_str = f" مع خصم {item.discount_percent:.0f}% (السعر الأساسي: {item.price:.2f} {item.currency})"
            if item.offer_description_ar:
                offer_str += f" - {item.offer_description_ar}"

        body = (
            f"مرحباً {name}،\n\n"
            f"يسعدنا الرد على استفساركم بخصوص {item.title_ar} (رمز المنتج: {item.sku}).\n"
            f"المنتج متوفر لدينا حالياً بسعر {price_str}{offer_str}.\n"
            f"إذا كنتم ترغبون في تأكيد الطلب أو لديكم أي استفسار آخر، فنحن سعداء بخدمتكم.\n\n"
            f"مع أطيب التحيات،\nفريق خدمة العملاء والمبيعات"
        )
        return subject, body

    def _render_english_draft(self, lead: LeadInquiry, item: Optional[CatalogItem]) -> tuple[str, str]:
        """Renders professional English sales draft grounded in catalog facts."""
        name = lead.customer_name or "Valued Customer"

        if item is None:
            subject = "Regarding your inquiry"
            body = (
                f"Dear {name},\n\n"
                f"Thank you for contacting us regarding: '{lead.inquiry_text}'.\n"
                f"Could you please confirm the specific product SKU or title you are interested in so we can provide exact details?\n\n"
                f"Best regards,\nSales Support Team"
            )
            return subject, body

        subject = f"Inquiry regarding {item.title_en} ({item.sku})"

        if not item.in_stock:
            body = (
                f"Dear {name},\n\n"
                f"Thank you for your interest in {item.title_en} (SKU: {item.sku}).\n"
                f"Currently, this item is out of stock in our inventory. We are working on restocking soon.\n"
                f"Please let us know if you would like us to notify you once it becomes available.\n\n"
                f"Best regards,\nSales Support Team"
            )
            return subject, body

        price_str = f"{item.effective_price:.2f} {item.currency}"
        offer_str = ""
        if item.discount_percent > 0:
            offer_str = f" with an active {item.discount_percent:.0f}% discount (regular price: {item.price:.2f} {item.currency})"
            if item.offer_description_en:
                offer_str += f" - {item.offer_description_en}"

        body = (
            f"Dear {name},\n\n"
            f"Thank you for inquiring about {item.title_en} (SKU: {item.sku}).\n"
            f"This item is currently in stock at the price of {price_str}{offer_str}.\n"
            f"Please let us know if you would like to proceed with an order or have additional questions.\n\n"
            f"Best regards,\nCustomer Sales Team"
        )
        return subject, body
