"""Tests for Catalog, CatalogLoader, and Arabic normalization."""

import unittest
from catalog_copilot.models import CatalogItem
from catalog_copilot.catalog import Catalog, CatalogLoader, normalize_arabic_text


class TestCatalog(unittest.TestCase):

    def test_catalog_item_creation_and_effective_price(self):
        item = CatalogItem(
            sku="TEA-01",
            title_en="Organic Green Tea",
            title_ar="شاي أخضر عضوي",
            price=50.0,
            currency="SAR",
            stock_quantity=10,
            discount_percent=20.0,
            discount_price=40.0,
        )
        self.assertEqual(item.sku, "TEA-01")
        self.assertEqual(item.effective_price, 40.0)
        self.assertTrue(item.in_stock)

        # Item with explicit discount price (consistent with 25% discount)
        item2 = CatalogItem(
            sku="TEA-02",
            title_en="Earl Grey Tea",
            title_ar="شاي إيرل غراي",
            price=60.0,
            stock_quantity=0,
            discount_percent=25.0,
            discount_price=45.0,
        )
        self.assertEqual(item2.effective_price, 45.0)
        self.assertFalse(item2.in_stock)

    def test_zero_price_allowed(self):
        """MEDIUM REGRESSION: Numeric 0.0 is a valid promotional or free item price."""
        data = [{"sku": "FREE-01", "title_en": "Sample", "title_ar": "عينة", "price": 0.0, "stock_quantity": 5}]
        catalog = CatalogLoader.load_from_dict_list(data)
        item = catalog.get_item("FREE-01")
        self.assertIsNotNone(item)
        self.assertEqual(item.price, 0.0)

    def test_non_finite_numbers_rejected(self):
        """MEDIUM REGRESSION: NaN and Inf prices or discounts must be rejected."""
        bad_nan = [{"sku": "NAN-1", "title_en": "A", "title_ar": "أ", "price": float("nan")}]
        with self.assertRaises(ValueError):
            CatalogLoader.load_from_dict_list(bad_nan)

        bad_inf = [{"sku": "INF-1", "title_en": "A", "title_ar": "أ", "price": float("inf")}]
        with self.assertRaises(ValueError):
            CatalogLoader.load_from_dict_list(bad_inf)

    def test_contradictory_discounts_rejected(self):
        """HIGH REGRESSION: Contradictory discount_percent (10%) and discount_price (50 on base 100) must FAIL."""
        bad_discount = [{
            "sku": "BAD-DISC",
            "title_en": "Mismatch",
            "title_ar": "تناقض",
            "price": 100.0,
            "discount_percent": 10.0,  # implies 90.0
            "discount_price": 50.0,     # contradicts 10%
        }]
        with self.assertRaises(ValueError) as ctx:
            CatalogLoader.load_from_dict_list(bad_discount)
        self.assertIn("contradictory discount_price", str(ctx.exception))

    def test_duplicate_sku_rejection(self):
        catalog = Catalog()
        item1 = CatalogItem(sku="SKU-1", title_en="A", title_ar="أ", price=10)
        catalog.add_item(item1)
        item2 = CatalogItem(sku="sku-1", title_en="B", title_ar="ب", price=20)
        with self.assertRaises(ValueError) as ctx:
            catalog.add_item(item2)
        self.assertIn("Duplicate SKU", str(ctx.exception))

    def test_arabic_normalization(self):
        raw = "إِبْرِيقُ الشَّايِ"
        norm = normalize_arabic_text(raw)
        self.assertEqual(norm, "ابريق الشاي")

        raw2 = "قهوة سعودية"
        norm2 = normalize_arabic_text(raw2)
        self.assertEqual(norm2, "قهوه سعوديه")

        raw3 = "سعر ٤٥ ريال"
        norm3 = normalize_arabic_text(raw3)
        self.assertEqual(norm3, "سعر 45 ريال")

    def test_catalog_search(self):
        catalog = Catalog()
        catalog.add_item(CatalogItem(sku="SKU-AR-1", title_en="Espresso Cup", title_ar="فنجان اسبريسو فاخر", price=25))
        catalog.add_item(CatalogItem(sku="SKU-AR-2", title_en="Drip Pot", title_ar="إبريق تقطير القهوة", price=95))

        matches = catalog.search("اسبريسو", lang="ar")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].sku, "SKU-AR-1")

        matches_en = catalog.search("drip pot", lang="en")
        self.assertEqual(len(matches_en), 1)
        self.assertEqual(matches_en[0].sku, "SKU-AR-2")

    def test_loader_validation_errors(self):
        # Negative price
        bad_data_1 = [{"sku": "B1", "title_en": "E", "title_ar": "ع", "price": -5}]
        with self.assertRaises(ValueError):
            CatalogLoader.load_from_dict_list(bad_data_1)

        # Negative stock
        bad_data_2 = [{"sku": "B2", "title_en": "E", "title_ar": "ع", "price": 10, "stock_quantity": -1}]
        with self.assertRaises(ValueError):
            CatalogLoader.load_from_dict_list(bad_data_2)

        # Missing SKU
        bad_data_3 = [{"sku": "", "title_en": "E", "title_ar": "ع", "price": 10}]
        with self.assertRaises(ValueError):
            CatalogLoader.load_from_dict_list(bad_data_3)


if __name__ == "__main__":
    unittest.main()
