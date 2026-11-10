"""Deterministic commercial-claim verification engine for CatalogCopilot."""

from __future__ import annotations
import re
from typing import List, Optional, Tuple, Set, Dict, Any
from datetime import datetime, timezone
from .models import (
    CatalogItem,
    LeadInquiry,
    ClaimType,
    ClaimVerification,
    VerificationStatus,
    VerificationReport,
    DraftMessage,
)
from .catalog import Catalog, _ARABIC_INDIC_DIGITS, normalize_arabic_text


class CommercialClaimVerifier:
    """Deterministic verifier for commercial assertions in sales drafts.
    
    Verifies:
    1. Mandatory Customer Consent (Never send unsolicited outreach).
    2. SKU existence and text binding against authorized catalog.
    3. Stated price accuracy across ALL price assertions in the message.
    4. Stock availability with contradictory statement and negation parsing.
    5. Active promotional discount eligibility and expiry.
    6. Currency alignment.
    7. Fail-closed detection of ungrounded commercial assertions (warranties, free shipping, gifts).
    """

    # Price extraction regex patterns
    _PRICE_WITH_CURRENCY = [
        re.compile(r"(\d+(?:\.\d+)?)\s*(SAR|USD|EGP|AED|EUR|GBP|ريال|ر\.س|جنيه|درهم|دولار|يورو|باوند|euros?|pounds?|\$|€|£)", re.IGNORECASE),
        re.compile(r"(SAR|USD|EGP|AED|EUR|GBP|\$|€|£)\s*(\d+(?:\.\d+)?)", re.IGNORECASE),
    ]

    _PRICE_WITHOUT_CURRENCY = [
        re.compile(r"(?:بسعر|سعر|قيمة)\s*(\d+(?:\.\d+)?)", re.IGNORECASE),
        re.compile(r"(?:price\s*(?:is|at)?[:\s]+)(\d+(?:\.\d+)?)", re.IGNORECASE),
        re.compile(r"(?:costs?|for)\s*(\d+(?:\.\d+)?)", re.IGNORECASE),
    ]

    # Discount patterns: 10% off or خصم 10%
    _DISCOUNT_PATTERNS = [
        re.compile(r"(\d+(?:\.\d+)?)\s*%\s*(?:off|discount|خصم)?", re.IGNORECASE),
        re.compile(r"(?:خصم|تخفيض)\s*(\d+(?:\.\d+)?)\s*%", re.IGNORECASE),
    ]

    # Broad ungrounded commercial claim keywords (must fail-closed if found without catalog support)
    _UNGROUNDED_PATTERNS = [
        re.compile(r"\b(\w+\s+)?(warranty|guarantee|guaranteed|refund|return policy|free shipping|free delivery|free gift|gift|price match|lowest price|cashback)\b", re.IGNORECASE),
        re.compile(r"(ضمان|كفالة|استرجاع|استرداد|شحن مجاني|توصيل مجاني|هدية|أقل سعر|أفضل سعر|كاش باك)", re.IGNORECASE),
    ]

    # Normalized currency mapping
    _CURRENCY_NORM = {
        "SAR": "SAR", "ريال": "SAR", "ر.س": "SAR", "ريال سعودي": "SAR",
        "USD": "USD", "دولار": "USD", "$": "USD",
        "EGP": "EGP", "جنيه": "EGP", "ج.م": "EGP",
        "AED": "AED", "درهم": "AED", "د.إ": "AED",
        "EUR": "EUR", "EUROS": "EUR", "EURO": "EUR", "يورو": "EUR", "€": "EUR",
        "GBP": "GBP", "POUNDS": "GBP", "POUND": "GBP", "باوند": "GBP", "£": "GBP",
    }

    @classmethod
    def _extract_all_prices_and_currencies(cls, text: str) -> List[Tuple[float, Optional[str]]]:
        """Extracts ALL numerical prices and currencies without skipping unanchored prices."""
        clean_text = text.translate(_ARABIC_INDIC_DIGITS)
        extracted: List[Tuple[float, Optional[str]]] = []

        # 1. Price + Currency
        for pattern in cls._PRICE_WITH_CURRENCY:
            for match in pattern.finditer(clean_text):
                g1, g2 = match.groups()
                try:
                    val = float(g1)
                    curr_raw = g2.upper()
                    curr = cls._CURRENCY_NORM.get(curr_raw, cls._CURRENCY_NORM.get(g2, curr_raw))
                    extracted.append((val, curr))
                except ValueError:
                    try:
                        val = float(g2)
                        curr_raw = g1.upper()
                        curr = cls._CURRENCY_NORM.get(curr_raw, cls._CURRENCY_NORM.get(g1, curr_raw))
                        extracted.append((val, curr))
                    except ValueError:
                        continue

        # 2. Standalone price mentions
        for pattern in cls._PRICE_WITHOUT_CURRENCY:
            for match in pattern.finditer(clean_text):
                try:
                    val = float(match.group(1))
                    if not any(abs(val - ex_val) < 0.001 for ex_val, _ in extracted):
                        extracted.append((val, None))
                except (ValueError, IndexError):
                    continue

        return extracted

    @classmethod
    def _extract_discounts(cls, text: str) -> List[float]:
        """Extracts mentioned discount percentages."""
        clean_text = text.translate(_ARABIC_INDIC_DIGITS)
        discounts: List[float] = []
        for pattern in cls._DISCOUNT_PATTERNS:
            for match in pattern.finditer(clean_text):
                try:
                    discounts.append(float(match.group(1)))
                except (ValueError, IndexError):
                    continue
        return discounts

    @classmethod
    def _detect_stock_claims(cls, text: str) -> Tuple[bool, bool, bool]:
        """Detects stock assertions considering negation and contradictory statements.
        Returns: (claims_in_stock: bool, claims_out_of_stock: bool, is_contradictory: bool)
        """
        lower_en = text.lower()

        # Check explicit English negations
        en_not_out = bool(re.search(r"\b(not|isn't|is not)\s+out\s+of\s+stock\b", lower_en))
        en_not_in = bool(re.search(r"\b(not|isn't|is not|no longer)\s+(in\s+stock|available)\b", lower_en))

        has_out_phrase_en = bool(re.search(r"\b(out\s+of\s+stock|unavailable|sold\s+out)\b", lower_en)) or en_not_in

        # Positive in-stock phrases in English (excluding those consumed by negative phrases)
        clean_en_for_pos = lower_en
        for neg_term in ["out of stock", "not in stock", "not available", "no longer available", "unavailable", "sold out"]:
            clean_en_for_pos = clean_en_for_pos.replace(neg_term, " ")
        has_in_phrase_en = bool(re.search(r"\b(in\s+stock|available|on\s+hand|ready\s+to\s+ship)\b", clean_en_for_pos)) or en_not_out

        # Arabic phrases
        ar_not_out = "ليس غير متوفر" in text or "ليست غير متوفره" in text
        has_out_phrase_ar = any(term in text for term in ["غير متوفر", "غير متاح", "نفذت الكمية", "نفد من المخزون", "ليس متوفر", "لا يتوفر", "غير متواجد"])

        clean_ar_for_pos = text
        for neg_term in ["غير متوفر", "ليس متوفر", "غير متاح", "ليس متاح", "غير متواجد", "لا يتوفر", "نفذت الكمية", "نفد من المخزون"]:
            clean_ar_for_pos = clean_ar_for_pos.replace(neg_term, " ")
        has_in_phrase_ar = ar_not_out or any(term in clean_ar_for_pos for term in ["متوفر لدينا", "متوفر حاليا", "متاح للشحن", "جاهز للشحن", "متوفرة", "متوفر", "متاح"])

        # Check contradictions (asserting both in-stock and out-of-stock without negation)
        is_contra_en = has_out_phrase_en and has_in_phrase_en and not en_not_out and not en_not_in
        is_contra_ar = has_out_phrase_ar and has_in_phrase_ar and not ar_not_out

        if is_contra_en or is_contra_ar:
            return False, False, True

        claims_in = en_not_out or ar_not_out or (has_in_phrase_en and not has_out_phrase_en) or (has_in_phrase_ar and not has_out_phrase_ar)
        claims_out = en_not_in or (has_out_phrase_en and not en_not_out and not has_in_phrase_en) or (has_out_phrase_ar and not ar_not_out and not has_in_phrase_ar)

        return claims_in, claims_out, False

    def verify(
        self,
        lead: LeadInquiry,
        catalog: Catalog,
        draft_text: str,
        target_sku: Optional[str] = None,
        claimed_price: Optional[float] = None,
        claimed_stock: Optional[bool] = None,
        claimed_discount: Optional[float] = None,
    ) -> VerificationReport:
        """Deterministically verifies commercial statements in the draft against catalog truth and consent."""
        checks: List[ClaimVerification] = []
        reasons: List[str] = []
        overall_status = VerificationStatus.PASS

        # 1. Mandatory Customer Consent Guardrail
        if not lead.consent_to_contact:
            checks.append(ClaimVerification(
                claim_type=ClaimType.CONSENT,
                claimed_value=False,
                catalog_value=True,
                is_valid=False,
                notes="CRITICAL GUARDRAIL: Customer has not granted opt-in contact consent. Outreach strictly prohibited.",
            ))
            reasons.append("Consent violation: Lead opted out or has no verified contact permission.")
            overall_status = VerificationStatus.FAIL
        else:
            checks.append(ClaimVerification(
                claim_type=ClaimType.CONSENT,
                claimed_value=True,
                catalog_value=True,
                is_valid=True,
                notes=f"Verified customer opt-in consent exists (Evidence: {lead.consent_evidence}).",
            ))

        # 2. Check for Ungrounded Claims (warranties, free shipping, gifts, guarantees)
        sku = (target_sku or lead.requested_sku or "").strip().upper()
        catalog_item: Optional[CatalogItem] = catalog.get_item(sku) if sku else None

        for pattern in self._UNGROUNDED_PATTERNS:
            match = pattern.search(draft_text)
            if match:
                term = match.group(0).strip()
                # Check if catalog explicitly supports this term in descriptions
                cat_desc = f"{catalog_item.offer_description_en or ''} {catalog_item.offer_description_ar or ''}" if catalog_item else ""
                if term.lower() not in cat_desc.lower():
                    checks.append(ClaimVerification(
                        claim_type=ClaimType.UNGROUNDED_CLAIM,
                        claimed_value=term,
                        catalog_value=None,
                        is_valid=False,
                        notes=f"Draft asserts ungrounded commercial term '{term}' not supported by merchant catalog.",
                    ))
                    reasons.append(f"Ungrounded claim: '{term}' is not supported by catalog ground truth.")
                    overall_status = VerificationStatus.UNCERTAIN

        # 3. SKU Existence and Text Binding Check
        if sku:
            if catalog_item is None:
                checks.append(ClaimVerification(
                    claim_type=ClaimType.SKU_EXISTENCE,
                    claimed_value=sku,
                    catalog_value=None,
                    is_valid=False,
                    notes=f"SKU '{sku}' does not exist in authorized merchant catalog.",
                ))
                reasons.append(f"Invalid SKU '{sku}': Not found in catalog.")
                overall_status = VerificationStatus.FAIL
            else:
                checks.append(ClaimVerification(
                    claim_type=ClaimType.SKU_EXISTENCE,
                    claimed_value=sku,
                    catalog_value=sku,
                    is_valid=True,
                    notes=f"SKU '{sku}' verified in catalog ({catalog_item.title_en} / {catalog_item.title_ar}).",
                ))

                # Check if other unexpected SKUs are mentioned in the text
                sku_matches = re.findall(r"\b([A-Z0-9]{3,8}-[A-Z0-9]{2,8}-[0-9]{2,4})\b", draft_text)
                for found_sku in sku_matches:
                    if found_sku != catalog_item.sku:
                        checks.append(ClaimVerification(
                            claim_type=ClaimType.SKU_EXISTENCE,
                            claimed_value=found_sku,
                            catalog_value=catalog_item.sku,
                            is_valid=False,
                            notes=f"Conflicting SKU '{found_sku}' mentioned in text alongside target SKU '{catalog_item.sku}'.",
                        ))
                        reasons.append(f"SKU conflict: Text mentions '{found_sku}', but bound item is '{catalog_item.sku}'.")
                        overall_status = VerificationStatus.FAIL
        else:
            extracted_prices = self._extract_all_prices_and_currencies(draft_text)
            claims_in, claims_out, is_contra = self._detect_stock_claims(draft_text)
            if extracted_prices or claims_in or claims_out or claimed_price is not None:
                checks.append(ClaimVerification(
                    claim_type=ClaimType.UNGROUNDED_CLAIM,
                    claimed_value="Commercial terms present without SKU",
                    catalog_value=None,
                    is_valid=False,
                    notes="Draft asserts pricing or stock without identifying an authorized catalog SKU.",
                ))
                reasons.append("Ungrounded commercial claims: Price or stock mentioned without a valid SKU.")
                overall_status = VerificationStatus.UNCERTAIN
            else:
                checks.append(ClaimVerification(
                    claim_type=ClaimType.SKU_EXISTENCE,
                    claimed_value=None,
                    catalog_value=None,
                    is_valid=True,
                    notes="General informational inquiry without specific SKU binding.",
                ))

        # 4. Stock Availability Verification
        if catalog_item is not None:
            claims_in, claims_out, is_contra = self._detect_stock_claims(draft_text)
            if claimed_stock is not None:
                claims_in = claimed_stock
                claims_out = not claimed_stock
                is_contra = False

            if is_contra:
                checks.append(ClaimVerification(
                    claim_type=ClaimType.STOCK_AVAILABILITY,
                    claimed_value="Contradictory stock statements",
                    catalog_value=f"Quantity: {catalog_item.stock_quantity}",
                    is_valid=False,
                    notes="Draft contains contradictory availability statements (e.g. claiming both available and out of stock).",
                ))
                reasons.append("Stock ambiguity: Contradictory stock statements in message.")
                overall_status = VerificationStatus.UNCERTAIN
            elif claims_in:
                if not catalog_item.in_stock:
                    checks.append(ClaimVerification(
                        claim_type=ClaimType.STOCK_AVAILABILITY,
                        claimed_value="In Stock / Available",
                        catalog_value=f"Quantity: {catalog_item.stock_quantity}, Active: {catalog_item.is_active}",
                        is_valid=False,
                        notes=f"Draft claims product is in stock, but catalog shows {catalog_item.stock_quantity} available.",
                    ))
                    reasons.append(f"Stock mismatch: Claimed available, but SKU '{sku}' is out of stock.")
                    overall_status = VerificationStatus.FAIL
                else:
                    checks.append(ClaimVerification(
                        claim_type=ClaimType.STOCK_AVAILABILITY,
                        claimed_value="In Stock",
                        catalog_value=f"Quantity: {catalog_item.stock_quantity}",
                        is_valid=True,
                        notes=f"Product availability verified ({catalog_item.stock_quantity} units available).",
                    ))
            elif claims_out:
                if catalog_item.in_stock:
                    checks.append(ClaimVerification(
                        claim_type=ClaimType.STOCK_AVAILABILITY,
                        claimed_value="Out of Stock",
                        catalog_value=f"Quantity: {catalog_item.stock_quantity}",
                        is_valid=False,
                        notes=f"Draft claims product is unavailable, but catalog has {catalog_item.stock_quantity} units in stock.",
                    ))
                    reasons.append(f"Stock mismatch: Claimed out of stock, but SKU '{sku}' has active inventory.")
                    overall_status = VerificationStatus.FAIL

        # 5. Price & Currency Verification
        if catalog_item is not None:
            extracted_prices = self._extract_all_prices_and_currencies(draft_text)
            if claimed_price is not None:
                extracted_prices.append((claimed_price, catalog_item.currency))

            allowed_prices = {
                round(catalog_item.price, 2),
                round(catalog_item.effective_price, 2),
            }

            for price_val, curr_val in extracted_prices:
                price_rounded = round(price_val, 2)
                if price_rounded not in allowed_prices:
                    checks.append(ClaimVerification(
                        claim_type=ClaimType.PRICE,
                        claimed_value=price_rounded,
                        catalog_value=f"Base: {catalog_item.price}, Effective: {catalog_item.effective_price}",
                        is_valid=False,
                        notes=f"Price claim {price_rounded} does not match catalog base price ({catalog_item.price}) or promotional price ({catalog_item.effective_price}).",
                    ))
                    reasons.append(f"Price mismatch: Stated {price_rounded}, catalog allows {sorted(list(allowed_prices))}.")
                    overall_status = VerificationStatus.FAIL
                else:
                    checks.append(ClaimVerification(
                        claim_type=ClaimType.PRICE,
                        claimed_value=price_rounded,
                        catalog_value=price_rounded,
                        is_valid=True,
                        notes=f"Price {price_rounded} matches verified catalog price.",
                    ))

                # Check currency alignment if present
                if curr_val is not None:
                    norm_extracted_curr = self._CURRENCY_NORM.get(curr_val, curr_val)
                    if norm_extracted_curr != catalog_item.currency:
                        checks.append(ClaimVerification(
                            claim_type=ClaimType.CURRENCY,
                            claimed_value=norm_extracted_curr,
                            catalog_value=catalog_item.currency,
                            is_valid=False,
                            notes=f"Currency mismatch: Draft mentions '{norm_extracted_curr}', catalog item requires '{catalog_item.currency}'.",
                        ))
                        reasons.append(f"Currency mismatch: '{norm_extracted_curr}' vs '{catalog_item.currency}'.")
                        overall_status = VerificationStatus.FAIL
                    else:
                        checks.append(ClaimVerification(
                            claim_type=ClaimType.CURRENCY,
                            claimed_value=norm_extracted_curr,
                            catalog_value=catalog_item.currency,
                            is_valid=True,
                            notes=f"Currency '{catalog_item.currency}' matches catalog specification.",
                        ))

        # 6. Discount / Promotion Verification & Expiry
        if catalog_item is not None:
            extracted_discounts = self._extract_discounts(draft_text)
            if claimed_discount is not None:
                extracted_discounts.append(claimed_discount)

            # Check promotion expiry if validity date is set
            if catalog_item.offer_valid_until:
                try:
                    expiry_dt = datetime.fromisoformat(catalog_item.offer_valid_until.replace("Z", "+00:00"))
                    if datetime.now(timezone.utc) > expiry_dt:
                        if extracted_discounts or catalog_item.discount_percent > 0:
                            checks.append(ClaimVerification(
                                claim_type=ClaimType.DISCOUNT_OFFER,
                                claimed_value="Active promotion",
                                catalog_value=f"Expired on {catalog_item.offer_valid_until}",
                                is_valid=False,
                                notes="Promotional offer has expired according to catalog offer_valid_until date.",
                            ))
                            reasons.append("Expired promotion: Offer date is in the past.")
                            overall_status = VerificationStatus.FAIL
                except ValueError:
                    pass

            for discount_val in extracted_discounts:
                discount_rounded = round(discount_val, 1)
                allowed_discount = round(catalog_item.discount_percent, 1)
                if abs(discount_rounded - allowed_discount) > 0.05:
                    checks.append(ClaimVerification(
                        claim_type=ClaimType.DISCOUNT_OFFER,
                        claimed_value=f"{discount_rounded}%",
                        catalog_value=f"{allowed_discount}%",
                        is_valid=False,
                        notes=f"Unauthorized discount: Draft promises {discount_rounded}%, but active catalog offer is {allowed_discount}%.",
                    ))
                    reasons.append(f"Unauthorized discount: Claimed {discount_rounded}%, authorized is {allowed_discount}%.")
                    overall_status = VerificationStatus.FAIL
                else:
                    checks.append(ClaimVerification(
                        claim_type=ClaimType.DISCOUNT_OFFER,
                        claimed_value=f"{discount_rounded}%",
                        catalog_value=f"{allowed_discount}%",
                        is_valid=True,
                        notes=f"Discount of {discount_rounded}% matches active authorized promotion.",
                    ))

        # Fail-closed summary determination:
        # 1. Hard failure (consent violation, wrong price, out-of-stock violation, unauthorized discount, invalid SKU) -> FAIL
        # 2. Ungrounded / ambiguous commercial claim / contradictory stock without hard conflict -> UNCERTAIN (Fail-Closed)
        # 3. All checks verified against catalog ground truth and consent confirmed -> PASS
        has_hard_fail = any(
            not c.is_valid
            and c.claim_type != ClaimType.UNGROUNDED_CLAIM
            and not (c.claim_type == ClaimType.STOCK_AVAILABILITY and is_contra)
            for c in checks
        )
        has_uncertain = (
            any(c.claim_type == ClaimType.UNGROUNDED_CLAIM for c in checks)
            or is_contra
            or (overall_status == VerificationStatus.UNCERTAIN)
        )

        if has_hard_fail:
            overall_status = VerificationStatus.FAIL
        elif has_uncertain:
            overall_status = VerificationStatus.UNCERTAIN
        else:
            overall_status = VerificationStatus.PASS

        return VerificationReport(
            status=overall_status,
            checks=checks,
            reasons=reasons,
            requires_human_intervention=(overall_status != VerificationStatus.PASS),
        )
