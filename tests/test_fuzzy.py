"""Unit tests for fuzzy SKU and bilingual product resolution engine."""

import unittest
from catalog_copilot.models import CatalogItem
from catalog_copilot.fuzzy import (
    damerau_levenshtein_distance,
    normalized_string_similarity,
    token_jaccard_similarity,
    transliterate_text,
    normalize_sku_pattern,
    MatchType,
    FuzzyCatalogMatcher,
)


class TestFuzzyCatalogMatcher(unittest.TestCase):
    """Test suite for string distance metrics, SKU normalization, and catalog item resolution."""

    def setUp(self):
        self.item_iphone = CatalogItem(
            sku="APL-IPH-15PRO",
            title_en="Apple iPhone 15 Pro Max 256GB",
            title_ar="آبل آيفون 15 برو ماكس 256 جيجابايت",
            price=4500.0,
            stock_quantity=10,
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
            price=4200.0,
            stock_quantity=8,
            category="Smartphones",
        )
        self.item_dell = CatalogItem(
            sku="DEL-XPS-15",
            title_en="Dell XPS 15 Laptop Intel Core i7",
            title_ar="لابتوب ديل اكس بي اس 15 انتل كور اي 7",
            price=6500.0,
            stock_quantity=3,
            category="Computers",
        )
        self.catalog_items = [
            self.item_iphone,
            self.item_iphone_base,
            self.item_samsung,
            self.item_dell,
        ]
        self.matcher = FuzzyCatalogMatcher(min_confidence=0.70, min_margin=0.12)

    def test_damerau_levenshtein_distance(self):
        """Damerau-Levenshtein distance must handle insertions, deletions, substitutions, and transpositions."""
        self.assertEqual(damerau_levenshtein_distance("iphone", "iphone"), 0)
        self.assertEqual(damerau_levenshtein_distance("ipohne", "iphone"), 1)  # transposition of 'o' and 'h'
        self.assertEqual(damerau_levenshtein_distance("samusng", "samsung"), 1) # transposition of 'u' and 's'
        self.assertEqual(damerau_levenshtein_distance("dell", "deil"), 1)     # substitution
        self.assertEqual(damerau_levenshtein_distance("macbook", "macbok"), 1) # deletion
        self.assertEqual(damerau_levenshtein_distance("pro", "proo"), 1)      # insertion

    def test_transliterate_text(self):
        """Arabic transliterations of tech brands must translate accurately to English and vice versa."""
        self.assertIn("iphone", transliterate_text("ايفون 15 برو", target_lang="en"))
        self.assertIn("samsung", transliterate_text("سامسونج جالاكسي", target_lang="en"))
        self.assertIn("dell", transliterate_text("لابتوب ديل", target_lang="en"))
        self.assertIn("macbook", transliterate_text("ماك بوك", target_lang="en"))

    def test_normalize_sku_pattern(self):
        """SKU normalization must strip whitespace, hyphens, and punctuation."""
        self.assertEqual(normalize_sku_pattern("apl-iph-15pro"), "APLIPH15PRO")
        self.assertEqual(normalize_sku_pattern("APL IPH 15PRO"), "APLIPH15PRO")
        self.assertEqual(normalize_sku_pattern("APL_IPH_15PRO"), "APLIPH15PRO")

    def test_exact_and_normalized_sku_resolution(self):
        """Exact and normalized SKU lookups must score 1.0 or near 1.0."""
        # Exact SKU
        cand_exact = self.matcher.score_item_against_query(self.item_iphone, "APL-IPH-15PRO")
        self.assertEqual(cand_exact.score, 1.0)
        self.assertEqual(cand_exact.match_type, MatchType.EXACT_SKU)

        # SKU without hyphens
        cand_norm = self.matcher.score_item_against_query(self.item_iphone, "apliph15pro")
        self.assertEqual(cand_norm.score, 0.98)
        self.assertEqual(cand_norm.match_type, MatchType.NORMALIZED_SKU)

        # Partial SKU suffix
        cand_part = self.matcher.score_item_against_query(self.item_iphone, "15PRO")
        self.assertEqual(cand_part.match_type, MatchType.PARTIAL_SKU)
        self.assertGreaterEqual(cand_part.score, 0.75)

    def test_fuzzy_brand_and_typo_resolution(self):
        """Common typos in Arabic and English product titles must resolve with high confidence."""
        # Typo in English: 'Samusng S24'
        res = self.matcher.resolve(self.catalog_items, "Samusng Galaxy S24 Ultra", lang="en")
        self.assertTrue(res.has_match)
        self.assertEqual(res.resolved_item.sku, "SAM-GAL-S24")

        # Arabic transliteration: 'ايفون 15 برو ماكس'
        res_ar = self.matcher.resolve(self.catalog_items, "ايفون 15 برو ماكس", lang="ar")
        self.assertTrue(res_ar.has_match)
        self.assertEqual(res_ar.resolved_item.sku, "APL-IPH-15PRO")

    def test_ambiguity_guardrail_fails_closed(self):
        """When an inquiry is equally or closely matched to multiple items, it must fail closed as ambiguous."""
        # Customer just asks for 'آبل آيفون 15' which matches both 'APL-IPH-15PRO' and 'APL-IPH-15BASE'
        res = self.matcher.resolve(self.catalog_items, "آبل آيفون 15", lang="ar")
        self.assertTrue(res.is_ambiguous)
        self.assertIsNone(res.resolved_item, "Ambiguous query must NOT resolve to a single item")
        self.assertLess(res.margin, self.matcher.min_margin)
        self.assertGreaterEqual(len(res.candidates), 2)

    def test_low_confidence_query_rejected(self):
        """Unrelated queries must not match any item in the catalog."""
        res = self.matcher.resolve(self.catalog_items, "طاولة خشبية للمطبخ", lang="ar")
        self.assertFalse(res.has_match)
        self.assertFalse(res.is_ambiguous)
        self.assertIsNone(res.resolved_item)


if __name__ == "__main__":
    unittest.main()
