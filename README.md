# CatalogCopilot

> Catalog-grounded Arabic and English sales assistant with deterministic commercial-claim verification, human-in-the-loop review, and tamper-evident audit receipts.

---

## Key Highlights
- **Strict Catalog Grounding**: Parses CSV and JSON catalogs, validating SKU, pricing, stock levels, and active promotional discounts.
- **Deterministic Claim Verification**: Commercial statements in draft messages are deterministically verified against catalog facts. Zero hallucinated discounts, fabricated stock, or wrong prices.
- **Fail-Closed on Uncertainty**: If a claim cannot be verified deterministically against catalog fields, or if an inquiry is ambiguous, it fails closed to polite clarification and demands human review.
- **Consent-First (No Unsolicited Messages)**: Inquiries without explicit customer consent (`consent_to_contact=True`) are strictly blocked at verification and database levels.
- **Human-in-the-Loop Review**: All draft communications must be approved or edited by an authorized human before export/dispatch. Includes an interactive Terminal UI (`tui`).
- **Dialect Normalization & Fuzzy SKU Resolution**: Native support for Egyptian (لهجة مصرية), Gulf/Saudi (لهجة خليجية), Levantine (لهجة شامية), and MSA. Pure standard-library Damerau-Levenshtein distance, token containment, and Arabic-English tech transliteration dictionary.
- **Chatwoot Integration**: Zero-dependency HTTP webhook listener (`chatwoot-listener`), synchronizing verified proposals as private notes with agent approval commands (`/approve`, `/reject`) and loop prevention.
- **Tamper-Evident Audit Exports**: Generates CSV/JSON exports accompanied by cryptographic SHA-256 receipts (`.receipt.json`) certifying file integrity, record chains, and SQLite immutable audit logs. Includes formula injection protection.
- **Zero External Dependencies**: 100% pure Python standard library runtime (Python 3.10+).

---

## Quickstart

### 1. Installation
Refer to [INSTALL.md](INSTALL.md) for detailed operator guidance.
```bash
python3 -m pip install -e .
```

### 2. Run the Verification & Demonstration Suite
```bash
python3 -m unittest discover tests
python3 examples/run_demo.py
python3 examples/run_dialect_demo.py
```

### 3. CLI Commands
```bash
# Ingest and validate a catalog
catalog-copilot validate-catalog -c examples/demo_catalog.csv

# Process a batch of leads
catalog-copilot run-batch -c examples/demo_catalog.csv -l examples/demo_leads.csv -d queue.db

# Launch interactive Human Review Terminal UI
catalog-copilot tui -d queue.db -c examples/demo_catalog.csv -r "Supervisor Sara"

# Start standalone Chatwoot webhook listener
catalog-copilot chatwoot-listener -c examples/demo_catalog.csv -d queue.db --port 8000

# Export approved messages to dispatch CSV (with formula injection defense)
catalog-copilot export-approved -d queue.db -o dispatch_queue.csv

# Export tamper-evident audit trail with cryptographic receipt
catalog-copilot export-audit -d queue.db -o audit_export.csv --format csv -r "Audit Officer"

# Verify export file against cryptographic receipt and SQLite database
catalog-copilot verify-audit -f audit_export.csv -r audit_export.csv.receipt.json -d queue.db
```

---

## Architecture & Governance
Refer to [AGENTS.md](AGENTS.md) and [LEDGER.md](LEDGER.md) for architectural rules, task breakdown, and execution receipts. Full installation and security guidelines are documented in [INSTALL.md](INSTALL.md).
