# CC-001 Independent Acceptance Verdict

Reviewed commit: `e13c477` (HEAD), including `git diff HEAD~1`. Verdict: **CHANGES_REQUESTED**. CC-001 remains REVIEW_PENDING.

## Findings

1. **Critical — Unsupported product specifications still pass.** The verifier relies on a finite keyword list and limited predicate patterns (`catalog_copilot/verifier.py:50`). With SKU-A in a catalog containing neither specification, independent probes `SKU-A contains asbestos.` and `SKU-A weighs 5 kg.` both returned PASS. These drafts can be approved and exported despite unverified factual claims. The exact waterproof example is fixed, but the underlying fail-closed requirement is not. Treat unrecognized factual assertions as UNCERTAIN or restrict accepted claims to deterministically verifiable forms.

2. **High — Published exports can be duplicated after interruption.** `catalog_copilot/workflow.py:220` publishes the CSV before its manifest, and the exception handler at line 239 releases the reservation even if the CSV is already published. Independently simulating an error on the manifest rename left the first CSV on disk; recovery reported no batch and retry to a different destination exported the same draft again. A process crash between the two renames similarly leaves a published CSV without a matching manifest; recovery releases its reservation, or lease expiry does so automatically (`catalog_copilot/approval.py:456`). Recovery must reconcile publication before requeueing, including every crash boundary.

3. **High — Recovery and destination protection are unsafe under concurrency.** Recovery releases all tokens absent from the scanned directory, including live exports and reservations for other directories (`catalog_copilot/approval.py:581`). Also, the destination existence check (`catalog_copilot/workflow.py:140`) is separate from `os.replace` at line 220, so another process can create the destination between them and have its file overwritten even with `overwrite=False`. `mark_exported` reads but does not check the reservation token (`catalog_copilot/approval.py:525`), so a stale worker can finalize another worker's reservation. Make recovery ownership-aware, enforce token ownership, and publish with atomic no-clobber semantics.

## Re-test of previous findings

- **1:** Exact `A is waterproof.` returned UNCERTAIN. Broader unsupported assertions still return PASS; see finding 1.
- **2:** Malformed dates are rejected, a date-only expired offer compares with UTC without an exception, and a future `offer_valid_from` fails verification. Regression tests passed.
- **3:** Empty catalog, target `MISSING`, text `Hello` returned FAIL without an exception. Regression test passed.
- **4:** Reservation and duplicate destination checks pass the normal-path regression test. Crash recovery is incomplete; see findings 2 and 3. The existing regression test does not exercise a crash.
- **5:** `models.py` no longer fabricates consent evidence, and missing evidence returns FAIL. Regression test passed. The check establishes presence of a string, not independent validation of its provenance or binding to the recipient and channel.

## Verification

- `python3 -m unittest discover -s tests -v`: **46 tests passed**.
- Independently exercised the exact waterproof text, two additional unsupported assertions, and a manifest-publication failure followed by recovery and retry. The latter produced two CSV exports of the same approved draft.

## Verdict: CHANGES_REQUESTED
