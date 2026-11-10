# Independent review — CC-001

Date: 2026-11-10  
Reviewed repository HEAD: `65c5a3f2c022f0f3e4e889b868f9998b44793b34` (ledger lists `ee992c7` as last verified).  
Scope: `catalog_copilot/`, `tests/`, `examples/`, `AGENTS.md`, and `LEDGER.md`. Review is independent of the implementation. No product code was changed.

## Verification performed

`python3 -m unittest discover -s tests`: **22 tests passed**. I also exercised the verifier and the approval API with a temporary SQLite database. The suite covers the intended happy path and several explicit failures, but does not exercise the bypasses below. A passing suite does not establish the required safety properties.

## Findings

### Critical — Editing a consent failure fabricates consent and makes it dispatchable

`catalog_copilot/approval.py:256-312` reconstructs every lead with `consent_to_contact=True`, regardless of the original result. The drafts table does not persist consent. A generated draft for a non-consenting lead initially has `FAIL`, and `approve` correctly rejects it; however, `edit_and_approve(id, "Hello", reviewer, catalog)` returns `PASS` and `EDITED_AND_APPROVED`, and `get_approved_for_dispatch()` includes it. This is reachable through the public Python API and violates the core no-unsolicited-contact guardrail.

Persist the actual verified consent and its evidence, bind it to the contact/channel, preserve it through edits, and check it again at approval and export. Never infer consent from a reviewer edit. Add an end-to-end regression covering no consent, edit, approval, and export; also cover missing and revoked consent.

### Critical — Unrecognized or partially recognized claims pass verification

`catalog_copilot/verifier.py:32-102, 187-218, 259-345` only checks narrow patterns; unknown assertions are treated as absence of claims and yield `PASS`. With a valid target SKU, `A has a lifetime warranty.` returns `PASS` even though the catalog has no warranty data. With price 100 SAR, `A costs 100 SAR; price is 5.` returns `PASS` because plain-price patterns are skipped as soon as any currency price is found. The verifier never binds SKU or product names mentioned in text to the selected item, and has no specification verification. Currency forms, Arabic punctuation/numerals and synonyms outside the regex also escape or can be partially parsed. This affects custom generated text and human edits as well as review decisions.

Use a constrained, explicitly supported claim representation or deterministic grammar that accounts for the entire commercial content, checks every extracted claim and binds it to the correct SKU, and returns `UNCERTAIN` for anything unrecognized or ambiguous. Verify specifications and currency explicitly; cover mixed price formats, other product names/SKUs, Arabic and English variants, and unrecognized claims with adversarial tests. Do not treat the presence of a valid claim as proof that all other claims are valid.

### High — Stock negation can invert the claim

`catalog_copilot/verifier.py:119-136` searches substrings and suppresses the positive indicator whenever `out of stock` occurs. For an item with zero stock, `A is not out of stock.` returns `PASS`, despite asserting availability. Similar substring and negation ambiguity affects Arabic and other English constructions. Parse assertion polarity and scope explicitly, or mark ambiguous statements `UNCERTAIN`; test negations and multiple conflicting stock statements in both languages.

### High — Approval and persistence do not enforce the stated workflow invariants

`approval.py:100-140` accepts caller-supplied approval and verification states and uses `INSERT OR REPLACE`. A caller can insert an already approved draft without a human transition, replace an approved draft's body/report under the same ID, or reset dispatch flags. `approve` and `edit_and_approve` reject only `FAIL`, so `UNCERTAIN` drafts can enter export without resolving the ungrounded claim; transitions also accept rejected or already dispatched records. The dispatch query (`approval.py:351-362`) checks only approval state and dispatch flag, not verification or consent. `mark_dispatched` does not validate existence or state. Thus the ordinary `approve` method blocks FAIL, but failed drafts can still become dispatchable through edit or direct save.

