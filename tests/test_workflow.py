"""Tests for BatchWorkflowEngine and offline CSV processing."""

import unittest
import tempfile
import csv
import os
from catalog_copilot.approval import ApprovalLedger
from catalog_copilot.workflow import BatchWorkflowEngine


class TestWorkflow(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "workflow_test.db")
        self.catalog_csv = os.path.join(self.tmp_dir.name, "catalog.csv")
        self.leads_csv = os.path.join(self.tmp_dir.name, "leads.csv")
        self.output_csv = os.path.join(self.tmp_dir.name, "dispatch.csv")

        # Create test catalog CSV
        with open(self.catalog_csv, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["sku", "title_en", "title_ar", "price", "currency", "stock_quantity", "is_active", "discount_percent"])
            writer.writerow(["LAPTOP-X1", "Laptop Pro 16", "حاسوب محمول برو 16", "4500.00", "SAR", "15", "True", "5.0"])
            writer.writerow(["MOUSE-M1", "Wireless Mouse", "فأرة لاسلكية", "75.00", "SAR", "0", "True", "0.0"])

        # Create test leads CSV
        with open(self.leads_csv, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["lead_id", "customer_name", "contact_channel", "contact_target", "consent_to_contact", "preferred_language", "inquiry_text", "requested_sku"])
            writer.writerow(["LEAD-01", "Hassan", "email", "hassan@example.com", "True", "en", "Inquiry about Laptop", "LAPTOP-X1"])
            writer.writerow(["LEAD-02", "ريم", "whatsapp", "+966512345678", "True", "ar", "استفسار عن الفأرة", "MOUSE-M1"])
            writer.writerow(["LEAD-03", "NoConsent", "email", "no@example.com", "False", "en", "Spam attempt", "LAPTOP-X1"])

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_batch_pipeline_and_export(self):
        ledger = ApprovalLedger(db_path=self.db_path)
        engine = BatchWorkflowEngine(ledger=ledger)

        stats = engine.process_batch(catalog_path=self.catalog_csv, leads_path=self.leads_csv)
        self.assertEqual(stats["total_leads"], 3)
        self.assertEqual(stats["passed_verification"], 2)  # LEAD-01 (in-stock) and LEAD-02 (accurately notes out-of-stock)
        self.assertEqual(stats["failed_verification"], 1)  # LEAD-03 (fails consent)

        drafts = ledger.list_drafts()
        self.assertEqual(len(drafts), 3)

        # Approve LEAD-01
        d_laptop = [d for d in drafts if d.lead_id == "LEAD-01"][0]
        ledger.approve(d_laptop.draft_id, reviewer="Supervisor Tariq", notes="Approved for dispatch")

        # Export approved
        exported_count = engine.export_approved_to_csv(output_csv_path=self.output_csv)
        self.assertEqual(exported_count, 1)

        with open(self.output_csv, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["lead_id"], "LEAD-01")
            self.assertEqual(rows[0]["customer_name"], "Hassan")
            self.assertEqual(rows[0]["sku"], "LAPTOP-X1")
            self.assertIn("Laptop Pro 16", rows[0]["body"])


if __name__ == "__main__":
    unittest.main()
