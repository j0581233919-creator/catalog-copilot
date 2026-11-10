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

    # Broad ungrounded commercial & specification claim keywords (must fail-closed if found without catalog support)
    _UNGROUNDED_PATTERNS = [
        re.compile(r"\b(\w+\s+)?(warranty|guarantee|guaranteed|refund|return policy|free shipping|free delivery|free gift|gift|price match|lowest price|best price|cashback)\b", re.IGNORECASE),
        re.compile(r"(ضمان|كفالة|استرجاع|استرداد|شحن مجاني|توصيل مجاني|هدية|أقل سعر|أفضل سعر|كاش باك)", re.IGNORECASE),
        re.compile(r"\b(waterproof|water-resistant|dustproof|drop-proof|battery|battery life|wireless|bluetooth|fast charging|charger included|accessories included|titanium|leather|cotton|organic|resolution|camera|megapixel|storage|ram|processor|genuine|original|certified|unlocked)\b", re.IGNORECASE),
        re.compile(r"(مقاوم للماء|ضد الماء|مقاوم للغبار|مقاوم للصدمات|عمر البطارية|بطارية|لاسلكي|بلوتوث|شحن سريع|شاحن مرفق|ملحقات مرفقة|تيتانيوم|جلد طبيعي|قطن|عضوي|دقة الشاشة|كاميرا|ذاكرة|معالج|أصلي|معتمد|مفتوح الشبكة)", re.IGNORECASE),
    ]

    _PREDICATE_ASSERTIONS = [
        re.compile(r"\b(?:is|are)\s+([a-zA-Z0-9_\-]+)\b", re.IGNORECASE),
        re.compile(r"(?:مصنوع من|يتميز بـ|مزود بـ)\s+([\u0621-\u064A0-9_\-]+)"),
    ]

    # Standard benign predicates allowed in polite sales messages
    _BENIGN_PREDICATES = {
        "currently", "available", "unavailable", "ready", "happy", "pleased", "glad",
        "here", "welcomed", "confirmed", "out", "in", "متوفر", "غير", "متاح", "سعداء", "جاهز",
    }

    _TRANSACTIONAL_WORDS_EN = {
        "dear", "hello", "hi", "good", "morning", "afternoon", "evening",
        "thank", "thanks", "you", "for", "contacting", "us", "inquiring",
        "about", "your", "interest", "in", "item", "product", "sku",
        "regarding", "inquiry", "is", "are", "currently", "stock", "out",
        "of", "inventory", "at", "the", "price", "regular", "with", "an",
        "active", "discount", "off", "please", "let", "know", "if",
        "would", "like", "to", "proceed", "order", "or", "have",
        "additional", "questions", "question", "asking", "asked", "assist", "alternative", "could",
        "confirm", "specific", "so", "we", "can", "provide", "exact",
        "pricing", "and", "details", "best", "regards", "customer",
        "sales", "team", "support", "valued", "a", "an", "the", "this",
        "that", "it", "on", "in", "by", "from", "sar", "usd", "aed",
        "egp", "eur", "gbp", "costs", "available", "unavailable",
        "ready", "happy", "pleased", "glad", "not", "only", "special",
        "deal", "request", "help", "here", "as", "be", "been", "was", "were",
        "our", "these", "those", "will", "shall", "should", "may", "might",
    }

    _TRANSACTIONAL_WORDS_AR = {
        "مرحبا", "مرحباً", "عزيزنا", "عزيزي", "عزيزتي", "عميلنا", "العزيز",
        "نشكركم", "شكر", "شكرا", "شكراً", "الشكر", "على", "تواصلكم", "اهتمام",
        "اهتمامكم", "واهتمامكم", "منتج", "منتجات", "منتجاتنا", "بمنتجاتنا",
        "بمنتج", "بخصوص", "استفسار", "استفساركم", "استفساراتكم", "يسعدنا", "الرد",
        "رمز", "المنتج", "متوفر", "لدينا", "حاليا", "حالياً", "غير", "في",
        "مستودعات", "مستودعاتنا", "بسعر", "سعر", "السعر", "مع", "خصم",
        "الأساسي", "إذا", "كنتم", "ترغبون", "تأكيد", "الطلب", "طلب", "أو",
        "لديكم", "أي", "آخر", "فنحن", "سعداء", "بخدمتكم", "خدمتكم", "يرجى",
        "إعلامنا", "المساعدة", "مساعدة", "باختيار", "اختيار", "بديل", "تزويدنا",
        "بدقة", "دقة", "لنتمكن", "نتمكن", "من", "تفاصيل", "وتفاصيل", "التوفر",
        "توفر", "الرسمية", "أطيب", "التحيات", "تحيات", "تحياتنا", "خالص",
        "تقدير", "التقدير", "والتقدير", "فريق", "المبيعات", "مبيعات", "خدمة",
        "العملاء", "والمبيعات", "ريال", "درهم", "جنيه", "دولار", "يورو",
        "باوند", "هو", "هي", "هذا", "هذه", "ذلك", "تلك", "ما", "هل", "عن",
        "إلى", "و", "أو", "لا", "نعم", "عرض", "شراء", "السلام", "عليكم",
        "ورحمة", "الله", "وبركاته", "أهلاً", "اهلا", "وسهلاً", "وسهلا", "تحية", "طيبة",
        "نود", "إفادتكم", "افادتكم", "بأن", "بان", "المطلوب", "مطلوب", "أسعار",
        "الأسعار", "بالأسعار", "بالاسعار", "تزويدكم", "أن", "إن", "كان", "كانت",
        "يكون", "تكون",
    }

    # Normalized currency mapping
    _CURRENCY_NORM = {
        "SAR": "SAR", "ريال": "SAR", "ر.س": "SAR", "ريال سعودي": "SAR",
        "USD": "USD", "دولار": "USD", "$": "USD",
        "EGP": "EGP", "جنيه": "EGP", "ج.م": "EGP",
        "AED": "AED", "درهم": "AED", "د.إ": "AED",
        "EUR": "EUR", "EUROS": "EUR", "EURO": "EUR", "يورو": "EUR", "€": "EUR",
        "GBP": "GBP", "POUNDS": "GBP", "POUND": "GBP", "باوند": "GBP", "£": "GBP",
    }

    _INTERROGATIVE_STARTERS_EN = re.compile(
        r"^(?:"
        r"is|are|was|were|am|do|does|did|can|could|will|would|shall|should|may|might|must|have|has|had|"
        r"what|which|who|whom|whose|where|when|why|how|"
        r"is\s+it\s+(?:true|possible|correct)|can\s+you\s+(?:tell|confirm|clarify)|do\s+you\s+(?:have|know)|could\s+you\s+(?:please\s+)?(?:tell|confirm|clarify)"
        r")\b",
        re.IGNORECASE,
    )

    _INTERROGATIVE_STARTERS_AR = re.compile(
        r"^(?:"
        r"هل|[أإا]|ما|ماذا|من|متى|[أإا]ين|كيف|كم|[أإا]ي|[أإا]يها|[أإا]ينما|لماذا|لم|"
        r"هل\s+صحيح|هل\s+من\s+الممكن|هل\s+يمكن|[أإا]يمكن|هل\s+تتوفر|هل\s+يوجد|هل\s+يتوفر|[أإا]رجو\s+توضيح|هل\s+بالإمكان"
        r")(?:\s+|$)",
    )

    _TRUTH_EVALUATING_STARTERS = [
        re.compile(r"^(?:is\s+it\s+(?:true|correct|accurate)(?:\s+that)?|can\s+you\s+confirm\s+(?:if|whether))\s+", re.IGNORECASE),
        re.compile(r"^(?:هل\s+صحيح\s+أن|هل\s+من\s+الصحيح\s+أن)\s+"),
    ]

    _INQUIRY_TRANSACTIONAL_WORDS_EN = {
        "i", "you", "we", "they", "he", "she", "it", "this", "that", "these", "those",
        "a", "an", "the", "my", "your", "our", "their", "any", "some", "all",
        "is", "are", "was", "were", "am", "be", "been", "being",
        "do", "does", "did", "can", "could", "will", "would", "shall", "should", "may", "might", "must",
        "have", "has", "had",
        "what", "which", "who", "whom", "whose", "where", "when", "why", "how", "much", "many",
        "price", "cost", "costs", "pricing", "rate", "rates",
        "stock", "available", "availability", "unavailable", "in", "out", "of", "ready",
        "discount", "offer", "deal", "special", "promotion", "sale", "percent", "off",
        "buy", "purchase", "order", "get", "shipping", "delivery", "deliver", "ship", "fast",
        "hello", "hi", "dear", "please", "thank", "thanks", "item", "product", "unit", "units",
        "sar", "usd", "aed", "egp", "eur", "gbp", "riyal", "riyals", "dollar", "dollars",
        "for", "to", "at", "on", "with", "from", "by", "about",
    }

    _INQUIRY_TRANSACTIONAL_WORDS_AR = {
        "هل", "ما", "ماذا", "من", "متى", "أين", "كيف", "كم", "أي", "أيها", "أينما", "لماذا", "لم",
        "هو", "هي", "هم", "هن", "هذا", "هذه", "ذلك", "تلك", "هؤلاء", "أنا", "نحن", "أنت", "أنتم",
        "متوفر", "متوفرة", "متاح", "متاحة", "توفر", "التوفر", "غير", "جاهز", "جاهزة",
        "سعر", "السعر", "أسعار", "الأسعار", "قيمة", "القيمة", "تكلفة", "التكلفة", "بكم",
        "خصم", "الخصم", "تخفيض", "عرض", "العرض", "عروض", "العروض", "خاص", "خاصة",
        "شراء", "طلب", "الطلب", "توصيل", "التوصيل", "شحن", "الشحن", "شحنها", "توصيلها",
        "منتج", "المنتج", "منتجات", "المنتجات", "صنف", "الصنف", "أصناف", "الأصناف",
        "قطعة", "قطع", "حبة", "حبات", "وحدة", "وحدات",
        "ريال", "درهم", "جنيه", "دولار", "يورو", "باوند",
        "مرحبا", "مرحباً", "أهلاً", "اهلا", "وسهلاً", "وسهلا", "شكرا", "شكراً", "لو", "سمحت", "من", "فضلك",
        "في", "من", "إلى", "على", "عن", "مع", "بـ", "لـ", "كـ",
    }

    _CLAUSAL_CONNECTIVES_EN = re.compile(
        r"\b(?:"
        r"and|but|yet|so|or|nor|plus|besides|"
        r"if|unless|provided(?:\s+that)?|supposing(?:\s+that)?|assuming(?:\s+that)?|in\s+case|"
        r"after|before|when|whenever|while|whilst|since|until|till|once|as\s+soon\s+as|"
        r"because|as|for|inasmuch\s+as|so\s+that|in\s+order\s+that|"
        r"although|though|even\s+though|even\s+if|whereas|despite|in\s+spite\s+of|notwithstanding|"
        r"given(?:\s+that)?|considering(?:\s+that)?|knowing(?:\s+that)?|seeing\s+that|"
        r"as\s+well\s+as|in\s+addition\s+to|lest|where|wherever|whether|who|whom|whose|which|that"
        r")\b",
        re.IGNORECASE,
    )

    _CLAUSAL_CONNECTIVES_AR = re.compile(
        r"\b(?:"
        r"إذا|اذا|لو|إن|ان|لولا|طالما|"
        r"بعد|بعدما|بعد\s+أن|قبل|قبلما|قبل\s+أن|حين|حينما|عند|عندما|وقت|وقتما|منذ|حتى|إلى\s+أن|الى\s+أن|"
        r"لأن|لان|بسبب|كون|لكون|نظرا|نظراً|نظرا\s+لأن|نظراً\s+لأن|حيث|حيث\s+أن|حيثما|إذ|اذ|إذ\s+أن|اذ\s+أن|"
        r"مع\s+أن|مع\s+ان|رغم|رغم\s+أن|رغم\s+ان|بالرغم\s+من|غير\s+أن|غير\s+ان|إلا\s+أن|الا\s+أن|"
        r"لكن|ولكن|بيد\s+أن|بينما|علماً\s+أن|علما\s+أن|مع\s+العلم\s+أن|بالإضافة\s+إلى|بالاضافة\s+الى|"
        r"ثم|أم|ام|بل|الذي|التي|الذين|اللواتي|اللاتي"
        r")\b|\s+(?:و|أو|او|ثم|أم|ام|بل|لكن)\s+",
    )

    _SECONDARY_VERBS_EN = re.compile(
        r"\b(?:"
        r"is|are|was|were|am|be|been|being|"
        r"have|has|had|having|"
        r"do|does|did|"
        r"can|could|will|would|shall|should|may|might|must|"
        r"become|becomes|became|becoming|"
        r"contain|contains|contained|containing|"
        r"cause|causes|caused|causing|"
        r"emit|emits|emitted|emitting|"
        r"include|includes|included|including|"
        r"make|makes|made|making|"
        r"cost|costs|costing"
        r")\b",
        re.IGNORECASE,
    )

    _PREPOSITIONS_EN = {
        "to", "for", "with", "by", "from", "in", "at", "on", "about",
        "against", "of", "into", "through", "over", "under", "via", "without",
    }
    _VERB_SUFFIXES_EN = re.compile(r"(?:ed|ing)$", re.IGNORECASE)
    _EXTRA_VERBS_EN = {
        "cause", "causes", "caused", "causing", "leads", "leading", "kills", "killing", "harms", "harming",
        "contain", "contains", "contained", "containing", "known", "proven", "made", "linked", "associated",
        "become", "becomes", "became", "becoming", "have", "has", "had", "is", "are", "was", "were", "do", "does", "did",
    }

    _ADJECTIVE_PREDICATES_AR = {"أصلي", "غير أصلي", "مقلد", "سام", "مغشوش", "ضار", "مسرطن"}
    _PREPOSITIONS_AR = {"في", "من", "إلى", "الى", "عن", "مع", "لـ", "بـ", "بين", "حول", "ضد"}
    _BANNED_AR_WORDS = re.compile(
        r"\b(?:"
        r"يسبب|تسبب|يؤدي|تؤدي|يضر|تضر|ينتج|تنتج|يقتل|تقتل|يتلف|تتلف|"
        r"سبب|أدى|ادى|ضر|أضر|اضر|نتج|قتل|اتلف|أحدث|احدث|"
        r"خطير|خطيرة|سام|سامة|ضار|ضارة|مسرطن|مسرطنة|قاتل|قاتلة|مغشوش|مغشوشة|مضر|مضرة"
        r")\b"
    )

    @classmethod
    def _is_valid_truth_evaluating_complement(cls, body: str, allowed_tokens: Optional[Set[str]] = None) -> bool:
        """Validates that a truth-evaluating inquiry consists strictly of a single, unadorned
        complement proposition regarding the catalog product, without conditional, temporal,
        causal, or coordinate secondary assertion clauses, and with zero secondary predicates.
        """
        # Match English starter
        m_en = cls._TRUTH_EVALUATING_STARTERS[0].match(body)
        if m_en:
            comp = body[m_en.end():].strip()
            # Must not contain delimiters, dashes, or clausal connectives
            if re.search(r"[\.!\;؛:?\n\r,،/\\|~^—–&＆@#$%\*]", comp) or re.search(r"[—–]|--|(?:\s-)|(?:-\s)", comp):
                return False
            if cls._CLAUSAL_CONNECTIVES_EN.search(comp):
                return False

            # Must anchor to target product subject:
            subj_m = re.match(
                r"^(?:(?:the|this|our)\s+)?(?:sku[-_ ]?[a-z0-9]+|product|item|model|unit|device|it|this)\b",
                comp,
                re.IGNORECASE,
            )
            sku_m = None
            if not subj_m and allowed_tokens:
                for tok in allowed_tokens:
                    if tok and re.match(r"^" + re.escape(tok) + r"\b", comp, re.IGNORECASE):
                        sku_m = tok
                        break
            if not subj_m and not sku_m:
                return False

            subj_len = len(comp[: subj_m.end() if subj_m else len(sku_m)])
            rest = comp[subj_len:].strip()

            # Rest must begin with exactly ONE verb phrase
            verb_m = re.match(
                r"^(?:(?:is|are|was|were)\s+(?:not\s+)?(?:made\s+(?:of|with|from)\s+|composed\s+of\s+|built\s+with\s+)?|"
                r"(?:contains?|has|have|includes?|comes\s+with|uses?|emits?|causes?)\s+(?:not\s+)?)\b",
                rest,
                re.IGNORECASE,
            )
            if not verb_m:
                return False

            verb_matched = verb_m.group(0).lower()
            claim_phrase = rest[verb_m.end():].strip()
            if not claim_phrase:
                return False

            words = claim_phrase.split()
            # Copular verb validation
            if (verb_matched.startswith("is") or verb_matched.startswith("are") or
                    verb_matched.startswith("was") or verb_matched.startswith("were")):
                if "made" in verb_matched:
                    if len(words) != 1:
                        return False
                else:
                    if len(words) == 1:
                        pass
                    elif len(words) == 2 and words[0].lower() in ("a", "an"):
                        pass
                    else:
                        return False
            else:
                # Transitive verb validation: at most 2 words, or 3 if starting with an article
                if len(words) > 3:
                    return False
                if len(words) == 3 and words[0].lower() not in ("a", "an", "the"):
                    return False

            for w in words:
                w_lower = w.lower()
                if w_lower in cls._PREPOSITIONS_EN or w_lower in cls._EXTRA_VERBS_EN:
                    return False
                if len(w_lower) > 3 and cls._VERB_SUFFIXES_EN.search(w_lower) and w_lower not in ("red", "bed", "led"):
                    return False

            return True

        # Match Arabic starter
        m_ar = cls._TRUTH_EVALUATING_STARTERS[1].match(body)
        if m_ar:
            comp = body[m_ar.end():].strip()
            # Must not contain delimiters, dashes, or clausal connectives
            if re.search(r"[\.!\;؛:?\n\r,،/\\|~^—–&＆@#$%\*]", comp) or re.search(r"[—–]|--|(?:\s-)|(?:-\s)", comp):
                return False
            if cls._CLAUSAL_CONNECTIVES_AR.search(comp):
                return False

            # Watch out for proclitic "و" attached to words (e.g. والمنتج, وسام)
            if re.search(r"(?:^|\s)و(?:إذا|اذا|لو|إن|ان|بعد|قبل|حين|عند|المنتج|الصنف|السلعة|هو|هي|هذا|هذه|[^\s]{2,})\b", comp):
                return False

            subj_pattern = r"(?:sku[-_ ]?[a-z0-9]+|المنتج|الصنف|السلعة|هو|هي|هذا|هذه|البضاعة)"
            pred_pattern = r"(?:يحتوي|تحتوي|يحوي|تحوي|يشمل|تشمل|به|بها|فيه|فيها|مصنوع\s+من|مصنوعة\s+من|أصلي|غير\s+أصلي|مقلد|سام|مغشوش|ضار|مسرطن)"

            # Check SVO
            svo_m = re.match(r"^(" + subj_pattern + r")\s+(" + pred_pattern + r")(?:\s+(.+))?$", comp, re.IGNORECASE)
            # Check VSO
            vso_m = re.match(r"^(" + pred_pattern + r")\s+(" + subj_pattern + r")(?:\s+(.+))?$", comp, re.IGNORECASE)

            # Also check if allowed_tokens has Arabic names
            allowed_subj_m = None
            if not svo_m and not vso_m and allowed_tokens:
                for tok in allowed_tokens:
                    if tok and re.match(r"^" + re.escape(tok) + r"\b", comp, re.IGNORECASE):
                        allowed_subj_m = tok
                        break
                if allowed_subj_m:
                    rest_ar = comp[len(allowed_subj_m):].strip()
                    svo_m = re.match(r"^(" + pred_pattern + r")(?:\s+(.+))?$", rest_ar, re.IGNORECASE)

            if not svo_m and not vso_m:
                return False

            pred_matched = svo_m.group(2) if svo_m and not allowed_subj_m else (vso_m.group(1) if vso_m else (svo_m.group(1) if allowed_subj_m else ""))
            claim_part = (svo_m.group(3) if (svo_m and svo_m.lastindex and svo_m.lastindex >= 3)
                          else (vso_m.group(3) if (vso_m and vso_m.lastindex and vso_m.lastindex >= 3)
                                else (svo_m.group(2) if (allowed_subj_m and svo_m and svo_m.lastindex and svo_m.lastindex >= 2) else None)))

            claim_clean = claim_part.strip() if claim_part else ""
            if pred_matched in cls._ADJECTIVE_PREDICATES_AR:
                return claim_clean == ""

            words = claim_clean.split()
            if not words:
                return False
            if len(words) == 1:
                w = words[0]
                if w in cls._PREPOSITIONS_AR or cls._BANNED_AR_WORDS.search(w):
                    return False
                return True
            elif len(words) == 2:
                if words[0] != "على":
                    return False
                w = words[1]
                if w in cls._PREPOSITIONS_AR or cls._BANNED_AR_WORDS.search(w):
                    return False
                return True
            return False

        return False

    @classmethod
    def _is_pure_interrogative_clause(cls, text: str, allowed_tokens: Optional[Set[str]] = None) -> bool:
        """Determines if a clause is strictly an interrogative question without declarative assertions.
        Must end with ? or ؟, contain no internal sentence/clause boundaries or clausal connectives,
        and either be an explicit truth-evaluating question or have all content words grounded in catalog/transactional vocabulary.
        """
        t = text.strip()
        if not t or not re.search(r"[\?؟]$", t):
            return False

        body = t[:-1].strip()
        # Must not contain internal punctuation that divides sentences or clauses:
        # periods, exclamation, semicolons, colons, slashes, backslashes, pipes, question marks, commas, newlines, ampersands
        if re.search(r"[\.!\;؛:?\n\r,،/\\|~^—–&＆]", body):
            return False

        # Must not contain dash boundaries (em-dash, en-dash, double dash, or hyphen with whitespace)
        if re.search(r"[—–]|--|(?:\s-)|(?:-\s)", body):
            return False

        # Case A: Explicit truth-evaluating inquiry question (e.g. 'Is it true that SKU-A contains asbestos?')
        is_truth_evaluating = any(p.search(body) for p in cls._TRUTH_EVALUATING_STARTERS)
        if is_truth_evaluating:
            return cls._is_valid_truth_evaluating_complement(body, allowed_tokens=allowed_tokens)

        # Must not contain clausal connectives that introduce subordinate or coordinate assertion clauses
        if cls._CLAUSAL_CONNECTIVES_EN.search(body) or cls._CLAUSAL_CONNECTIVES_AR.search(body):
            return False

        # Case B: Standard product inquiry question (e.g. 'Is it available?', 'Is SKU-A in stock?')
        is_en = bool(cls._INTERROGATIVE_STARTERS_EN.match(body))
        is_ar = bool(cls._INTERROGATIVE_STARTERS_AR.match(body))
        if not (is_en or is_ar):
            return False

        # Whitelist verification: all content words must be grounded in catalog or inquiry transactional vocabulary
        if allowed_tokens is not None:
            allowed = allowed_tokens | cls._INQUIRY_TRANSACTIONAL_WORDS_EN | cls._INQUIRY_TRANSACTIONAL_WORDS_AR
            clean_body = body.translate(_ARABIC_INDIC_DIGITS)
            for w in re.findall(r"[\w\-]+", clean_body):
                w_lower = w.lower()
                if w.isdigit() or re.match(r"^\d+(?:\.\d+)?$", w):
                    continue
                if w_lower in cls._CURRENCY_NORM or w.upper() in cls._CURRENCY_NORM:
                    continue
                if len(w_lower) <= 1:
                    continue
                w_norm = normalize_arabic_text(w_lower) if re.search(r"[\u0621-\u064A]", w) else w_lower
                w_variants = {w_lower, w_norm}
                if re.search(r"[\u0621-\u064A]", w):
                    for prefix in ("وال", "بال", "لل", "ال", "و", "ب", "ل"):
                        if w_norm.startswith(prefix) and len(w_norm) > len(prefix) + 1:
                            w_variants.add(w_norm[len(prefix):])
                if not any(v in allowed for v in w_variants):
                    # Unverified content word in product question -> assertion-bearing -> fail closed!
                    return False

        return True

    @classmethod
    def _extract_pure_interrogative_clauses(cls, text: str, allowed_tokens: Optional[Set[str]] = None) -> List[str]:
        """Extracts genuine, isolated interrogative question clauses from customer text."""
        valid_clauses = []
        pattern = (
            r"(?:^|[\.!\;؛:\n\r,،/\\|~^—–&＆]|--|(?:\s-)|(?:-\s)"
            r"|\b(?:and|but|yet|so|or|plus|besides|as|since|because|although|though|even\s+though|given|while|whilst|when|whereas)\b"
            r"|\b(?:مع\s+أن|رغم\s+أن|بما\s+أن|حيث\s+أن|لأن|لكن|ولكن)\b|\s+(?:و|أو|لكن)\s+)"
            r"\s*([^.!?؛;:\n\r,،/\\|~^—–&＆]+?[\?؟])"
        )
        for match in re.finditer(pattern, text, re.IGNORECASE):
            candidate = match.group(1).strip()
            if cls._is_pure_interrogative_clause(candidate, allowed_tokens=allowed_tokens):
                valid_clauses.append(candidate)
        return valid_clauses

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

    @staticmethod
    def _parse_iso_date(val: str) -> datetime:
        s = str(val).strip()
        if len(s) == 10 and s.count("-") == 2:
            dt = datetime.fromisoformat(s)
        else:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

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
        is_contra = False

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
        elif not (lead.consent_evidence and lead.consent_evidence.strip()):
            checks.append(ClaimVerification(
                claim_type=ClaimType.CONSENT,
                claimed_value="Missing documented evidence",
                catalog_value="Auditable opt-in evidence required",
                is_valid=False,
                notes="CRITICAL GUARDRAIL: Customer opt-in flag is True, but no documented consent evidence was provided. Outreach prohibited without verifiable evidence.",
            ))
            reasons.append("Consent violation: Missing auditable opt-in evidence.")
            overall_status = VerificationStatus.FAIL
        else:
            checks.append(ClaimVerification(
                claim_type=ClaimType.CONSENT,
                claimed_value=True,
                catalog_value=True,
                is_valid=True,
                notes=f"Verified customer opt-in consent exists (Evidence: {lead.consent_evidence}).",
            ))

        # 2. Check for Ungrounded Claims (warranties, free shipping, gifts, guarantees, specs)
        sku = (target_sku or lead.requested_sku or "").strip().upper()
        catalog_item: Optional[CatalogItem] = catalog.get_item(sku) if sku else None

        cat_desc = ""
        if catalog_item:
            cat_desc = f"{catalog_item.title_en} {catalog_item.title_ar} {catalog_item.category} {catalog_item.offer_description_en or ''} {catalog_item.offer_description_ar or ''}".lower()

        # Comprehensive vocabulary and assertion grounding check:
        # Every content word in the draft must be grounded in catalog item data or transactional boilerplate.
        allowed_tokens = set(self._TRANSACTIONAL_WORDS_EN) | set(self._TRANSACTIONAL_WORDS_AR)
        if catalog_item:
            catalog_raw = f"{catalog_item.sku} {catalog_item.title_en} {catalog_item.title_ar} {catalog_item.category} {catalog_item.offer_description_en or ''} {catalog_item.offer_description_ar or ''}"
            allowed_tokens.update(t.lower() for t in re.findall(r"[\w\-]+", catalog_raw))
            allowed_tokens.update(normalize_arabic_text(t) for t in re.findall(r"[\u0621-\u064A]+", catalog_raw))

        if lead.customer_name:
            allowed_tokens.update(t.lower() for t in re.findall(r"[\w\-]+", lead.customer_name))
            allowed_tokens.update(normalize_arabic_text(t) for t in re.findall(r"[\u0621-\u064A]+", lead.customer_name))

        # Strict validation of customer inquiry quotations:
        # A merchant draft may quote a customer's inquiry only when:
        # 1. The inquiry exists, and the quoted text is the exact inquiry or a full interrogative question clause.
        # 2. If the customer inquiry contains an interrogative question (? or ؟), the quote MUST preserve that interrogative character (? or ؟).
        # 3. The quote is immediately preceded by an explicit attribution phrase (e.g., 'regarding your question', 'in response to your inquiry', etc.).
        # 4. The draft contains grounded merchant response text answering the customer.
        # Any quoted text that does not meet these criteria is treated as an assertion made by the merchant
        # and MUST be grounded in catalog facts.
        quote_pattern = re.compile(r'(?<!\w)[\'\"«»“”](.+?)[\'\"«»“”](?!\w)')
        inquiry_raw = (lead.inquiry_text or "").strip()
        inquiry_clean = re.sub(r"\s+", " ", inquiry_raw).strip()
        is_inquiry_question = bool(re.search(r"[\?؟]", inquiry_clean))
        valid_inquiry_clauses = self._extract_pure_interrogative_clauses(inquiry_clean, allowed_tokens=allowed_tokens) if is_inquiry_question else []

        attribution_prefixes = [
            re.compile(r"(?:regarding|about|for|answering|in response to)\s+(?:your\s+)?(?:question|inquiry|inquiries)\s*[:,\-]?\s*$", re.IGNORECASE),
            re.compile(r"(?:thank\s+you\s+for\s+(?:asking|inquiring|contacting\s+us\s+about))\s*[:,\-]?\s*$", re.IGNORECASE),
            re.compile(r"(?:بخصوص|رداً على|إجابة على|نشكركم على)\s+(?:استفساركم|سؤالكم|استفساراتكم)\s*[:،\-]?\s*$"),
        ]

        def _strip_if_valid_quote(match: re.Match) -> str:
            span_content = match.group(1).strip()
            span_clean = re.sub(r"\s+", " ", span_content).strip()

            if not inquiry_clean or len(span_clean) < 3:
                return f" {span_content} "

            # 1. The quoted text MUST be an isolated pure interrogative clause (no declarative assertions, no mixed clauses)
            if not self._is_pure_interrogative_clause(span_clean, allowed_tokens=allowed_tokens):
                return f" {span_content} "

            # 2. Must match an isolated pure interrogative question clause extracted from customer inquiry
            matches_inquiry = any(span_clean.lower() == c.lower() for c in valid_inquiry_clauses)
            if not matches_inquiry:
                return f" {span_content} "

            # 3. Must be immediately preceded by attribution framing in the draft text
            preceding_text = draft_text[:match.start()].strip()
            last_tokens = " ".join(preceding_text.split()[-8:]) if preceding_text else ""
            has_immediate_attribution = any(p.search(last_tokens) for p in attribution_prefixes)
            if not has_immediate_attribution:
                return f" {span_content} "

            # Validated customer inquiry question: safe to strip from merchant assertion checks
            return " "

        draft_text_unquoted = quote_pattern.sub(_strip_if_valid_quote, draft_text)

        # Fail closed if draft contains solely an inquiry quote with no merchant content
        if len(draft_text_unquoted.strip()) < 10:
            checks.append(ClaimVerification(
                claim_type=ClaimType.UNGROUNDED_CLAIM,
                claimed_value=draft_text,
                catalog_value=None,
                is_valid=False,
                notes="Draft consists only of quoted customer text with no grounded merchant response.",
            ))
            reasons.append("Draft lacks grounded merchant response body.")
            overall_status = VerificationStatus.FAIL

        for pattern in self._UNGROUNDED_PATTERNS:
            match = pattern.search(draft_text_unquoted)
            if match:
                term = match.group(0).strip()
                if (not catalog_item) or (term.lower() not in cat_desc):
                    checks.append(ClaimVerification(
                        claim_type=ClaimType.UNGROUNDED_CLAIM,
                        claimed_value=term,
                        catalog_value=None,
                        is_valid=False,
                        notes=f"Draft asserts ungrounded commercial/product term '{term}' not supported by merchant catalog.",
                    ))
                    reasons.append(f"Ungrounded claim: '{term}' is not supported by catalog ground truth.")
                    overall_status = VerificationStatus.UNCERTAIN

        for pred_pattern in self._PREDICATE_ASSERTIONS:
            for match in pred_pattern.finditer(draft_text_unquoted):
                pred_val = match.group(1).strip().lower()
                if pred_val not in self._BENIGN_PREDICATES and len(pred_val) >= 3:
                    if (not catalog_item) or (pred_val not in cat_desc):
                        checks.append(ClaimVerification(
                            claim_type=ClaimType.UNGROUNDED_CLAIM,
                            claimed_value=pred_val,
                            catalog_value=None,
                            is_valid=False,
                            notes=f"Draft asserts ungrounded product specification '{pred_val}' not verified in merchant catalog.",
                        ))
                        reasons.append(f"Ungrounded specification: '{pred_val}' not found in catalog.")
                        overall_status = VerificationStatus.UNCERTAIN

        # Comprehensive vocabulary and assertion grounding check:
        # Every content word in the draft must be grounded in catalog item data or transactional boilerplate.
        # Note: Customer inquiry text is NEVER added to allowed vocabulary because customer questions
        # cannot authorize product claims. Inquiries may only appear in explicit quotes ('...').
        sentences = [s.strip() for s in re.split(r"[\n.!?؛\r]+", draft_text_unquoted) if s.strip()]
        for sentence in sentences:
            sentence_clean = sentence.translate(_ARABIC_INDIC_DIGITS)
            words = re.findall(r"[\w\-]+", sentence_clean)
            unsupported_words = []
            for w in words:
                w_lower = w.lower()
                # Skip numbers, currencies, and single character punctuation
                if w.isdigit() or re.match(r"^\d+(?:\.\d+)?$", w):
                    continue
                if w_lower in self._CURRENCY_NORM or w.upper() in self._CURRENCY_NORM:
                    continue
                if len(w_lower) <= 1:
                    continue

                w_norm = normalize_arabic_text(w_lower) if re.search(r"[\u0621-\u064A]", w) else w_lower
                w_variants = {w_lower, w_norm}
                if re.search(r"[\u0621-\u064A]", w):
                    # Strip common Arabic prefixes (الـ, و, بـ, لـ, والـ, بالـ, للـ)
                    for prefix in ("وال", "بال", "لل", "ال", "و", "ب", "ل"):
                        if w_norm.startswith(prefix) and len(w_norm) > len(prefix) + 1:
                            w_variants.add(w_norm[len(prefix):])

                if not any(v in allowed_tokens for v in w_variants):
                    unsupported_words.append(w)

            if unsupported_words:
                claimed_assertion = " ".join(unsupported_words)
                checks.append(ClaimVerification(
                    claim_type=ClaimType.UNGROUNDED_CLAIM,
                    claimed_value=claimed_assertion,
                    catalog_value=None,
                    is_valid=False,
                    notes=f"Draft asserts ungrounded factual statement or specification ('{claimed_assertion}') in sentence: '{sentence}' not documented in catalog.",
                ))
                reasons.append(f"Ungrounded claim in text: '{claimed_assertion}' not verified by catalog.")
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

            # Check promotion start & expiry dates if configured
            now_utc = datetime.now(timezone.utc)
            if catalog_item.offer_valid_until:
                try:
                    expiry_dt = self._parse_iso_date(catalog_item.offer_valid_until)
                    if now_utc > expiry_dt:
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
                except Exception:
                    checks.append(ClaimVerification(
                        claim_type=ClaimType.DISCOUNT_OFFER,
                        claimed_value="Promotional discount",
                        catalog_value=f"Invalid date: {catalog_item.offer_valid_until}",
                        is_valid=False,
                        notes=f"Cannot verify promotion: catalog offer_valid_until '{catalog_item.offer_valid_until}' is invalid.",
                    ))
                    reasons.append(f"Invalid offer validity date format: '{catalog_item.offer_valid_until}'.")
                    overall_status = VerificationStatus.FAIL

            if catalog_item.offer_valid_from:
                try:
                    start_dt = self._parse_iso_date(catalog_item.offer_valid_from)
                    if now_utc < start_dt:
                        if extracted_discounts or catalog_item.discount_percent > 0:
                            checks.append(ClaimVerification(
                                claim_type=ClaimType.DISCOUNT_OFFER,
                                claimed_value="Active promotion",
                                catalog_value=f"Starts on {catalog_item.offer_valid_from}",
                                is_valid=False,
                                notes="Promotional offer has not yet started according to catalog offer_valid_from date.",
                            ))
                            reasons.append("Inactive promotion: Offer date is in the future.")
                            overall_status = VerificationStatus.FAIL
                except Exception:
                    checks.append(ClaimVerification(
                        claim_type=ClaimType.DISCOUNT_OFFER,
                        claimed_value="Promotional discount",
                        catalog_value=f"Invalid date: {catalog_item.offer_valid_from}",
                        is_valid=False,
                        notes=f"Cannot verify promotion: catalog offer_valid_from '{catalog_item.offer_valid_from}' is invalid.",
                    ))
                    reasons.append(f"Invalid offer start date format: '{catalog_item.offer_valid_from}'.")
                    overall_status = VerificationStatus.FAIL

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
