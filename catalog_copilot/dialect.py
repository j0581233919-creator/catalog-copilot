"""Advanced Arabic dialect normalizer, intent extractor, and query cleaner for CatalogCopilot.

Supports Egyptian (لهجة مصرية), Gulf/Saudi (لهجة خليجية/سعودية),
Levantine (لهجة شامية), and Modern Standard Arabic (فصحى).
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Set, Dict, Optional, Tuple

# Base Arabic transliteration and normalization tables
_ARABIC_INDIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_TASHKEEL_REGEX = re.compile(r"[\u0617-\u061A\u064B-\u0652]")
_TATWEEL_REGEX = re.compile(r"\u0640+")
_REPEATED_CHARS_REGEX = re.compile(r"(.)\1{2,}")  # 3 or more repetitions


class ArabicDialect(str, Enum):
    EGYPTIAN = "EGYPTIAN"
    GULF = "GULF"
    LEVANTINE = "LEVANTINE"
    MSA = "MSA"
    UNKNOWN = "UNKNOWN"


class QueryIntent(str, Enum):
    PRICE_INQUIRY = "PRICE_INQUIRY"
    STOCK_INQUIRY = "STOCK_INQUIRY"
    OFFER_INQUIRY = "OFFER_INQUIRY"
    SPEC_INQUIRY = "SPEC_INQUIRY"
    PURCHASE_INTENT = "PURCHASE_INTENT"
    GENERAL_INQUIRY = "GENERAL_INQUIRY"


# Dialect-specific vocabulary patterns
_DIALECT_MARKERS: Dict[ArabicDialect, Set[str]] = {
    ArabicDialect.EGYPTIAN: {
        "عايز", "عاوز", "عاوزه", "عايزه", "عايزين", "عاوزين",
        "بكام", "كام", "عامل كام", "بكام ده", "بكام دي",
        "ده", "دي", "دول", "دة", "كدة", "كده", "اوي", "قوي",
        "ازيك", "ازيكم", "اخبارك", "يا فندم", "يا باشا", "يا بيه",
        "ممكن اعرف", "لو سمحت", "بعد اذنك", "بالله عليك", "طب", "عشان",
        "فيه منه", "في منه", "في الوان", "في مقاسات", "ارخص حاجة", "حاجة",
    },
    ArabicDialect.GULF: {
        "ابغى", "أبغى", "ابي", "أبي", "ابغي", "ودّي", "ودي", "ودنا",
        "بكم", "كم", "كم سعره", "كم عليه", "وش سعره", "وش عروضكم",
        "وش", "ايش", "شو", "وشو", "ذا", "ذي", "ذول", "هذا", "هذي",
        "الحين", "هالحين", "مره", "واجد", "حيل", "وايد",
        "مساكم الله بالخير", "صبحكم الله بالخير", "هلا والله", "يا هلا",
        "لاهنت", "لا هنت", "يعطيك العافية", "عساك ع القوة", "تكفى",
        "فيه خصم", "عندكم كود", "متوفر الحين", "موجود الحين", "متى يوصل",
    },
    ArabicDialect.LEVANTINE: {
        "بدي", "بدّي", "بدنا", "عم دور", "عم افتش", "حابب", "حابب اعرف",
        "قديش", "أديش", "اديش", "شو سعره", "شو بكلف", "قديش حقه",
        "شو", "هيدا", "هادا", "هيدي", "هدول", "هلق", "هلأ", "هسع", "هسا",
        "كتير", "كتير منيح", "يسعد مساك", "يسعد صباحك", "لو بتريد",
        "من بعد اذنك", "شكرا الك", "في حسم", "في تنزيلات", "في منو",
        "متوفر هلق", "متوفر هلأ", "موجود عندكن",
    },
    ArabicDialect.MSA: {
        "أريد", "أرغب", "أود", "أبحث عن", "كم سعر", "كم ثمن",
        "هل يتوفر", "هل يوجد", "هل لديكم", "تخفيض", "حسم", "مواصفات",
        "السلام عليكم", "تحياتي", "أرجو الإفادة",
    },
}

# Intent trigger patterns
# Intent trigger delimiters supporting all Arabic and Western punctuation
_INTENT_SEP_START = r"(?:^|[\s,،.!\-؟?؛:()\[\]\"'_]+)"
_INTENT_SEP_END = r"(?:[\s,،.!\-؟?؛:()\[\]\"'_]+|$)"

_INTENT_PATTERNS: List[Tuple[QueryIntent, re.Pattern]] = [
    (
        QueryIntent.PRICE_INQUIRY,
        re.compile(rf"{_INTENT_SEP_START}(?:[وف])?(?:بكام|كام|بكم|كم|قديش|أديش|اديش|سعر|سعره|سعره كام|عامل كام|ثمن|ثمنه|تكلفة|تكلفته|تكلف|بكام ده|بكم ذا|شو سعره){_INTENT_SEP_END}", re.IGNORECASE),
    ),
    (
        QueryIntent.STOCK_INQUIRY,
        re.compile(rf"{_INTENT_SEP_START}(?:[وف])?(?:متوفر|متوفرة|موجود|موجودة|متاحة|متاح|في منه|فيه منه|في منو|عندكم|عندكن|بالمخزن|بالمستودع|خلص|نفد|توفر|يتوفر|هلأ|الحين){_INTENT_SEP_END}", re.IGNORECASE),
    ),
    (
        QueryIntent.OFFER_INQUIRY,
        re.compile(rf"{_INTENT_SEP_START}(?:[وف])?(?:خصم|تخفيض|عروض|عرض|تنزيلات|حسم|كوبون|كود خصم|تخفيضات|ارخص|وفر|اوفر){_INTENT_SEP_END}", re.IGNORECASE),
    ),
    (
        QueryIntent.SPEC_INQUIRY,
        re.compile(rf"{_INTENT_SEP_START}(?:[وف])?(?:مواصفات|تفاصيل|الوان|ألوان|مقاس|مقاسات|حجم|ذاكرة|جيجا|رام|سعة|بطارية|شاشة|لون){_INTENT_SEP_END}", re.IGNORECASE),
    ),
    (
        QueryIntent.PURCHASE_INTENT,
        re.compile(rf"{_INTENT_SEP_START}(?:[وف])?(?:اشتري|اشتريه|اشحن|اطلب|احجز|طلب|شراء|توصيل|شحن|طريقة الدفع|احجزلي|احجز لي|بدي اطلب|ابي اشتري|عايز اشتري){_INTENT_SEP_END}", re.IGNORECASE),
    ),
]

# Conversational greetings & pleasantries to strip when extracting product tokens
_GREETINGS_FILLERS = [
    r"السلام عليكم ورحمة الله وبركاته",
    r"السلام عليكم",
    r"سلام عليكم",
    r"مساكم الله بالخير",
    r"صبحكم الله بالخير",
    r"صباح الخير",
    r"مساء الخير",
    r"يسعد صباحك",
    r"يسعد صباحكم",
    r"يسعد صباحكن",
    r"يسعد مساك",
    r"يسعد مساكم",
    r"يسعد مساكن",
    r"يعطيكم العافية",
    r"يعطيك العافية",
    r"الله يعافيك",
    r"عساك ع القوة",
    r"عساكم ع القوة",
    r"هلا والله",
    r"يا هلا",
    r"مرحبا",
    r"أهلا وسهلا",
    r"اهلا وسهلا",
    r"ازيك",
    r"ازيكم",
    r"اخبارك",
    r"شلونك",
    r"شلونكم",
    r"كيف حالك",
    r"كيفكم",
    r"تحياتي",
]

_LEAD_IN_VERBS_FILLERS = [
    r"ممكن بعد اذنك اعرف",
    r"ممكن اعرف لو سمحت",
    r"ممكن اعرف",
    r"ممكن تقولي",
    r"ممكن تفيدني",
    r"لو سمحت يا فندم",
    r"لو سمحت يا باشا",
    r"لو سمحت",
    r"لو بتريد",
    r"من بعد اذنك",
    r"بعد اذنك",
    r"بالله عليك",
    r"لاهنت يا غالي",
    r"لاهنتوا",
    r"لا هنتوا",
    r"لاهنت",
    r"لا هنت",
    r"تكفى",
    r"الله يسعدك",
    r"يا غالي",
    r"يا فندم",
    r"يا باشا",
    r"يا طيب",
    r"اسأل عن",
    r"اسال عن",
    r"اسأل",
    r"اسال",
    r"استفسر عن",
    r"استفسر",
    r"عايز اسأل عن",
    r"عايز اسال عن",
    r"عاوز اعرف عن",
    r"بدي اسأل عن",
    r"بدي اعرف عن",
    r"ابغى استفسر عن",
    r"ابي استفسر عن",
    r"حبيت اسأل عن",
    r"حبيت اعرف",
    r"حابب استفسر عن",
    r"عايز اعرف",
    r"عاوز اعرف",
    r"بدي اعرف",
    r"ابغى اعرف",
    r"ابي اعرف",
    r"عايز",
    r"عاوز",
    r"بدي",
    r"ابغى",
    r"أبغى",
    r"ابي",
    r"أبي",
    r"اريد",
    r"أريد",
    r"محتاج",
]

_QUESTION_FILLERS = [
    r"بكام سعره ده",
    r"بكام ده",
    r"بكام دي",
    r"عامل كام ده",
    r"عامل كام",
    r"بكام",
    r"كام سعره",
    r"كام ثمنه",
    r"كام",
    r"بكم سعره ذا",
    r"بكم ذا",
    r"بكم ذي",
    r"كم سعره",
    r"كم عليه",
    r"بكم",
    r"كم",
    r"قديش سعره هيدا",
    r"قديش سعره",
    r"قديش حقه",
    r"قديش",
    r"أديش",
    r"اديش",
    r"شو سعره",
    r"شو سعر",
    r"شو حقه",
    r"شو ثمنه",
    r"شو ثمن",
    r"شو تكلفته",
    r"شو تكلفة",
    r"شو هو",
    r"وش سعره",
    r"وش سعر",
    r"وش ثمنه",
    r"وش ثمن",
    r"وش تكلفته",
    r"وش تكلفة",
    r"وش هو",
    r"ايش سعره",
    r"ايش سعر",
    r"ايش ثمنه",
    r"ايش ثمن",
    r"ايش تكلفته",
    r"ايش تكلفة",
    r"ايش هو",
    r"كم سعره",
    r"كم سعر",
    r"كم ثمنه",
    r"كم ثمن",
    r"كام سعره",
    r"كام سعر",
    r"بكام سعره",
    r"بكام سعر",
    r"بكم سعره",
    r"بكم سعر",
    r"قديش سعره",
    r"قديش سعر",
    r"أديش سعره",
    r"أديش سعر",
    r"اديش سعره",
    r"اديش سعر",
    r"عامله كام",
    r"عاملة كام",
    r"عاملين كام",
    r"سعر كام",
    r"سعره كام",
    r"سعرها كام",
    r"سعر",
    r"سعره",
    r"ثمن",
    r"ثمنه",
    r"تكلفة",
    r"تكلفته",
    r"حقه",
    r"وهل متوفر عندكم",
    r"وهل متوفر عندكن",
    r"هل متوفر عندكم",
    r"هل متوفر عندكن",
    r"متوفر عندكم",
    r"متوفر عندكن",
    r"وهل متوفر",
    r"هل متوفر",
    r"متوفر الحين",
    r"متوفر هلق",
    r"متوفر هلأ",
    r"متوفر منه",
    r"ومتوفر منه",
    r"ومتوفر",
    r"متوفر",
    r"وهل موجود عندكم",
    r"وهل موجود عندكن",
    r"هل موجود عندكم",
    r"هل موجود عندكن",
    r"موجود عندكم",
    r"موجود عندكن",
    r"وهل موجود",
    r"هل موجود",
    r"موجود الحين",
    r"موجود هلق",
    r"موجود هلأ",
    r"وموجود منه",
    r"موجود منه",
    r"وموجود",
    r"موجود",
    r"وفيه خصم",
    r"وفيه عروض",
    r"وفيه تنزيلات",
    r"وفيه حسم",
    r"فيه خصم",
    r"فيه عروض",
    r"فيه تنزيلات",
    r"فيه حسم",
    r"في خصم",
    r"في عروض",
    r"في تنزيلات",
    r"في حسم",
    r"فيه منه",
    r"في منه",
    r"في منو",
    r"وفيه منه",
    r"وفي منه",
    r"وفي منو",
    r"فيه عروض على",
    r"في عروض على",
    r"في خصم على",
    r"فيه خصم على",
]

_DEMONSTRATIVES_FILLERS = [
    r"وشكرا الك كتير", r"وشكرا كتير", r"وشكرا الك", r"وشكرا",
    r"شكرا الك كتير", r"شكرا كتير", r"شكرا الك", r"شكرا",
    r"منه", r"منو", r"منها", r"عنه", r"عنها",
    r"ده", r"دي", r"دول", r"دة",
    r"ذا", r"ذي", r"ذول", r"هذا", r"هذي",
    r"هيدا", r"هادا", r"هيدي", r"هدول",
    r"الحين", r"هلق", r"هلأ",
    r"يا ريت", r"ياريت",
]


def clean_elongation(text: str) -> str:
    """Collapses 3+ consecutive identical Arabic/English characters into 1.
    e.g. 'عااااايز' -> 'عايز', 'بكممممم' -> 'بكم'.
    """
    if not text:
        return ""
    # Collapse 3 or more repetitions to single character
    return _REPEATED_CHARS_REGEX.sub(r"\1", text)


def normalize_arabic_phonetics_and_letters(text: str) -> str:
    """Normalizes Arabic letters, tashkeel, tatweel, digits, and common orthographic variations."""
    if not text:
        return ""
    # Strip tashkeel
    t = _TASHKEEL_REGEX.sub("", text)
    # Strip tatweel
    t = _TATWEEL_REGEX.sub("", t)
    # Collapse elongation
    t = clean_elongation(t)
    # Translate digits
    t = t.translate(_ARABIC_INDIC_DIGITS)
    # Normalize Alef forms: إ, أ, آ, ا -> ا
    t = re.sub(r"[إأآا]", "ا", t)
    # Normalize Teh Marbuta and Heh at end of words: ة -> ه
    t = re.sub(r"ة\b", "ه", t)
    # Normalize Alef Maksura and Yeh: ى -> ي
    t = re.sub(r"ى\b", "ي", t)
    # Normalize Hamza variants: ئ, ؤ -> ء or standard form
    t = re.sub(r"[ئؤ]", "ء", t)
    return t.strip()


def detect_arabic_dialect(text: str) -> Tuple[ArabicDialect, float]:
    """Detects the primary Arabic dialect with a confidence score [0.0, 1.0]."""
    if not text:
        return ArabicDialect.UNKNOWN, 0.0

    norm = normalize_arabic_phonetics_and_letters(text.lower())
    words = set(re.split(r"\s+", norm))

    scores: Dict[ArabicDialect, int] = {
        ArabicDialect.EGYPTIAN: 0,
        ArabicDialect.GULF: 0,
        ArabicDialect.LEVANTINE: 0,
        ArabicDialect.MSA: 0,
    }

    for dialect, markers in _DIALECT_MARKERS.items():
        for marker in markers:
            m_norm = normalize_arabic_phonetics_and_letters(marker)
            if " " in m_norm:
                if m_norm in norm:
                    scores[dialect] += 2  # Multi-word marker has higher specificity
            else:
                if m_norm in words:
                    scores[dialect] += 1

    total_hits = sum(scores.values())
    if total_hits == 0:
        # Default to MSA if text is Arabic, else UNKNOWN
        has_arabic = bool(re.search(r"[\u0600-\u06FF]", text))
        return (ArabicDialect.MSA, 0.5) if has_arabic else (ArabicDialect.UNKNOWN, 0.0)

    best_dialect, best_score = max(scores.items(), key=lambda x: x[1])
    confidence = round(best_score / total_hits, 2)
    return best_dialect, confidence


def extract_intents(text: str) -> List[QueryIntent]:
    """Extracts all matching commercial inquiry intents from customer text."""
    if not text:
        return [QueryIntent.GENERAL_INQUIRY]

    norm = normalize_arabic_phonetics_and_letters(text.lower())
    intents: List[QueryIntent] = []

    for intent, pattern in _INTENT_PATTERNS:
        if pattern.search(norm) or pattern.search(text.lower()):
            if intent not in intents:
                intents.append(intent)

    if not intents:
        intents.append(QueryIntent.GENERAL_INQUIRY)

    return intents


def isolate_product_search_tokens(inquiry_text: str) -> str:
    """Strips conversational dialect fillers, greetings, and lead-in phrases
    to isolate the core product terms for accurate catalog and fuzzy matching.
    """
    if not inquiry_text:
        return ""

    cleaned = inquiry_text.strip()
    cleaned = clean_elongation(cleaned)

    # 1. Normalize punctuation into spaces first
    cleaned = re.sub(r"[؟?.,!،؛:()\[\]\"'_]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    # 2. Single unified pass over all fillers sorted by length descending
    all_fillers = sorted(
        _GREETINGS_FILLERS + _LEAD_IN_VERBS_FILLERS + _QUESTION_FILLERS + _DEMONSTRATIVES_FILLERS,
        key=len,
        reverse=True,
    )
    for filler in all_fillers:
        cleaned = re.sub(rf"(?<!\S){filler}(?!\S)", " ", cleaned, flags=re.IGNORECASE)

    # Clean redundant extra spaces
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    # If everything was stripped, fallback to original inquiry
    return cleaned if cleaned else inquiry_text.strip()

    # If everything was stripped, fallback to original inquiry
    return cleaned if cleaned else inquiry_text.strip()


@dataclass
class DialectalQueryAnalysis:
    """Structured analysis of a dialectal inquiry."""
    raw_text: str
    cleaned_product_query: str
    detected_dialect: ArabicDialect
    dialect_confidence: float
    intents: List[QueryIntent] = field(default_factory=list)

    @property
    def is_price_inquiry(self) -> bool:
        return QueryIntent.PRICE_INQUIRY in self.intents

    @property
    def is_stock_inquiry(self) -> bool:
        return QueryIntent.STOCK_INQUIRY in self.intents

    @property
    def is_offer_inquiry(self) -> bool:
        return QueryIntent.OFFER_INQUIRY in self.intents

    def to_dict(self) -> Dict[str, any]:
        return {
            "raw_text": self.raw_text,
            "cleaned_product_query": self.cleaned_product_query,
            "detected_dialect": self.detected_dialect.value,
            "dialect_confidence": self.dialect_confidence,
            "intents": [i.value for i in self.intents],
        }


def parse_dialectal_inquiry(text: str) -> DialectalQueryAnalysis:
    """Convenience facade to parse, classify, and clean any dialectal customer inquiry."""
    dialect, confidence = detect_arabic_dialect(text)
    intents = extract_intents(text)
    cleaned_query = isolate_product_search_tokens(text)

    return DialectalQueryAnalysis(
        raw_text=text,
        cleaned_product_query=cleaned_query,
        detected_dialect=dialect,
        dialect_confidence=confidence,
        intents=intents,
    )
