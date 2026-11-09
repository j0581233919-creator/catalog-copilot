# CatalogCopilot

> Catalog-grounded Arabic and English sales assistant with deterministic commercial-claim verification and human-in-the-loop approval.

---

## Key Highlights
- **Strict Catalog Grounding**: Parses CSV and JSON catalogs, validating SKU, pricing, stock levels, and active promotional discounts.
- **Deterministic Claim Verification**: Commercial statements in draft messages are deterministically verified against catalog facts. Zero hallucinated discounts, fabricated stock, or wrong prices.
- **Fail-Closed on Uncertainty**: If a claim cannot be verified deterministically against catalog fields, it fails closed and demands human review.
- **Consent-First (No Unsolicited Messages)**: Inquiries without explicit customer consent (`consent_to_contact=True`) are immediately blocked.
- **Human-in-the-Loop Review**: All draft communications must be approved or edited by an authorized human before export/dispatch.
- **Bilingual Core**: Native Modern Standard Arabic (العربية) and English support with Arabic-Indic numeral compatibility.
- **Offline Batch Processing**: Ingests lead inquiry batches from CSV, outputs verified drafts to an auditable SQLite store, and exports approved messages for CRM or messaging queues.

---

## Quickstart

### 1. Installation
Run locally using Python 3.10+:
```bash
python3 -m pip install -e .
```

### 2. Run the Verification & Demonstration Suite
```bash
python3 -m unittest discover -s tests
catalog-copilot demo
```

### 3. CLI Commands
```bash
# Ingest and validate a catalog
catalog-copilot validate-catalog --catalog examples/demo_catalog.csv

# Process a batch of leads
catalog-copilot run-batch --catalog examples/demo_catalog.csv --leads examples/demo_leads.csv --db queue.db

# Review pending drafts in the database
catalog-copilot review --db queue.db --list
catalog-copilot review --db queue.db --approve DRAFT-001 --reviewer "Ahmed"
catalog-copilot review --db queue.db --reject DRAFT-002 --reviewer "Sara" --reason "Lead requested custom bundle not in catalog"

# Export approved messages to dispatch queue
catalog-copilot export-approved --db queue.db --output dispatch_queue.csv
```

---

## Architecture & Governance
Refer to [AGENTS.md](AGENTS.md) and [LEDGER.md](LEDGER.md) for architecture rules, task breakdown, and execution receipts.