Make creation insert-only and always pending; restrict transitions with transactional conditional updates and explicit allowed predecessor states. Bind approval to the exact verified body, subject, recipient, consent and catalog version. Require resolved, current verification and consent at export; do not allow a mere approval to convert uncertainty into verified fact. Validate dispatch transitions, including duplicate and nonexistent IDs. Test all paths and attempts to mutate or replace an approved/dispatched record.

### High — Export can lose or duplicate queue entries

`workflow.py:104-135` selects records, writes the output file, and marks each dispatched in a separate database transaction before the file is closed successfully. A write/close failure or interruption can leave records marked dispatched with an incomplete output; concurrent exporters can select and export the same rows. Reusing an output path truncates the prior queue, including when no messages remain. An export is also logged as `DISPATCHED` even though no delivery is confirmed (`approval.py:364-379`).

Implement a recoverable, idempotent export protocol with an atomic claim/reservation and durable batch manifest, safe file publication, and explicit exported versus delivered states. Handle retries and concurrent exporters, avoid silently overwriting a prior queue, and test failures at write, close, publication and database boundaries.

### High — Catalog resolution and promotion data can create unsupported claims

`generator.py:34-43` selects the first token search result, even for ties or when an explicit unknown SKU was supplied, and then verifies against that substituted SKU. Ambiguous inquiries can therefore produce a confidently verified response about the wrong product. The out-of-stock templates (`generator.py:120-152`) promise restocking soon without catalog evidence. Catalog records have no offer validity period, and `catalog.py:145-161` accepts inconsistent discount percentages and explicit discount prices; `models.py:58-64` prioritizes the latter. Consequently a generated percentage and effective price may contradict one another, and no active-offer dates are validated.

Require unambiguous product resolution, preserve and reject unknown explicit SKUs, remove unsupported restock promises, and model/validate offer activity and consistency before generating or approving claims. Add tests for ties, invalid requested SKU, expired offers, and inconsistent price/percentage.

### Medium — Ledger history is incomplete and not immutable

The audit table (`approval.py:49-61`) has no immutability enforcement. Replacing drafts records another `DRAFT_CREATED`, losing the prior state, and edit events store bodies but not complete prior verification reports or consent evidence. Dispatch logging always records previous status as `APPROVED`, including edited approvals. Review and dispatch state can change after export without preserving a complete versioned record of what was sent. These limitations prevent reliable reconstruction; cryptographic integrity may be later scope, but a truthful append-only history is needed for this slice.

Record immutable versioned snapshots and actual state transitions atomically, retain verification and consent evidence, and prevent application-level updates/deletes of audit events. Test reconstruction across edits, rejection, export and retries.

### Medium — Input validation and batch robustness have gaps

`catalog.py:117-123` rejects numeric JSON `0` because `row.get("price") or ""` treats zero as missing, while accepting non-finite `NaN`/infinity because comparisons do not reject them. `LeadInquiry` (`models.py:87-99`) converts arbitrary truthy non-string values to consent, rather than requiring a verified explicit boolean. The CSV parser does not validate required headers or contact fields; the batch commits drafts one by one, so a later failure leaves a partial batch and retry can duplicate earlier drafts. These defects weaken data quality and operational auditability.

Validate finite monetary values and required fields explicitly, accept only documented consent values with evidence, reject malformed CSV headers and invalid contacts, and define atomic or resumable idempotent batch semantics. Add zero, non-finite, malformed and interrupted-batch tests.

## Acceptance assessment

CSV/JSON ingestion, bilingual templates, SQLite review actions and approved-state CSV export exist, and the supplied tests pass. However, consent can be bypassed, ungrounded claims can pass, product ambiguity is not fail-closed, and export/audit guarantees are insufficient. These are blocking failures against CC-001 criteria and architecture guardrails. The current `REVIEW_PENDING` state should not transition to `ACCEPTED` until the above remediations and regression tests pass an independent re-review.

## Verdict: CHANGES_REQUESTED
