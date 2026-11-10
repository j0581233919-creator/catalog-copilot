"""End-to-end behavioral tests for CatalogCopilot CLI and workflows."""

import unittest
import tempfile
import os
import subprocess
import sys
import csv

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class TestEndToEnd(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.env = os.environ.copy()
        self.env["PYTHONPATH"] = PROJECT_ROOT

        # Sample catalog CSV
        self.catalog_path = os.path.join(self.tmp_dir.name, "e2e_catalog.csv")
        with open(self.catalog_path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["sku", "title_en", "title_ar", "price", "currency", "stock_quantity", "is_active", "discount_percent", "offer_description_en", "offer_description_ar"])
            w.writerow(["SKU-WATCH-01", "Titanium Chrono Watch", "ساعة تيتانيوم كرونوغراف فاخرة", "1500.00", "SAR", "8", "True", "10.0", "Save 10% on release", "وفر 10% بمناسبة الإطلاق"])
            w.writerow(["SKU-STRAP-02", "Leather Strap 22mm", "سوار جلدي أصلي 22 مم", "120.00", "SAR", "0", "True", "0.0", "", ""])

        # Sample leads CSV
        self.leads_path = os.path.join(self.tmp_dir.name, "e2e_leads.csv")
        with open(self.leads_path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["lead_id", "customer_name", "contact_channel", "contact_target", "consent_to_contact", "consent_evidence", "preferred_language", "inquiry_text", "requested_sku"])
            w.writerow(["E2E-L1", "عبدالله السالم", "whatsapp", "+966501234567", "True", "Web opt-in checkbox 2026-11-09", "ar", "كم سعر ساعة التيتانيوم؟", "SKU-WATCH-01"])
            w.writerow(["E2E-L2", "John Doe", "email", "john@example.com", "True", "Inbound email header 2026-11-09", "en", "Interested in 22mm strap", "SKU-STRAP-02"])

        self.db_path = os.path.join(self.tmp_dir.name, "e2e.db")
        self.dispatch_csv = os.path.join(self.tmp_dir.name, "e2e_dispatch.csv")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def run_cli(self, args):
        cmd = [sys.executable, "-m", "catalog_copilot.cli"] + args
        return subprocess.run(cmd, env=self.env, capture_output=True, text=True)

    def test_cli_demo(self):
        res = self.run_cli(["demo"])
        self.assertEqual(res.returncode, 0, f"CLI demo failed with stderr:\n{res.stderr}")
        self.assertIn("Demonstration completed with 100% adherence to all commercial guardrails", res.stdout)
        self.assertIn("COFFEE-ESP-01", res.stdout)

    def test_cli_full_lifecycle(self):
        # 1. Validate catalog
        res_val = self.run_cli(["validate-catalog", "--catalog", self.catalog_path])
        self.assertEqual(res_val.returncode, 0)
        self.assertIn("Catalog successfully loaded and validated", res_val.stdout)

        # 2. Run batch
        res_batch = self.run_cli(["run-batch", "--catalog", self.catalog_path, "--leads", self.leads_path, "--db", self.db_path])
        self.assertEqual(res_batch.returncode, 0)
        self.assertIn("Batch processing completed successfully", res_batch.stdout)
        self.assertIn("Total leads:          2", res_batch.stdout)

        # 3. List drafts
        res_list = self.run_cli(["review", "--db", self.db_path, "--list"])
        self.assertEqual(res_list.returncode, 0)
        self.assertIn("E2E-L1", res_list.stdout)
        self.assertIn("E2E-L2", res_list.stdout)

        # Extract draft ID for E2E-L1 from db
        from catalog_copilot.approval import ApprovalLedger
        ledger = ApprovalLedger(db_path=self.db_path)
        drafts = ledger.list_drafts()
        d_watch = [d for d in drafts if d.lead_id == "E2E-L1"][0]

        # 4. Show draft details
        res_show = self.run_cli(["review", "--db", self.db_path, "--show", d_watch.draft_id])
        self.assertEqual(res_show.returncode, 0)
        self.assertIn("ساعة تيتانيوم كرونوغراف فاخرة", res_show.stdout)
        self.assertIn("1350.00 SAR", res_show.stdout)  # 1500 - 10% = 1350.00 SAR

        # 5. Approve draft
        res_app = self.run_cli(["review", "--db", self.db_path, "--approve", d_watch.draft_id, "--reviewer", "Supervisor Nader", "--notes", "Verified Arabic launch pricing"])
        self.assertEqual(res_app.returncode, 0)
        self.assertIn("successfully APPROVED", res_app.stdout)

        # 6. Export approved
        res_exp = self.run_cli(["export-approved", "--db", self.db_path, "--output", self.dispatch_csv])
        self.assertEqual(res_exp.returncode, 0)
        self.assertIn("Exported 1 approved messages", res_exp.stdout)

        # 7. Check export CSV
        with open(self.dispatch_csv, "r", encoding="utf-8") as f:
            reader = list(csv.DictReader(f))
            self.assertEqual(len(reader), 1)
            self.assertEqual(reader[0]["lead_id"], "E2E-L1")
            self.assertEqual(reader[0]["customer_name"], "عبدالله السالم")
            self.assertEqual(reader[0]["reviewed_by"], "Supervisor Nader")


if __name__ == "__main__":
    unittest.main()
