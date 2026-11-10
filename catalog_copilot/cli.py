"""Command Line Interface for CatalogCopilot."""

from __future__ import annotations
import argparse
import sys
import json
from pathlib import Path

from .models import ApprovalStatus, VerificationStatus
from .catalog import CatalogLoader
from .verifier import CommercialClaimVerifier
from .generator import DraftGenerator
from .approval import ApprovalLedger
from .workflow import BatchWorkflowEngine
from .tui import HumanReviewTUI


def cmd_validate_catalog(args: argparse.Namespace) -> int:
    path = Path(args.catalog)
    try:
        if path.suffix.lower() == ".json":
            catalog = CatalogLoader.load_json(path)
        else:
            catalog = CatalogLoader.load_csv(path)
        summary = catalog.summary()
        print(f"Catalog successfully loaded and validated: {path}")
        print(f"  Total items:        {summary['total_items']}")
        print(f"  Active items:       {summary['active_items']}")
        print(f"  In-stock items:     {summary['in_stock_items']}")
        print(f"  Out-of-stock items: {summary['out_of_stock_items']}")
        return 0
    except Exception as e:
        print(f"Catalog validation failed: {e}", file=sys.stderr)
        return 1


def cmd_run_batch(args: argparse.Namespace) -> int:
    try:
        ledger = ApprovalLedger(db_path=args.db)
        engine = BatchWorkflowEngine(ledger=ledger)
        stats = engine.process_batch(catalog_path=args.catalog, leads_path=args.leads)

        print("Batch processing completed successfully:")
        print(f"  Total leads:          {stats['total_leads']}")
        print(f"  Passed verification:  {stats['passed_verification']}")
        print(f"  Failed verification:  {stats['failed_verification']}")
        print(f"  Uncertain:            {stats['uncertain_verification']}")
        print(f"  Drafts stored in DB:  {stats['drafts_queued']} ({args.db})")
        return 0
    except Exception as e:
        print(f"Batch processing error: {e}", file=sys.stderr)
        return 1


def cmd_review(args: argparse.Namespace) -> int:
    try:
        ledger = ApprovalLedger(db_path=args.db)

        if getattr(args, "interactive", False):
            catalog = None
            if getattr(args, "catalog", None):
                c_path = Path(args.catalog)
                catalog = CatalogLoader.load_json(c_path) if c_path.suffix.lower() == ".json" else CatalogLoader.load_csv(c_path)
            tui = HumanReviewTUI(
                ledger=ledger,
                catalog=catalog,
                reviewer=getattr(args, "reviewer", None),
            )
            return tui.run()

        if args.show:
            draft = ledger.get_draft(args.show)
            if not draft:
                print(f"Draft '{args.show}' not found.", file=sys.stderr)
                return 1
            print(f"--- DRAFT {draft.draft_id} ---")
            print(f"Lead ID:      {draft.lead_id} ({draft.customer_name})")
            print(f"Channel:      {draft.contact_channel} -> {draft.contact_target}")
            print(f"SKU:          {draft.sku or 'N/A'}")
            print(f"Language:     {draft.language}")
            print(f"Verification: {draft.verification_report.status.value}")
            for c in draft.verification_report.checks:
                status_icon = "PASS" if c.is_valid else "FAIL"
                print(f"  [{status_icon}] {c.claim_type.value}: {c.notes}")
            print(f"Approval:     {draft.approval_status.value}")
            if draft.reviewed_by:
                print(f"Reviewed By:  {draft.reviewed_by} at {draft.reviewed_at}")
            if draft.reviewer_notes:
                print(f"Review Notes: {draft.reviewer_notes}")
            print(f"\nSubject: {draft.subject}")
            print(f"Body:\n{draft.body}\n")
            return 0

        if args.approve:
            if not args.reviewer:
                print("Error: --reviewer name is required to approve a draft.", file=sys.stderr)
                return 1
            draft = ledger.approve(args.approve, reviewer=args.reviewer, notes=args.notes)
            print(f"Draft '{draft.draft_id}' successfully APPROVED by {draft.reviewed_by}.")
            return 0

        if args.reject:
            if not args.reviewer:
                print("Error: --reviewer name is required to reject a draft.", file=sys.stderr)
                return 1
            if not args.reason:
                print("Error: --reason is required when rejecting a draft.", file=sys.stderr)
                return 1
            draft = ledger.reject(args.reject, reviewer=args.reviewer, reason=args.reason)
            print(f"Draft '{draft.draft_id}' REJECTED by {draft.reviewed_by}. Reason: {draft.reviewer_notes}")
            return 0

        # Default action: list drafts
        drafts = ledger.list_drafts()
        if not drafts:
            print(f"No drafts found in {args.db}.")
            return 0

        print(f"Drafts in queue ({len(drafts)} total):")
        print(f"{'Draft ID':<16} {'Lead ID':<12} {'SKU':<12} {'Lang':<6} {'Verif':<10} {'Approval Status':<18}")
        print("-" * 76)
        for d in drafts:
            sku_str = d.sku or "-"
            print(f"{d.draft_id:<16} {d.lead_id:<12} {sku_str:<12} {d.language:<6} {d.verification_report.status.value:<10} {d.approval_status.value:<18}")
        return 0

    except Exception as e:
        print(f"Review error: {e}", file=sys.stderr)
        return 1


