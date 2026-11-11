# CatalogCopilot Installation & Operator Guide

CatalogCopilot is a catalog-grounded, bilingual (Arabic/English) sales assistant designed with deterministic commercial claim verification, mandatory customer consent guardrails, human-in-the-loop review, and tamper-evident audit receipts.

---

## 1. System Requirements

- **Operating System:** Linux / POSIX (e.g., Ubuntu, Debian, Pop!_OS, macOS, or WSL2)
- **Python Runtime:** Python 3.10 or higher
- **External Dependencies:** **Zero (0)**. CatalogCopilot is built exclusively with the Python Standard Library (`sqlite3`, `csv`, `json`, `hashlib`, `hmac`, `http.server`, `urllib`, `re`, `dataclasses`, `enum`).

---

## 2. Installation

### Development Mode (Editable Install)
```bash
cd "/media/islam/shared pop/GitHub/catalog-copilot/repo"
pip install -e .
```

### Production Install
```bash
cd "/media/islam/shared pop/GitHub/catalog-copilot/repo"
pip install .
```

### Direct Execution Without Pip Install
CatalogCopilot can be run directly using Python's `-m` switch from within the repository root:
```bash
python3 -m catalog_copilot.cli --help
```

---

## 3. CLI Command Reference

### Catalog Validation
Validates merchant catalog schema, price non-negativity, stock integers, and active promotion status:
```bash
catalog-copilot validate-catalog -c examples/demo_catalog.csv
```

### Batch Ingestion & Offline Processing
Ingests customer leads, performs dialect classification and fuzzy SKU resolution, and generates deterministically verified drafts stored in SQLite:
```bash
catalog-copilot run-batch -c examples/demo_catalog.csv -l examples/demo_leads.csv -d catalog_copilot.db
```

### Interactive Human Review TUI
Launches the keyboard-driven terminal review interface:
```bash
catalog-copilot tui -d catalog_copilot.db -c examples/demo_catalog.csv -r "Supervisor Sara"
```
Keybindings:
- `[A]` — Approve draft for outbound dispatch
- `[R]` — Reject draft with mandatory immutable audit reason
- `[E]` — Inline edit draft text and deterministically re-verify against catalog
- `[N]` / `[P]` — Navigate next / previous draft
- `[Q]` — Quit review session

### Chatwoot Webhook Listener
Starts the standalone zero-dependency HTTP webhook listener for live WhatsApp, Email, and Chat conversations:
```bash
catalog-copilot chatwoot-listener \
  -c examples/demo_catalog.csv \
  -d catalog_copilot.db \
  --port 8000 \
  --secret "your_webhook_hmac_secret"
```

### Approved Messages Export
Exports approved drafts to dispatch queue CSV with formula injection sanitization and companion manifest:
```bash
catalog-copilot export-approved -d catalog_copilot.db -o dispatch_queue.csv
```

### Tamper-Evident Audit Export
Exports the complete audit ledger (drafts and immutable review history) with accompanying SHA-256 cryptographic receipt:
```bash
catalog-copilot export-audit -d catalog_copilot.db -o audit_export.csv --format csv -s all -r "Audit Officer"
```
Outputs:
- `audit_export.csv`: Sanitized export file.
- `audit_export.csv.receipt.json`: Provenance receipt containing file SHA-256, record chain digest, and SQLite audit events hash.

### Audit Receipt Verification
Mathematically verifies an export payload against its receipt and SQLite database:
```bash
catalog-copilot verify-audit -f audit_export.csv -r audit_export.csv.receipt.json -d catalog_copilot.db
```

---

## 4. Security & Safety Model

1. **Deterministic Claim Verification**: No commercial statement (SKU, price, stock, discount, currency) is ever approved probabilistically. Every fact is verified against catalog ground truth.
2. **Mandatory Consent Invariant**: Leads without explicit opt-in consent (`consent_to_contact == True`) fail closed with `FAIL`. Approval is blocked at both UI and SQLite database trigger levels.
3. **Fail-Closed Ambiguity**: Inquiries matching multiple products with close confidence scores (`margin < 0.12`) fail closed to clarification drafts and never guess prices.
4. **CSV / Formula Injection Defense**: All exported cells beginning with `=`, `+`, `-`, `@`, `\t`, `\r` are neutralized with single quotes (`'`) to protect downstream spreadsheet users.
5. **Rate Limiting**: Sliding-window rate limiting protects Chatwoot webhook endpoints and channels from message flooding.
6. **Immutable SQLite Audit Trail**: Database triggers prevent modification (`UPDATE`) or deletion (`DELETE`) of audit log rows.

---

## 5. Explicit Commercial Scope & Limitations

- **OCR is Not Supported**: Catalogs and leads must be structured CSV or JSON files. Image OCR is not performed.
- **Currency Conversion**: CatalogCopilot does not fabricate live exchange rates. Multi-currency transactions must use explicitly configured rates or preserve original currency specifications.
- **Audit Receipt Scope**: Cryptographic receipts verify bit-level file integrity and local SQLite ledger alignment. They do not constitute external third-party digital signature certifications or sovereign compliance stamps.
