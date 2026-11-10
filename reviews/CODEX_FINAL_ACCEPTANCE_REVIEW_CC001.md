# CC-001 Independent Final Acceptance Review

Reviewed commit: `684a584` (HEAD), including `git diff HEAD~1`. Verdict: **CHANGES_REQUESTED**. CC-001 should remain REVIEW_PENDING.

## Findings

1. **Critical — Unsupported product claims can pass verification.** `catalog_copilot/verifier.py:50` uses a finite keyword list and treats unrecognized text as verified. With catalog SKU A and no waterproof specification, `A is waterproof.` returns PASS. Such an edited draft can be approved and exported, violating catalog grounding and fail-closed requirements. Restrict approved claims to deterministically verifiable representations or return UNCERTAIN for unrecognized factual assertions.

2. **High — Offer validity fails open or crashes.** `catalog_copilot/verifier.py:377` silently ignores invalid date strings. An item with `offer_valid_until="invalid"`, price 100 SAR and 20% discount returns PASS for `A costs 80 SAR with 20% discount`. A valid date-only value `2020-01-01` raises `TypeError` when compared to an aware timestamp. Validate and normalize dates on ingestion and fail closed on invalid or unknown offer validity; model the start of an offer as well as its end.

3. **High — Unknown SKU crashes the verifier.** For an empty catalog, target SKU `MISSING`, and text `Hello`, `verify` raises `UnboundLocalError` because `is_contra` is uninitialized at `catalog_copilot/verifier.py:422`. Unknown SKUs should return FAIL, and batch processing should not crash on this input.

4. **High — Manifest does not make export recoverable.** `catalog_copilot/workflow.py:128` selects drafts without reservation; lines 189–200 write the manifest, publish the CSV, then update SQLite in separate operations. Concurrent exporters or interruption after publication can duplicate messages on retry; a later export to the same path overwrites the prior CSV and manifest. A failed rename can also leave a manifest describing an unpublished batch. Use a durable reservation and recovery protocol, atomic state transitions, and protect existing queues. The normal-path manifest existence check below does not establish crash durability.

5. **High — Consent evidence is fabricated.** `catalog_copilot/models.py:140` creates a purported documentation string when no evidence was supplied; `verifier.py:187` labels it verified. A true boolean alone is thereby presented as documented evidence. Require actual consent evidence and preserve its provenance and recipient/channel binding, rather than synthesizing proof.

## Verification performed

- `python3 -m unittest discover -s tests`: **38 tests passed**.
- Direct `save_draft` with APPROVED: stored as PENDING_REVIEW; dispatchable queue contained zero records.
- With catalog SKU A, `A includes a two year warranty.` returned UNCERTAIN; `A is out of stock but available.` returned UNCERTAIN; `A costs 5 euros.` returned FAIL.
- Direct SQLite UPDATE and DELETE of existing audit_log rows were both rejected by triggers.
- A normal approved export wrote one CSV record and an adjacent `.manifest.json` containing the export filename, count, draft ID, batch ID, and timestamp. This confirms normal-path creation only; see finding 4.
- Independent additional probes reproduced the unsupported specification PASS, invalid offer date PASS, date-only exception, and unknown SKU exception described above.

## Verdict: CHANGES_REQUESTED