def cmd_tui(args: argparse.Namespace) -> int:
    try:
        ledger = ApprovalLedger(db_path=args.db)
        catalog = None
        if getattr(args, "catalog", None):
            c_path = Path(args.catalog)
            catalog = CatalogLoader.load_json(c_path) if c_path.suffix.lower() == ".json" else CatalogLoader.load_csv(c_path)
        status_filter = None if getattr(args, "all", False) else ApprovalStatus.PENDING_REVIEW
        tui = HumanReviewTUI(
            ledger=ledger,
            catalog=catalog,
            reviewer=getattr(args, "reviewer", None),
            status_filter=status_filter,
        )
        return tui.run()
    except Exception as e:
        print(f"TUI error: {e}", file=sys.stderr)
        return 1


def cmd_export(args: argparse.Namespace) -> int:
    try:
        ledger = ApprovalLedger(db_path=args.db)
        engine = BatchWorkflowEngine(ledger=ledger)
        count = engine.export_approved_to_csv(
            output_csv_path=args.output,
            mark_as_exported=not args.no_dispatch,
            overwrite=args.overwrite,
        )
        print(f"Exported {count} approved messages to {args.output}")
        return 0
    except Exception as e:
        print(f"Export error: {e}", file=sys.stderr)
        return 1


def cmd_demo(args: argparse.Namespace) -> int:
    """Zero-dependency self-contained interactive demonstration."""
    print("=" * 70)
    print(" CatalogCopilot: End-to-End Demonstration")
    print("=" * 70)

    # 1. Ingest Catalog
    sample_catalog_data = [
        {
            "sku": "COFFEE-ESP-01",
            "title_en": "Italian Espresso Roast Coffee Beans (1kg)",
            "title_ar": "حبوب قهوة اسبريسو إيطالية فاخرة (1 كجم)",
            "price": 120.00,
            "currency": "SAR",
            "stock_quantity": 45,
            "is_active": True,
            "discount_percent": 10.0,
            "offer_description_en": "Buy 1 get 10% off launch special",
            "offer_description_ar": "عرض الإطلاق الخاص: خصم 10%",
            "category": "Beverages",
        },
        {
            "sku": "COFFEE-COL-02",
            "title_en": "Colombian Geisha Single Origin (250g)",
            "title_ar": "قهوة جيشا كولومبية مختصة (250 جم)",
            "price": 250.00,
            "currency": "SAR",
            "stock_quantity": 0,  # Out of stock
            "is_active": True,
            "discount_percent": 0.0,
            "category": "Beverages",
        },
        {
            "sku": "SYRUP-VAN-03",
            "title_en": "Artisan Vanilla Syrup (750ml)",
            "title_ar": "سيروب فانيلا حرفي مركز (750 مل)",
            "price": 65.00,
            "currency": "SAR",
            "stock_quantity": 18,
            "is_active": True,
            "category": "Syrups",
        },
    ]

    catalog = CatalogLoader.load_from_dict_list(sample_catalog_data)
    print(f"\n[1] Ingested Catalog: {catalog.total_count} items ({catalog.in_stock_count} in-stock, {catalog.total_count - catalog.in_stock_count} out-of-stock).")

    # 2. Leads: 1 authorized in Arabic, 1 authorized out-of-stock in English, 1 lead WITHOUT consent
    from .models import LeadInquiry
    leads = [
        LeadInquiry(
            lead_id="LEAD-101",
            customer_name="فيصل العتيبي",
            contact_channel="whatsapp",
            contact_target="+966501112233",
            consent_to_contact=True,
            consent_evidence="Web opt-in checkbox 2026-11-09T08:00:00Z target:+966501112233",
            preferred_language="ar",
            inquiry_text="أرغب في الاستفسار عن قهوة الاسبريسو وسعرها مع الخصم",
            requested_sku="COFFEE-ESP-01",
        ),
        LeadInquiry(
            lead_id="LEAD-102",
            customer_name="Sarah Miller",
            contact_channel="email",
            contact_target="sarah.m@example.com",
            consent_to_contact=True,
            consent_evidence="Inbound email inquiry consent header target:sarah.m@example.com",
            preferred_language="en",
            inquiry_text="Do you have Colombian Geisha in stock right now?",
            requested_sku="COFFEE-COL-02",
        ),
        LeadInquiry(
            lead_id="LEAD-103",
            customer_name="خالد الناصر",
            contact_channel="sms",
            contact_target="+966509998877",
            consent_to_contact=False,  # NO CONSENT - must fail verification!
            preferred_language="ar",
            inquiry_text="عرض القهوة الاسبريسو",
            requested_sku="COFFEE-ESP-01",
        ),
    ]

    print(f"\n[2] Ingested {len(leads)} sample leads with strict consent attributes.")

    # 3. Generate and Verify
    generator = DraftGenerator()
    import tempfile
    import os
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        tmp_db_path = tmp_db.name

    try:
        ledger = ApprovalLedger(db_path=tmp_db_path)
        drafts = []

        print("\n[3] Generating and deterministically verifying commercial claims:")
        for lead in leads:
            draft = generator.generate_draft(lead=lead, catalog=catalog)
            ledger.save_draft(draft)
            drafts.append(draft)
            status_symbol = "PASS" if draft.verification_report.status == VerificationStatus.PASS else "FAIL"
            print(f"  Lead: {lead.lead_id:<9} ({lead.customer_name}) -> Verification: [{status_symbol}] ({draft.verification_report.status.value})")
            for c in draft.verification_report.checks:
                flag = "OK" if c.is_valid else "X "
                print(f"    [{flag}] {c.claim_type.value}: {c.notes}")

        # 4. Human Approval Workflow
        print("\n[4] Human-in-the-Loop Review Decisions:")
        # Approve first lead
        approved = ledger.approve(drafts[0].draft_id, reviewer="Supervisor Omar", notes="Verified 10% discount and stock.")
        print(f"  Approved: {approved.draft_id} for {approved.customer_name}")

        # Reject second lead (out-of-stock, wait for restock)
        rejected = ledger.reject(drafts[1].draft_id, reviewer="Supervisor Omar", reason="Wait for next week's container arrival before messaging.")
        print(f"  Rejected: {rejected.draft_id} for {rejected.customer_name}. Reason: {rejected.reviewer_notes}")

        # Third lead cannot be approved OR edited-and-approved because consent failed!
        try:
            ledger.approve(drafts[2].draft_id, reviewer="Supervisor Omar")
            print("  ERROR: Lead without consent was unexpectedly approved!")
        except ValueError as err:
            print(f"  Blocked illegal approval as expected: {err}")

        try:
            ledger.edit_and_approve(drafts[2].draft_id, new_body="Hello", reviewer="Supervisor Omar", catalog=catalog)
            print("  ERROR: Lead without consent was unexpectedly edited and approved!")
        except ValueError as err:
            print(f"  Blocked illegal edit-and-approval as expected: {err}")

        # 5. Export Approved
        dispatch_file = tmp_db_path.replace(".db", "_dispatch.csv")
        engine = BatchWorkflowEngine(ledger=ledger)
        exported_count = engine.export_approved_to_csv(dispatch_file)
        print(f"\n[5] Dispatch Export: {exported_count} message(s) exported to {dispatch_file}")
        with open(dispatch_file, "r", encoding="utf-8") as df:
            print(f"  Export content:\n{df.read().strip()}")

        print("\n[6] Demonstration completed with 100% adherence to all commercial guardrails.")
        return 0
    finally:
        if os.path.exists(tmp_db_path):
            os.remove(tmp_db_path)
        if os.path.exists(tmp_db_path.replace(".db", "_dispatch.csv")):
            os.remove(tmp_db_path.replace(".db", "_dispatch.csv"))


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="catalog-copilot",
        description="Catalog-grounded Arabic/English sales assistant with deterministic commercial claim verification.",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # validate-catalog
    p_val = subparsers.add_parser("validate-catalog", help="Validate a catalog CSV or JSON file")
    p_val.add_argument("--catalog", "-c", required=True, help="Path to catalog CSV/JSON")
    p_val.set_defaults(func=cmd_validate_catalog)

    # run-batch
    p_batch = subparsers.add_parser("run-batch", help="Run offline batch lead processing")
    p_batch.add_argument("--catalog", "-c", required=True, help="Path to catalog CSV/JSON")
    p_batch.add_argument("--leads", "-l", required=True, help="Path to leads CSV")
    p_batch.add_argument("--db", "-d", default="catalog_copilot.db", help="Path to SQLite queue DB")
    p_batch.set_defaults(func=cmd_run_batch)

    # review
    p_rev = subparsers.add_parser("review", help="Review and manage pending drafts")
    p_rev.add_argument("--db", "-d", default="catalog_copilot.db", help="Path to SQLite queue DB")
    p_rev.add_argument("--catalog", "-c", help="Path to catalog CSV/JSON (for TUI inline edit re-verification)")
    p_rev.add_argument("--interactive", "-i", action="store_true", help="Launch interactive human review TUI")
    p_rev.add_argument("--list", action="store_true", help="List all drafts")
    p_rev.add_argument("--show", help="Show full details of a single draft ID")
    p_rev.add_argument("--approve", help="Approve draft ID")
    p_rev.add_argument("--reject", help="Reject draft ID")
    p_rev.add_argument("--reviewer", help="Reviewer full name or ID")
    p_rev.add_argument("--reason", help="Reason for rejection")
    p_rev.add_argument("--notes", help="Review notes")
    p_rev.set_defaults(func=cmd_review)

    # tui
    p_tui = subparsers.add_parser("tui", help="Launch interactive human review terminal interface")
    p_tui.add_argument("--db", "-d", default="catalog_copilot.db", help="Path to SQLite queue DB")
    p_tui.add_argument("--catalog", "-c", help="Path to catalog CSV/JSON (required for inline edit re-verification)")
    p_tui.add_argument("--reviewer", "-r", help="Reviewer full name or identity")
    p_tui.add_argument("--all", action="store_true", help="Review all drafts, not just pending")
    p_tui.set_defaults(func=cmd_tui)

    # export-approved
    p_exp = subparsers.add_parser("export-approved", help="Export approved messages to CSV")
    p_exp.add_argument("--db", "-d", default="catalog_copilot.db", help="Path to SQLite queue DB")
    p_exp.add_argument("--output", "-o", default="dispatch_queue.csv", help="Output CSV path")
    p_exp.add_argument("--no-dispatch", action="store_true", help="Do not mark exported items as dispatched")
    p_exp.add_argument("--overwrite", action="store_true", help="Overwrite destination CSV if it already exists")
    p_exp.set_defaults(func=cmd_export)

    # demo
    p_demo = subparsers.add_parser("demo", help="Run interactive verification demonstration")
    p_demo.set_defaults(func=cmd_demo)

    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)

    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
