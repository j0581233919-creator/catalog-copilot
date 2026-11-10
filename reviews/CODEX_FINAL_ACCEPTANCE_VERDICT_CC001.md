# CC-001 Independent Acceptance Verdict

Reviewed commit: `fbee09b` (HEAD), including `git diff 452f3c5 HEAD`. Verdict: **CHANGES_REQUESTED**. CC-001 remains REVIEW_PENDING.

## Findings

1. **Critical — Quotation marks bypass grounding of merchant assertions.** `catalog_copilot/verifier.py:276` removes arbitrary quoted text before the ungrounded-pattern, predicate and vocabulary checks. With the test catalog containing no asbestos information and inquiry `Is it true SKU-A contains asbestos?`, the draft `"SKU-A contains asbestos."` returns **PASS**, despite asserting an unsupported specification. The quoted text need not match the inquiry, be a question, or have any attribution. This permits approval of an unverified commercial claim. Validate quoted content and its context against catalog facts; fail closed on unsupported assertions.

2. **High — A stale manifest causes a published CSV to be released and exported again.** `catalog_copilot/workflow.py:235` publishes the CSV before linking the manifest. If the destination CSV does not exist but its companion manifest exists with a different token, the manifest link raises `FileExistsError`. The new identity check at `catalog_copilot/approval.py:32` rejects the actually published CSV because of the old manifest, and the exception path releases its reservation (`workflow.py:262-285`). Independently, this left the first CSV on disk and the draft APPROVED and available in the queue; exporting to another path succeeded, leaving two CSVs containing the same message. The same conflict can arise after CSV replacement with `overwrite=True` if manifest replacement fails or the process crashes. Treat successful CSV publication as durable even with a conflicting manifest, and design reconciliation to prevent duplicate dispatch while resolving the manifest conflict.

3. **Medium — Export audit records the wrong previous approval state.** `catalog_copilot/approval.py:658` always records `APPROVED`, including when the atomic update transitions an `EDITED_AND_APPROVED` draft. The resulting immutable audit trail misrepresents the actual review transition. Capture the true prior state atomically and log it accurately.

4. **Medium — Recovery accepts a token-only CSV as proof of all reserved drafts.** `catalog_copilot/approval.py:45-56` verifies only that at least one row carries the token; it does not verify draft IDs, count, or content against the reserved batch. A truncated or unrelated CSV with that token can cause release/recovery or expired-lease reconciliation to finalize every draft under the reservation as EXPORTED, even when absent from the file. Verify the complete expected batch identity and content before finalizing, while preserving ambiguous reservations for intervention.

## Requested probes and verification

- `python3 -m unittest discover -s tests -v`: **52 tests passed**.
- The direct unquoted asbestos assertion returned **UNCERTAIN**. The fully quoted assertion returned **PASS** (finding 1).
- Independently injected a foreign CSV between the initial existence check and `os.link`: `FileExistsError` was raised, the foreign file remained unchanged, and the draft stayed APPROVED and available in the queue. The added regression test only creates the file before the initial check; the injected probe exercises the race itself. The separate manifest collision still permits duplicates (finding 2).
- Independently called `mark_exported(['D'], 'any_token')` both without a reservation and with a different reservation token: both returned 0. The atomic UPDATE now includes the reservation token predicate.
- Probes used temporary files and databases. Findings 3 and 4 are based on direct inspection of the transition and recovery predicates.

## Verdict: CHANGES_REQUESTED
