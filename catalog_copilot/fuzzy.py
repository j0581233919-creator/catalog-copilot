"""Fuzzy SKU and bilingual product resolution engine for CatalogCopilot.

Pure standard-library implementation providing:
- Damerau-Levenshtein edit distance with transposition support.
- Token-set Jaccard similarity and prefix/substring matching.
- Arabic-English phonetic and transliteration bridging.
- Hyphen/space-agnostic SKU resolution.
- Deterministic candidate ranking and ambiguity-detection guardrails.
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple, Set
from .models import CatalogItem
from .dialect import normalize_arabic_phonetics_and_letters


class MatchType(str, Enum):
    EXACT_SKU = "EXACT_SKU"
    NORMALIZED_SKU = "NORMALIZED_SKU"
    PARTIAL_SKU = "PARTIAL_SKU"
    EXACT_TITLE = "EXACT_TITLE"
    FUZZY_TITLE = "FUZZY_TITLE"
    TRANSLITERATED = "TRANSLITERATED"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"


# Common brand & product transliteration dictionaries
_AR_TO_EN_TRANSLITERATION: Dict[str, str] = {
    "ايفون": "iphone",
    "أيفون": "iphone",
    "آيفون": "iphone",
    "سامسونج": "samsung",
    "سمسونج": "samsung",
    "ماكبوك": "macbook",
    "ماك بوك": "macbook",
    "ايباد": "ipad",
    "آيباد": "ipad",
    "تابلت": "tablet",
    "تاب لت": "tablet",
    "لابتوب": "laptop",
    "لاب توب": "laptop",
    "ديل": "dell",
    "سوني": "sony",
    "شاومي": "xiaomi",
    "هواوي": "huawei",
    "لينوفو": "lenovo",
    "اتش بي": "hp",
    "برو": "pro",
    "ماكس": "max",
    "بلس": "plus",
    "الترا": "ultra",
    "ايربودز": "airpods",
    "اير بودز": "airpods",
    "سماعة": "headphone",
    "سماعات": "headphones",
    "ساعة": "watch",
    "شاحن": "charger",
    "بلوتوث": "bluetooth",
    "بلوتوس": "bluetooth",
    "كيبل": "cable",
    "كابل": "cable",
    "سلك": "cable",
    "جراب": "case",
    "كفر": "case",
    "شاشة": "screen",
    "كاميرا": "camera",
    "ماوس": "mouse",
    "كيبورد": "keyboard",
}

# Reverse mapping
_EN_TO_AR_TRANSLITERATION: Dict[str, str] = {
    v: k for k, v in _AR_TO_EN_TRANSLITERATION.items()
}


def damerau_levenshtein_distance(s1: str, s2: str) -> int:
    """Calculates Damerau-Levenshtein distance between two strings,
    supporting insertions, deletions, substitutions, and adjacent transpositions.
    """
    if s1 == s2:
        return 0
    len1, len2 = len(s1), len(s2)
    if len1 == 0:
        return len2
    if len2 == 0:
        return len1

    # Matrix of dimensions (len1 + 2) x (len2 + 2)
    max_dist = len1 + len2
    da: Dict[str, int] = {}
    h = [[0] * (len2 + 2) for _ in range(len1 + 2)]

    h[0][0] = max_dist
    for i in range(len1 + 1):
        h[i + 1][0] = max_dist
        h[i + 1][1] = i
    for j in range(len2 + 1):
        h[0][j + 1] = max_dist
        h[1][j + 1] = j

    for i in range(1, len1 + 1):
        db = 0
        for j in range(1, len2 + 1):
            k = da.get(s2[j - 1], 0)
            l = db
            cost = 0 if s1[i - 1] == s2[j - 1] else 1
            if cost == 0:
                db = j

            h[i + 1][j + 1] = min(
                h[i][j + 1] + 1,        # Deletion
                h[i + 1][j] + 1,        # Insertion
                h[i][j] + cost,         # Substitution
                h[k][l] + (i - k - 1) + 1 + (j - l - 1),  # Transposition
            )
        da[s1[i - 1]] = i

    return h[len1 + 1][len2 + 1]


def normalized_string_similarity(s1: str, s2: str) -> float:
    """Returns normalized string similarity in range [0.0, 1.0]."""
    if not s1 and not s2:
        return 1.0
    if not s1 or not s2:
        return 0.0
    max_len = max(len(s1), len(s2))
    dist = damerau_levenshtein_distance(s1, s2)
    return max(0.0, 1.0 - (dist / max_len))


def token_jaccard_similarity(tokens1: Set[str], tokens2: Set[str]) -> float:
    """Calculates Jaccard index between two sets of tokens."""
    if not tokens1 and not tokens2:
        return 1.0
    if not tokens1 or not tokens2:
        return 0.0
    intersection = len(tokens1.intersection(tokens2))
    union = len(tokens1.union(tokens2))
    return intersection / union if union > 0 else 0.0


def strip_arabic_al(tok: str) -> str:
    """Strips leading Arabic definite article 'ال' for words with 4+ characters."""
    if tok.startswith("ال") and len(tok) >= 4:
        return tok[2:]
    return tok


def transliterate_text(text: str, target_lang: str = "en") -> str:
    """Translates known brand and product tokens between Arabic and English."""
    t = text.strip().lower()
    if target_lang == "en":
        t_clean = normalize_arabic_phonetics_and_letters(t)
        # Multi-word phrase replacements first
        mapping = sorted(_AR_TO_EN_TRANSLITERATION.items(), key=lambda x: len(x[0]), reverse=True)
        for ar_phrase, en_phrase in mapping:
            if " " in ar_phrase and ar_phrase in t_clean:
                t_clean = t_clean.replace(ar_phrase, en_phrase)
        # Single-token replacements with 'ال' article normalization
        words = re.split(r"[\s\-_]+", t_clean)
        translated = []
        for w in words:
            if not w:
                continue
            tr = _AR_TO_EN_TRANSLITERATION.get(w)
            if not tr:
                tr = _AR_TO_EN_TRANSLITERATION.get(strip_arabic_al(w), w)
            translated.append(tr)
        return " ".join(translated)
    else:
        mapping = sorted(_EN_TO_AR_TRANSLITERATION.items(), key=lambda x: len(x[0]), reverse=True)
        for en_phrase, ar_phrase in mapping:
            if " " in en_phrase and en_phrase in t:
                t = t.replace(en_phrase, ar_phrase)
        words = re.split(r"[\s\-_]+", t)
        translated = [_EN_TO_AR_TRANSLITERATION.get(w, w) for w in words if w]
        return " ".join(translated)


def normalize_sku_pattern(sku: str) -> str:
    """Strips hyphens, spaces, underscores, and dots from SKU."""
    return re.sub(r"[\s\-_.:/]+", "", sku.strip().upper())


@dataclass
class FuzzyCandidate:
    """Scored catalog item candidate."""
    item: CatalogItem
    score: float
    match_type: MatchType
    matched_field: str
    details: str

    def to_dict(self) -> Dict[str, any]:
        return {
            "sku": self.item.sku,
            "title_ar": self.item.title_ar,
            "title_en": self.item.title_en,
            "score": round(self.score, 3),
            "match_type": self.match_type.value,
            "matched_field": self.matched_field,
            "details": self.details,
        }


@dataclass
class ResolutionResult:
    """Result of catalog item resolution with fail-closed guardrails."""
    resolved_item: Optional[CatalogItem]
    is_ambiguous: bool
    confidence_score: float
    margin: float
    candidates: List[FuzzyCandidate] = field(default_factory=list)
    resolution_notes: str = ""

    @property
    def has_match(self) -> bool:
        return self.resolved_item is not None

    def to_dict(self) -> Dict[str, any]:
        return {
            "resolved_sku": self.resolved_item.sku if self.resolved_item else None,
            "is_ambiguous": self.is_ambiguous,
            "confidence_score": round(self.confidence_score, 3),
            "margin": round(self.margin, 3),
            "resolution_notes": self.resolution_notes,
            "candidates_count": len(self.candidates),
            "top_candidates": [c.to_dict() for c in self.candidates[:5]],
        }


class FuzzyCatalogMatcher:
    """Deterministic, pure-python fuzzy matcher for SKUs and bilingual product titles."""

    def __init__(self, min_confidence: float = 0.72, min_margin: float = 0.12):
        self.min_confidence = min_confidence
        self.min_margin = min_margin

    def score_item_against_query(
        self,
        item: CatalogItem,
        query: str,
        lang: str = "ar",
    ) -> FuzzyCandidate:
        """Calculates multifaceted similarity score between an inquiry and a catalog item."""
        q_raw = query.strip()
        q_norm_ar = normalize_arabic_phonetics_and_letters(q_raw.lower())
        q_norm_en = transliterate_text(q_raw, target_lang="en").lower()
        q_clean_sku = normalize_sku_pattern(q_raw)

        item_sku_norm = normalize_sku_pattern(item.sku)
        item_title_ar_norm = normalize_arabic_phonetics_and_letters(item.title_ar.lower())
        item_title_en_norm = item.title_en.lower()

        # 1. Check exact / normalized SKU match
        if q_raw.upper() == item.sku:
            return FuzzyCandidate(
                item=item,
                score=1.0,
                match_type=MatchType.EXACT_SKU,
                matched_field="sku",
                details="Exact SKU match",
            )

        if len(q_clean_sku) >= 3 and q_clean_sku == item_sku_norm:
            return FuzzyCandidate(
                item=item,
                score=0.98,
                match_type=MatchType.NORMALIZED_SKU,
                matched_field="sku",
                details="Normalized SKU match (hyphens/spaces stripped)",
            )

        # Check partial SKU (e.g. customer entered '100' or 'PRO-100' for 'PRD-PRO-100')
        if len(q_clean_sku) >= 3 and (q_clean_sku in item_sku_norm or item_sku_norm.endswith(q_clean_sku)):
            ratio = len(q_clean_sku) / len(item_sku_norm)
            sku_partial_score = min(0.92, 0.75 + 0.20 * ratio)
            return FuzzyCandidate(
                item=item,
                score=sku_partial_score,
                match_type=MatchType.PARTIAL_SKU,
                matched_field="sku",
                details=f"Partial SKU match ({q_clean_sku} in {item_sku_norm})",
            )

        # 2. Check title exact matches
        if q_norm_ar and q_norm_ar == item_title_ar_norm:
            return FuzzyCandidate(
                item=item,
                score=1.0,
                match_type=MatchType.EXACT_TITLE,
                matched_field="title_ar",
                details="Exact Arabic title match",
            )
        if q_raw.lower() == item_title_en_norm:
            return FuzzyCandidate(
                item=item,
                score=1.0,
                match_type=MatchType.EXACT_TITLE,
                matched_field="title_en",
                details="Exact English title match",
            )

        # 3. Compute multifaceted fuzzy score
        scores = []

        # Arabic title string, substring, & token similarity
        ar_str_sim = normalized_string_similarity(q_norm_ar, item_title_ar_norm)
        q_tokens_ar = {strip_arabic_al(t) for t in re.split(r"[\s\-_/]+", q_norm_ar) if t}
        item_tokens_ar = {strip_arabic_al(t) for t in re.split(r"[\s\-_/]+", item_title_ar_norm) if t}
        ar_tok_jaccard = token_jaccard_similarity(q_tokens_ar, item_tokens_ar)
        ar_coverage = len(q_tokens_ar.intersection(item_tokens_ar)) / len(q_tokens_ar) if q_tokens_ar else 0.0
        ar_sub = (0.82 + 0.16 * (len(q_norm_ar) / len(item_title_ar_norm))) if (q_norm_ar in item_title_ar_norm and len(q_norm_ar) >= 4) else 0.0
        ar_tok_combined = (0.75 * ar_coverage + 0.25 * ar_tok_jaccard) if ar_coverage > 0 else 0.0
        ar_combined = max(ar_str_sim, ar_sub, ar_tok_combined)
        scores.append((ar_combined, "title_ar", MatchType.FUZZY_TITLE))

        # English title string, substring, & token similarity (using transliterated query)
        en_str_sim = normalized_string_similarity(q_norm_en, item_title_en_norm)
        q_tokens_en = set(t for t in re.split(r"[\s\-_/]+", q_norm_en) if t)
        item_tokens_en = set(t for t in re.split(r"[\s\-_/]+", item_title_en_norm) if t)
        en_tok_jaccard = token_jaccard_similarity(q_tokens_en, item_tokens_en)
        en_coverage = len(q_tokens_en.intersection(item_tokens_en)) / len(q_tokens_en) if q_tokens_en else 0.0
        en_sub = (0.82 + 0.16 * (len(q_norm_en) / len(item_title_en_norm))) if (q_norm_en in item_title_en_norm and len(q_norm_en) >= 4) else 0.0
        en_tok_combined = (0.75 * en_coverage + 0.25 * en_tok_jaccard) if en_coverage > 0 else 0.0
        en_combined = max(en_str_sim, en_sub, en_tok_combined)

        # Check if transliteration contributed to the match
        match_type_en = MatchType.TRANSLITERATED if q_norm_en != q_raw.lower() else MatchType.FUZZY_TITLE
        scores.append((en_combined, "title_en", match_type_en))

        # SKU fuzzy similarity (in case of typos in SKU e.g. 'PRD-PRO-10O' vs 'PRD-PRO-100')
        sku_sim = normalized_string_similarity(q_clean_sku, item_sku_norm)
        if sku_sim >= 0.70:
            scores.append((sku_sim * 0.95, "sku", MatchType.FUZZY_TITLE))

        best_score, best_field, best_match_type = max(scores, key=lambda x: x[0])

        return FuzzyCandidate(
            item=item,
            score=round(best_score, 3),
            match_type=best_match_type if best_score >= self.min_confidence else MatchType.LOW_CONFIDENCE,
            matched_field=best_field,
            details=f"Fuzzy score {best_score:.3f} on {best_field}",
        )

    def resolve(
        self,
        items: List[CatalogItem],
        query: str,
        lang: str = "ar",
    ) -> ResolutionResult:
        """Resolves query against catalog items with deterministic fail-closed ambiguity checks."""
        if not query or not items:
            return ResolutionResult(
                resolved_item=None,
                is_ambiguous=False,
                confidence_score=0.0,
                margin=0.0,
                candidates=[],
                resolution_notes="Empty query or catalog items.",
            )

        scored_candidates: List[FuzzyCandidate] = []
        for item in items:
            candidate = self.score_item_against_query(item, query, lang=lang)
            if candidate.score > 0.25:  # Filter out irrelevant noise
                scored_candidates.append(candidate)

        scored_candidates.sort(key=lambda c: c.score, reverse=True)

        if not scored_candidates:
            return ResolutionResult(
                resolved_item=None,
                is_ambiguous=False,
                confidence_score=0.0,
                margin=0.0,
                candidates=[],
                resolution_notes="No matching items found.",
            )

        top1 = scored_candidates[0]

        # Single candidate case
        if len(scored_candidates) == 1:
            if top1.score >= self.min_confidence:
                return ResolutionResult(
                    resolved_item=top1.item,
                    is_ambiguous=False,
                    confidence_score=top1.score,
                    margin=1.0,
                    candidates=scored_candidates,
                    resolution_notes=f"Single high-confidence match ({top1.score:.2f}) on {top1.matched_field}.",
                )
            else:
                return ResolutionResult(
                    resolved_item=None,
                    is_ambiguous=False,
                    confidence_score=top1.score,
                    margin=0.0,
                    candidates=scored_candidates,
                    resolution_notes=f"Match confidence {top1.score:.2f} below threshold {self.min_confidence:.2f}.",
                )

        # Multiple candidates: check score margin
        top2 = scored_candidates[1]
        margin = round(top1.score - top2.score, 3)

        # Exact match always wins
        if top1.score == 1.0 and top2.score < 1.0:
            return ResolutionResult(
                resolved_item=top1.item,
                is_ambiguous=False,
                confidence_score=top1.score,
                margin=margin,
                candidates=scored_candidates,
                resolution_notes="Exact match found.",
            )

        # Check if top candidate meets confidence threshold
        if top1.score < self.min_confidence:
            return ResolutionResult(
                resolved_item=None,
                is_ambiguous=False,
                confidence_score=top1.score,
                margin=margin,
                candidates=scored_candidates,
                resolution_notes=f"Top candidate score ({top1.score:.2f}) below threshold {self.min_confidence:.2f}.",
            )

        # Check if margin over 2nd candidate is sufficient
        if margin < self.min_margin:
            # Ambiguous! Both candidates are closely scored -> Fail closed to prevent wrong product claim!
            return ResolutionResult(
                resolved_item=None,
                is_ambiguous=True,
                confidence_score=top1.score,
                margin=margin,
                candidates=scored_candidates,
                resolution_notes=(
                    f"Ambiguous query: Top candidates '{top1.item.sku}' ({top1.score:.2f}) "
                    f"and '{top2.item.sku}' ({top2.score:.2f}) have delta {margin:.2f} < {self.min_margin:.2f}."
                ),
            )

        # Clear high-confidence winner with sufficient margin
        return ResolutionResult(
            resolved_item=top1.item,
            is_ambiguous=False,
            confidence_score=top1.score,
            margin=margin,
            candidates=scored_candidates,
            resolution_notes=(
                f"Resolved to '{top1.item.sku}' with confidence {top1.score:.2f} "
                f"and margin {margin:.2f}."
            ),
        )
