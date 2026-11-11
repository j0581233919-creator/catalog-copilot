"""Tests for release packaging, public API exports, and CLI commands in CatalogCopilot."""

from __future__ import annotations
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import io

import catalog_copilot
from catalog_copilot.cli import main


class TestPackagingAndCLI(unittest.TestCase):
    """Verifies package integrity, metadata consistency, and CLI command execution."""

    def test_version_consistency(self):
        self.assertEqual(catalog_copilot.__version__, "0.2.0")

        # Check pyproject.toml matches
        repo_root = Path(__file__).resolve().parent.parent
        pyproject_path = repo_root / "pyproject.toml"
        self.assertTrue(pyproject_path.exists())

        with open(pyproject_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn('version = "0.2.0"', content)

    def test_public_api_exports(self):
        expected_symbols = [
            "CatalogItem",
            "LeadInquiry",
            "ClaimType",
            "ClaimVerification",
            "VerificationStatus",
            "VerificationReport",
            "ApprovalStatus",
            "DraftMessage",
            "CatalogLoader",
            "Catalog",
            "CommercialClaimVerifier",
            "DraftGenerator",
            "ApprovalLedger",
            "BatchWorkflowEngine",
            "HumanReviewTUI",
            "TerminalFormatter",
            "ChatwootApiClient",
            "ChatwootWebhookHandler",
            "MockChatwootTransport",
            "create_chatwoot_server",
            "ArabicDialect",
            "QueryIntent",
            "DialectalQueryAnalysis",
            "parse_dialectal_inquiry",
            "detect_arabic_dialect",
            "clean_elongation",
            "isolate_product_search_tokens",
            "MatchType",
            "FuzzyCandidate",
            "ResolutionResult",
            "FuzzyCatalogMatcher",
            "sanitize_csv_cell",
            "desanitize_csv_cell",
            "sanitize_csv_row",
            "desanitize_csv_row",
            "RateLimiter",
            "AuditReceipt",
            "AuditExporter",
            "verify_audit_export",
            "AuditVerificationResult",
        ]
        for sym in expected_symbols:
            with self.subTest(symbol=sym):
                self.assertTrue(hasattr(catalog_copilot, sym), f"Missing public symbol: {sym}")
                self.assertIn(sym, catalog_copilot.__all__)

    def test_cli_validate_catalog_command(self):
        repo_root = Path(__file__).resolve().parent.parent
        catalog_path = str(repo_root / "examples" / "demo_catalog.csv")

        test_args = ["catalog-copilot", "validate-catalog", "-c", catalog_path]
        with patch.object(sys, "argv", test_args):
            with patch("sys.stdout", new=io.StringIO()) as fake_out:
                with self.assertRaises(SystemExit) as cm:
                    main()
                self.assertEqual(cm.exception.code, 0)
                out = fake_out.getvalue()
                self.assertIn("Catalog successfully loaded and validated", out)

    def test_cli_export_and_verify_audit_commands(self):
        repo_root = Path(__file__).resolve().parent.parent
        catalog_path = str(repo_root / "examples" / "demo_catalog.csv")
        leads_path = str(repo_root / "examples" / "demo_leads.csv")

        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = str(Path(tmp_dir) / "cli_test.db")
            audit_csv = str(Path(tmp_dir) / "cli_audit.csv")

            # 1. Run batch to populate DB
            batch_args = ["catalog-copilot", "run-batch", "-c", catalog_path, "-l", leads_path, "-d", db_path]
            with patch.object(sys, "argv", batch_args):
                with patch("sys.stdout", new=io.StringIO()):
                    with self.assertRaises(SystemExit) as cm:
                        main()
                    self.assertEqual(cm.exception.code, 0)

            # 2. Export audit
            export_args = ["catalog-copilot", "export-audit", "-d", db_path, "-o", audit_csv, "--format", "csv", "-r", "QA Lead"]
            with patch.object(sys, "argv", export_args):
                with patch("sys.stdout", new=io.StringIO()) as fake_out:
                    with self.assertRaises(SystemExit) as cm:
                        main()
                    self.assertEqual(cm.exception.code, 0)
                    out = fake_out.getvalue()
                    self.assertIn("CATALOGCOPILOT AUDIT EXPORT SUCCESSFUL", out)
                    self.assertIn("File SHA-256", out)

            # 3. Verify audit
            verify_args = ["catalog-copilot", "verify-audit", "-f", audit_csv, "-d", db_path]
            with patch.object(sys, "argv", verify_args):
                with patch("sys.stdout", new=io.StringIO()) as fake_out:
                    with self.assertRaises(SystemExit) as cm:
                        main()
                    self.assertEqual(cm.exception.code, 0)
                    out = fake_out.getvalue()
                    self.assertIn("VERIFICATION RESULT: [PASS]", out)


if __name__ == "__main__":
    unittest.main()
