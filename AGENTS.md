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
8. **Independent Review Requirement & Codex Reservation Directive**:
   - OpenAI Codex CLI is **strictly reserved** for the CoreNode ecosystem (CoreNode Unified Sovereign, NUSOOR, NEXUS, ATHEER).
   - In CatalogCopilot and all non-CoreNode workflows: Never invoke Codex directly or indirectly, never perform Codex quota/availability probes or fallback loops.
   - Independent engineering review and certification are conducted via verified Google Elephant-Goldfish Model (EGM) Goldfish reviewers (`egm-codebase-details` and `egm-structural`) using genuine fresh-context subagents.
   - Flash/Executor agents must NOT certify their own implementation as independently reviewed.

## Lifecycle States
`READY` -> `IN_PROGRESS` -> `IMPLEMENTED` -> `TESTED` -> `REVIEW_PENDING` -> `ACCEPTED` -> `RELEASE_READY`

## Task Ledger Summary
- **Current Task**: CC-004 — Advanced Arabic Dialect Normalization & Fuzzy SKU Resolution.
- **Current Lifecycle State**: `IN_PROGRESS`
- **Current Owner**: Gemini Flash (Lead Engineering Executor).
- **Independent Reviewer**: EGM Goldfish Codebase Details Auditor (`egm-codebase-details`).
- **Branch**: main
- **Last Verified Commit**: 58416d1
- **Independent Review Status**: Milestones CC-001, CC-002, and CC-003 Certified ACCEPTED; 94/94 tests passing.
- **Completed Tasks**:
  - CC-001: First End-to-End Vertical Slice (ACCEPTED)
  - CC-002: Interactive Human Review Terminal UI (ACCEPTED)
  - CC-003: Focused Chatwoot Webhook Integration (ACCEPTED)

## Verification Criteria for CC-004
1. **Dialect Normalization Engine (`catalog_copilot/dialect.py`)**:
   - Supports Egyptian (لهجة مصرية), Gulf/Saudi (لهجة خليجية/سعودية), Levantine (لهجة شامية), and MSA.
   - Handles dialectal verbs (عايز/عاوز/بدي/ابغى/ابي/أود), question words (بكام/كام/قديش/بكم/شو/ايش/وش), demonstratives (ده/دي/هذا/هذي/هيدا/هادا), and availability phrases (متوفر/موجود/فيه/في/هلأ/الحين).
   - Normalizes elongated letters (e.g. "عاااايز", "بكممم"), tatweel (`ـ`), diacritics/tashkeel, and orthographic variations (Alef, Teh Marbuta, Yeh, Hamza forms).
   - Extracts intent classifications: `PRICE_INQUIRY`, `STOCK_INQUIRY`, `OFFER_INQUIRY`, `SPEC_INQUIRY`, `PURCHASE_INTENT`, `GENERAL_INQUIRY`.
   - Isolates cleansed product search tokens stripped of conversational dialect fillers and greetings.
2. **Fuzzy SKU & Product Matcher (`catalog_copilot/fuzzy.py`)**:
   - Implements pure standard-library string distance metrics (normalized Levenshtein / Damerau edit distance, token-set similarity, prefix/substring matching).
   - Supports common Arabic-English transliteration and typo patterns (e.g., "ايفون" vs "iPhone", "سامسونج" vs "Samsung", "بلوتوس" vs "بلوتوث").
   - Supports partial and hyphen-insensitive SKU resolution (e.g., "PRO100", "PRO-100", "100" -> "PRD-PRO-100").
   - Returns ranked candidates with deterministic confidence scores `[0.0, 1.0]` and confidence margin calculations.
3. **Deterministic Guardrails & Integration (`catalog_copilot/generator.py` & `verifier.py`)**:
   - Single high-confidence match (confidence >= threshold, e.g. 0.75, and margin >= 0.15) resolves the catalog item.
   - Ambiguous or low-confidence match fails closed: generator outputs polite clarification asking the customer to clarify between matched candidates or supply the exact SKU; verifier validates that no ungrounded claims are made.
   - Re-verification in TUI and Chatwoot operates seamlessly with dialectal inquiries.
4. **Zero External Dependencies**: Pure Python standard library implementation, no network downloads, no new packages.
5. **Adversarial & Regression Testing**: 100% test pass rate across new and legacy tests (`python3 -m unittest discover -s tests -v`).
6. **EGM Independent Verification**: Fresh-context independent review by `egm-codebase-details` confirming acceptance criteria without invoking Codex.
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
