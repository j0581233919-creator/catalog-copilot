"""Unit tests for Arabic dialect normalization, intent extraction, and query cleaning."""

import unittest
from catalog_copilot.dialect import (
    ArabicDialect,
    QueryIntent,
    clean_elongation,
    normalize_arabic_phonetics_and_letters,
    detect_arabic_dialect,
    extract_intents,
    isolate_product_search_tokens,
    parse_dialectal_inquiry,
)


class TestArabicDialect(unittest.TestCase):
    """Test suite for dialect parsing, classification, and intent extraction."""

    def test_clean_elongation(self):
        """Repeated letters (3+) must be collapsed to a single character."""
        self.assertEqual(clean_elongation("عااااايز"), "عايز")
        self.assertEqual(clean_elongation("بكممممم"), "بكم")
        self.assertEqual(clean_elongation("كتييييير"), "كتير")
        self.assertEqual(clean_elongation("سلاااااام"), "سلام")
        # Double letters should be preserved (e.g. shaddah representation or natural pairs)
        self.assertEqual(clean_elongation("كل"), "كل")

    def test_normalize_arabic_phonetics_and_letters(self):
        """Tashkeel, tatweel, digits, and Alef/Teh Marbuta forms must be normalized."""
        raw = "سَمَاعَةٌ ذَكِيَّةٌ ١٠٠ـ"
        norm = normalize_arabic_phonetics_and_letters(raw)
        self.assertEqual(norm, "سماعه ذكيه 100")

        # Alef forms
        self.assertEqual(normalize_arabic_phonetics_and_letters("إلكترونيات وأجهزة آبل"), "الكترونيات واجهزه ابل")

    def test_detect_egyptian_dialect(self):
        """Phrases using Egyptian dialect markers must be classified as EGYPTIAN."""
        texts = [
            "ازيك يا فندم، عايز اعرف بكام التيشيرت ده؟",
            "عاوزه اسأل لو سمحت فيه منه مقاس لارج؟",
            "عامل كام ده يا باشا؟",
            "بكام ده بعد اذنك عشان عايز اشتريه",
        ]
        for t in texts:
            dialect, conf = detect_arabic_dialect(t)
            self.assertEqual(dialect, ArabicDialect.EGYPTIAN, f"Failed for: {t}")
            self.assertGreater(conf, 0.5)

    def test_detect_gulf_dialect(self):
        """Phrases using Gulf/Saudi dialect markers must be classified as GULF."""
        texts = [
            "هلا والله، لاهنت ابغى استفسر عن سعر الجوال ذا",
            "مساكم الله بالخير، كم عليه الايفون الحين؟",
            "يعطيك العافية، ودي اعرف متوفر الحين بمخازنكم؟",
            "بكم ذا تكفى وعساك ع القوة",
        ]
        for t in texts:
            dialect, conf = detect_arabic_dialect(t)
            self.assertEqual(dialect, ArabicDialect.GULF, f"Failed for: {t}")
            self.assertGreater(conf, 0.5)

    def test_detect_levantine_dialect(self):
        """Phrases using Levantine dialect markers must be classified as LEVANTINE."""
        texts = [
            "يسعد مساك لو بتريد، بدي اسأل قديش سعر اللابتوب؟",
            "مرحبا يعطيكم العافية، في حسم او تنزيلات هلق؟",
            "شو سعره هيدا التلفون وموجود هلق عندكن؟",
            "حابب اعرف قديش حقه وشكرا الك كتير",
        ]
        for t in texts:
            dialect, conf = detect_arabic_dialect(t)
            self.assertEqual(dialect, ArabicDialect.LEVANTINE, f"Failed for: {t}")
            self.assertGreater(conf, 0.5)

    def test_detect_msa_and_unknown(self):
        """Formal Arabic and non-Arabic inputs must be classified accurately."""
        msa_text = "السلام عليكم ورحمة الله، أود الاستفسار عن توفر المنتج وسعره الرسمي."
        dialect, conf = detect_arabic_dialect(msa_text)
        self.assertEqual(dialect, ArabicDialect.MSA)

        en_text = "Hello, what is the price of this laptop?"
        dialect, conf = detect_arabic_dialect(en_text)
        self.assertEqual(dialect, ArabicDialect.UNKNOWN)
        self.assertEqual(conf, 0.0)

    def test_extract_intents(self):
        """Query intent extraction must capture price, stock, offer, and purchase intents."""
        # Price inquiry
        intents = extract_intents("بكام الشاحن ده؟")
        self.assertIn(QueryIntent.PRICE_INQUIRY, intents)

        # Stock inquiry
        intents = extract_intents("هل متوفر عندكم بالمستودع الحين؟")
        self.assertIn(QueryIntent.STOCK_INQUIRY, intents)

        # Offer & discount inquiry
        intents = extract_intents("فيه عروض أو كود خصم اليوم؟")
        self.assertIn(QueryIntent.OFFER_INQUIRY, intents)

        # Purchase intent
        intents = extract_intents("عايز اشتري واحجز توصيل للبيت")
        self.assertIn(QueryIntent.PURCHASE_INTENT, intents)

        # Multi-intent inquiry
        intents = extract_intents("بكم سعره وهل متوفر الحين وفيه خصم؟")
        self.assertIn(QueryIntent.PRICE_INQUIRY, intents)
        self.assertIn(QueryIntent.STOCK_INQUIRY, intents)
        self.assertIn(QueryIntent.OFFER_INQUIRY, intents)

    def test_isolate_product_search_tokens(self):
        """Dialectal greetings, polite fillers, and question words must be stripped from product terms."""
        cases = [
            (
                "ازيك يا فندم عايز اعرف بكام ايفون 15 برو ماكس ده لو سمحت",
                "ايفون 15 برو ماكس",
            ),
            (
                "هلا والله لاهنت ابغى اعرف كم سعر سوني بلايستيشن 5 الحين",
                "سوني بلايستيشن 5",
            ),
            (
                "يسعد مساك لو بتريد بدي اسأل قديش سعر سماعات ابل ايربودز هيدا",
                "سماعات ابل ايربودز",
            ),
        ]
        for inquiry, expected_token_sub in cases:
            cleaned = isolate_product_search_tokens(inquiry)
            self.assertIn(expected_token_sub, cleaned, f"Expected '{expected_token_sub}' in '{cleaned}'")

    def test_parse_dialectal_inquiry_facade(self):
        """The facade function must return a complete DialectalQueryAnalysis dataclass."""
        analysis = parse_dialectal_inquiry("صباح الخير يا باشا، بكام التيشيرت القطن ده وفيه منه مقاس ميديام؟")
        self.assertEqual(analysis.detected_dialect, ArabicDialect.EGYPTIAN)
        self.assertTrue(analysis.is_price_inquiry)
        self.assertTrue(analysis.is_stock_inquiry)
        self.assertIn("تيشيرت القطن", analysis.cleaned_product_query)
        self.assertIsInstance(analysis.to_dict(), dict)


if __name__ == "__main__":
    unittest.main()
