"""Bilingual catalog-grounded message draft generator for CatalogCopilot."""

from __future__ import annotations
import uuid
from typing import Optional, Callable, Dict, Any, List
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
from .dialect import parse_dialectal_inquiry, DialectalQueryAnalysis
from .fuzzy import ResolutionResult


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
        dialect_analysis: Optional[DialectalQueryAnalysis] = None
        resolution_result: Optional[ResolutionResult] = None

        if lead.inquiry_text and lead.preferred_language == "ar":
            dialect_analysis = parse_dialectal_inquiry(lead.inquiry_text)

        if sku:
            # Explicit SKU requested: strictly lookup
            item = catalog.get_item(sku)
            if item is None:
                # If exact SKU lookup failed, attempt fuzzy SKU resolution
                fuzzy_res = catalog.resolve_item_fuzzy(sku, lang=lead.preferred_language)
                resolution_result = fuzzy_res
                if fuzzy_res.has_match and not fuzzy_res.is_ambiguous:
                    item = fuzzy_res.resolved_item
        elif lead.inquiry_text:
            # Use cleansed product search tokens if dialect analysis exists, else inquiry text
            search_query = dialect_analysis.cleaned_product_query if dialect_analysis else lead.inquiry_text
            fuzzy_res = catalog.resolve_item_fuzzy(search_query, lang=lead.preferred_language)
            resolution_result = fuzzy_res
            if fuzzy_res.has_match and not fuzzy_res.is_ambiguous:
                item = fuzzy_res.resolved_item
            else:
                item = None

        # Generate bilingual content
        if lead.preferred_language == "ar":
            subject, body = self._render_arabic_draft(lead, item, resolution_result)
        else:
            subject, body = self._render_english_draft(lead, item, resolution_result)

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
            target_sku=item.sku if item else lead.requested_sku,
        )

        detected_dialect_val = dialect_analysis.detected_dialect.value if dialect_analysis else None
        intents_list = [i.value for i in dialect_analysis.intents] if dialect_analysis else []
        fuzzy_notes = resolution_result.resolution_notes if resolution_result else None

        return DraftMessage(
            draft_id=draft_id,
            lead_id=lead.lead_id,
            customer_name=lead.customer_name,
            contact_channel=lead.contact_channel,
            contact_target=lead.contact_target,
            sku=item.sku if item else lead.requested_sku,
            language=lead.preferred_language,
            subject=subject,
            body=body,
            verification_report=report,
            consent_to_contact=lead.consent_to_contact,
            consent_evidence=lead.consent_evidence,
            approval_status=ApprovalStatus.PENDING_REVIEW,
            inquiry_text=lead.inquiry_text,
            detected_dialect=detected_dialect_val,
            query_intents=intents_list,
            fuzzy_match_notes=fuzzy_notes,
        )

    def _render_arabic_draft(
        self,
        lead: LeadInquiry,
        item: Optional[CatalogItem],
        resolution: Optional[ResolutionResult] = None,
    ) -> tuple[str, str]:
        """Renders Modern Standard Arabic draft grounded in verified catalog data."""
        name = lead.customer_name or "عميلنا العزيز"

        if item is None:
            # Handle ambiguous candidates clarification
            if resolution and resolution.is_ambiguous and resolution.candidates:
                top_opts = resolution.candidates[:3]
                options_str = "\n".join([f"- {c.item.title_ar} ({c.item.sku})" for c in top_opts])
                subject = "بخصوص استفساركم - توضيح المنتج المطلوب"
                body = (
                    f"مرحباً {name}،\n\n"
                    f"نشكركم على تواصلكم واهتمامكم بمنتجاتنا. بخصوص استفساركم: '{lead.inquiry_text}'.\n"
                    f"وجدنا أكثر من منتج مطابق:\n"
                    f"{options_str}\n\n"
                    f"يرجى تحديد المنتج المطلوب بدقة لنتمكن من تزويدكم بالأسعار وتفاصيل التوفر الرسمية.\n\n"
                    f"مع أطيب التحيات،\nفريق المبيعات"
                )
                return subject, body

            subject = "بخصوص استفساركم التجاري"
            body = (
                f"مرحباً {name}،\n\n"
                f"نشكركم على تواصلكم واهتمامكم بمنتجاتنا. بخصوص استفساركم: '{lead.inquiry_text}'.\n"
                f"يرجى تزويدنا برمز المنتج المطلوب (SKU) بدقة لنتمكن من تزويدكم بالأسعار وتفاصيل التوفر الرسمية.\n\n"
                f"مع أطيب التحيات،\nفريق المبيعات"
            )
            return subject, body

        subject = f"استفسار بخصوص {item.title_ar} ({item.sku})"

        if not item.in_stock:
            body = (
                f"مرحباً {name}،\n\n"
                f"نشكركم على اهتمامكم بمنتج {item.title_ar} (رمز المنتج: {item.sku}).\n"
                f"نود إفادتكم بأن هذا المنتج غير متوفر حالياً في مستودعاتنا.\n"
                f"يرجى إعلامنا إذا كنتم ترغبون في المساعدة باختيار منتج بديل آخر.\n\n"
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

    def _render_english_draft(
        self,
        lead: LeadInquiry,
        item: Optional[CatalogItem],
        resolution: Optional[ResolutionResult] = None,
    ) -> tuple[str, str]:
        """Renders professional English sales draft grounded in catalog facts."""
        name = lead.customer_name or "Valued Customer"

        if item is None:
            if resolution and resolution.is_ambiguous and resolution.candidates:
                top_opts = resolution.candidates[:3]
                options_str = "\n".join([f"- {c.item.title_en} ({c.item.sku})" for c in top_opts])
                subject = "Regarding your inquiry - Product Clarification"
                body = (
                    f"Dear {name},\n\n"
                    f"Thank you for contacting us regarding: '{lead.inquiry_text}'.\n"
                    f"We found multiple matching products in our catalog:\n"
                    f"{options_str}\n\n"
                    f"Could you please confirm the specific product SKU you are interested in so we can provide exact pricing and stock details?\n\n"
                    f"Best regards,\nSales Support Team"
                )
                return subject, body

            subject = "Regarding your inquiry"
            body = (
                f"Dear {name},\n\n"
                f"Thank you for contacting us regarding: '{lead.inquiry_text}'.\n"
                f"Could you please confirm the specific product SKU you are interested in so we can provide exact pricing and stock details?\n\n"
                f"Best regards,\nSales Support Team"
            )
            return subject, body

        subject = f"Inquiry regarding {item.title_en} ({item.sku})"

        if not item.in_stock:
            body = (
                f"Dear {name},\n\n"
                f"Thank you for your interest in {item.title_en} (SKU: {item.sku}).\n"
                f"Currently, this item is out of stock in our inventory.\n"
                f"Please let us know if we can assist you with an alternative product.\n\n"
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
