# Security Policy

## Scope & Operational Context

`catalog-copilot` is an in-process Python library and command-line sales assistant designed to generate catalog-grounded product responses, verify commercial assertions against authorized structured catalogs, enforce human-approval gates for high-stakes actions, and export tamper-evident audit receipts.

It is designed for execution within trusted application boundaries (e.g., behind an e-commerce backend, CRM connector, or enterprise sales workbench). It is **not** an independently secured public network service, multi-tenant authentication gate, or cryptographic legal notary.

## Security Guarantees & Defenses

1. **Deterministic Catalog Grounding**:
   - The assistant only asserts pricing, stock availability, discount thresholds, and technical specifications that exist within the catalog.
   - Ambiguous or out-of-catalog inquiries trigger explicit safe refusals or clarification prompts rather than fabricated assertions.

2. **CSV Injection & Export Safety**:
   - All exported CSV records (audit receipts, quote summaries, verification logs) escape dangerous formula trigger prefixes (`=`, `+`, `-`, `@`, `\t`, `\r`) with leading single quotes (`'`) to prevent formula execution in Microsoft Excel, LibreOffice Calc, or Google Sheets.

3. **Tamper-Evident Audit Digests**:
   - All session state transitions, quote calculations, and export operations compute SHA-256 integrity digests over canonical serialized representations.
   - While these digests verify data integrity against post-facto tampering, they do **not** constitute asymmetric cryptographic legal signatures or government regulatory compliance seals.

4. **Human-in-the-Loop Authority Boundaries**:
   - Sensitive commercial actions (discount approvals exceeding policy thresholds, order commitments, binding sales quotations) produce an `APPROVAL_REQUIRED` state and cannot be marked executed without explicit operator confirmation.

5. **Pure Standard Library Runtime**:
   - The core library requires zero third-party PyPI dependencies, eliminating external supply-chain vulnerabilities, unpinned dependencies, or arbitrary native binary execution.

## Reporting a Vulnerability

If you discover a security vulnerability or weakness in `catalog-copilot`, please report it responsibly:
- Email: `security@localhost` (or contact the repository maintainers via GitHub Security Advisories).
- Please provide reproduction steps, an affected version number, and sample catalog input.
