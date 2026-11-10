# AGENTS.md — CatalogCopilot

## Authority & Mission
CatalogCopilot is a catalog-grounded sales assistant for Arabic and English customer inquiries. It grounds draft responses strictly in verified merchant catalogs (CSV/JSON), deterministically verifies all commercial claims (SKU, price, stock, active offers, currency), enforces explicit customer consent to prevent unsolicited messaging, and provides an auditable human review interface before any message can be dispatched.

## Architecture Principles & Guardrails
1. **Catalog Grounding**: Every factual claim about product availability, price, discount, or specifications must be verified against an ingested catalog.
2. **Deterministic Claim Verification**: Never rely on an LLM or probabilistic model to verify commercial claims. The verification engine deterministically parses prices, discounts, stock statements, and SKU identifiers, verifying them strictly against catalog ground truth.
3. **Fail-Closed on Uncertainty**: If a claim cannot be verified, or if the product details are ambiguous, the verification engine marks the draft as `UNCERTAIN` or `FAIL`, forcing human intervention.
4. **Never Send Unsolicited Messages**: Leads must possess explicit verified opt-in consent (`consent_to_contact == True`). Any inquiry without consent is blocked at verification time.
5. **Human-in-the-Loop Review**: No message may be dispatched without human approval (`APPROVED` or `EDITED_AND_APPROVED`). The human reviewer has full authority to inspect verification reports, edit draft bodies, or reject proposals.
6. **Offline-First CSV Architecture**: Complete batch CSV processing and SQLite audit persistence first. External webhook/CRM integrations (e.g., Chatwoot) are only layered on after standalone acceptance.
7. **Bilingual Precision**: First-class support for both Modern Standard Arabic (العربية) and English, including Arabic numeral handling, dialect normalization, and polite commercial tone.
8. **Independent Review Requirement**: Flash/Executor agents must NOT certify their own implementation as independently reviewed. Codex or a designated independent reviewer conducts review and certifies transitions from `REVIEW_PENDING` to `ACCEPTED`.

## Lifecycle States
`READY` -> `IN_PROGRESS` -> `IMPLEMENTED` -> `TESTED` -> `REVIEW_PENDING` -> `ACCEPTED` -> `RELEASE_READY`

## Task Ledger Summary
- **Current Task**: CC-004 — Advanced Arabic Dialect & Fuzzy SKU Resolution.
- **Current Owner**: Gemini Flash (Lead Engineering Executor).
- **Branch**: main
- **Last Verified Commit**: 99f3f58
- **Independent Review Status**: ACCEPTED (Milestones CC-001, CC-002, and CC-003 Certified ACCEPTED; 94/94 tests passing).
- **Completed Tasks**:
  - CC-001: First End-to-End Vertical Slice (ACCEPTED)
  - CC-002: Interactive Human Review Terminal UI (ACCEPTED)
  - CC-003: Focused Chatwoot Webhook Integration (ACCEPTED)

## Verification Criteria for CC-001
1. **Catalog Parsing**: Validates valid CSV/JSON catalogs; rejects duplicate SKUs, negative prices, and invalid stock values.
2. **Claim Verification**:
   - Matches valid catalog price and stock -> PASS.
   - Detects halluncinated/incorrect price -> FAIL.
   - Detects out-of-stock item claimed as available -> FAIL.
   - Detects unauthorized discount -> FAIL.
   - Detects lead without consent -> FAIL.
   - Flags ungrounded claims -> UNCERTAIN (Fail-Closed).
3. **Draft Generation**: Produces coherent, grounded drafts in Arabic and English.
4. **Human Review & Audit**: Persists drafts to SQLite with `PENDING_REVIEW` state; transitions accurately to `APPROVED`, `REJECTED`, or `EDITED_AND_APPROVED`.
5. **Dispatch Export**: Exports only approved messages to dispatch queue CSV; excludes rejected and unreviewed items.
6. **Behavioral Test Suite**: 100% passing tests via `python3 -m unittest discover -s tests`.
