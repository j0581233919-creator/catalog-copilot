"""Tests for spreadsheet formula injection defense and CSV sanitization in CatalogCopilot."""

from __future__ import annotations
import unittest
from catalog_copilot.csv_security import (
    sanitize_csv_cell,
    desanitize_csv_cell,
    sanitize_csv_row,
    desanitize_csv_row,
    is_potentially_dangerous_formula,
)


class TestCSVSecurity(unittest.TestCase):
    """Verifies formula injection defense across various dangerous and benign payloads."""

    def test_benign_values_pass_unmodified(self):
        cases = [
            ("Regular Text", "Regular Text"),
            ("مرحباً بك عميلنا العزيز", "مرحباً بك عميلنا العزيز"),
            ("123", "123"),
            ("123.45", "123.45"),
            ("-5", "-5"),
            ("+10", "+10"),
            ("-120.50", "-120.50"),
            ("+0.99", "+0.99"),
            ("TECH-PHN-01", "TECH-PHN-01"),
            ("iphone 15 pro", "iphone 15 pro"),
            ("", ""),
            (100, "100"),
            (45.5, "45.5"),
            (True, "1"),
            (False, "0"),
            (None, ""),
        ]
        for val, expected in cases:
            with self.subTest(val=val):
                self.assertFalse(is_potentially_dangerous_formula(val))
                self.assertEqual(sanitize_csv_cell(val), expected)

    def test_dangerous_formula_triggers_are_sanitized(self):
        dangerous = [
            "=SUM(A1:A10)",
            "=1+2",
            "=cmd|' /C calc'!A0",
            "+2+3",
            "+cmd|' /C calc'!A0",
            "-2+3",
            "-cmd|' /C notepad'!A0",
            "@SUM(A1:A5)",
            "@import(url)",
            "\tTAB_INJECTED_CMD",
            "\rCR_INJECTED_CMD",
            "=HYPERLINK(\"http://evil.com\",\"Click\")",
            "+HYPERLINK(\"http://evil.com\")",
        ]
        for payload in dangerous:
            with self.subTest(payload=payload):
                self.assertTrue(is_potentially_dangerous_formula(payload))
                sanitized = sanitize_csv_cell(payload)
                self.assertTrue(sanitized.startswith("'"), f"Expected leading quote on: {sanitized}")
                self.assertEqual(sanitized[1:], payload)

    def test_roundtrip_desanitization(self):
        dangerous_payloads = [
            "=1+2",
            "-cmd|' /C calc'!A0",
            "@SUM(1,2)",
            "+4+5",
        ]
        for p in dangerous_payloads:
            sanitized = sanitize_csv_cell(p)
            self.assertEqual(sanitized, f"'{p}")
            desanitized = desanitize_csv_cell(sanitized)
            self.assertEqual(desanitized, p)

    def test_row_sanitization(self):
        row = {
            "customer_name": "=cmd|' /C evil.exe'",
            "sku": "TECH-PHN-01",
            "price": "3499.00",
            "inquiry": "مرحباً.. هل يوجد خصم؟",
            "notes": "@SUM(1,2,3)",
        }
        sanitized = sanitize_csv_row(row)
        self.assertEqual(sanitized["customer_name"], "'=cmd|' /C evil.exe'")
        self.assertEqual(sanitized["sku"], "TECH-PHN-01")
        self.assertEqual(sanitized["price"], "3499.00")
        self.assertEqual(sanitized["inquiry"], "مرحباً.. هل يوجد خصم؟")
        self.assertEqual(sanitized["notes"], "'@SUM(1,2,3)")

        # Desanitize back
        restored = desanitize_csv_row(sanitized)
        self.assertEqual(restored["customer_name"], row["customer_name"])
        self.assertEqual(restored["notes"], row["notes"])


if __name__ == "__main__":
    unittest.main()
