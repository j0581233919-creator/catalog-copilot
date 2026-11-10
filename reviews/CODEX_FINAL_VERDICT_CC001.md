# CC-001 Independent Final Verdict

Reviewed commit: `452f3c5c7f89ce4e663898ffecda1295d2c0e011` (HEAD), including `git diff HEAD~1`. Verdict: **CHANGES_REQUESTED**. CC-001 remains REVIEW_PENDING.

## Findings

1. **Critical — Customer inquiry text can authorize an unsupported product assertion.** `catalog_copilot/verifier.py:316` adds every word in the customer's inquiry to the allowed vocabulary, and lines 343–356 only flag words outside that vocabulary. With the test catalog containing no asbestos information, inquiry `Is it true SKU-A contains asbestos?` and draft `SKU-A contains asbestos.` independently returned **PASS**. The customer asking a question is not catalog evidence. Word-level membership also cannot establish that a factual relationship is grounded. Verify assertions against catalog facts and fail closed when they cannot be verified.

2. **High — A no-clobber collision falsely finalizes an unpublished draft.** `catalog_copilot/workflow.py:230` correctly uses an atomic hard link to protect the CSV. However, the exception handler at lines 257–267 treats any nonempty destination as proof that this worker published it. Independently creating a foreign file immediately before the link caused `FileExistsError`, preserved that foreign file, and changed the reserved draft to **EXPORTED** even though the file contained only `foreign data`. The approved message is lost from the export queue and a manifest may be attached to the wrong file. The same assumption appears in lease reconciliation, release, and recovery (`catalog_copilot/approval.py:482`, `:562`, `:685`), including when `overwrite=True` starts with an existing destination. Persist and verify publication identity tied to the reservation before finalizing.

3. **High — Export ownership is not strictly enforced.** `catalog_copilot/approval.py:609` accepts a null reservation token, so an unreserved approved draft can be finalized by any token. Independently calling `mark_exported(['D'], 'stale')` on an unreserved approved draft returned 1 and marked it EXPORTED. Additionally, the token is checked in a separate SELECT from the UPDATE at line 620, whose predicate contains only the draft ID; a reservation change between those operations is not guarded by the update. Require the matching token in the atomic update predicate and reject unreserved drafts.

## Acceptance blocker re-tests

- **Finding 1:** With the original test inquiry and catalog, both `SKU-A contains asbestos.` and `SKU-A weighs 5 kg.` returned UNCERTAIN in the regression test. The independent inquiry-based counterexample above still returns PASS.
- **Finding 2:** The added test simulates publication before ledger finalization, then recovers and retries to a different destination; it passes without duplication. This does not establish safety for all interruption and collision boundaries; see finding 2 above.
- **Finding 3:** The hard-link publication protects an existing CSV from clobbering in the independently simulated race, but incorrectly finalizes the draft. Recovery of directory A leaves a live reservation in directory B intact in the regression test. A mismatched active token is rejected by the code, but unreserved drafts and the non-atomic check remain vulnerable; see finding 3.

## Verification

- `python3 -m unittest discover -s tests -v`: **49 tests passed**.
- Independently probed inquiry-derived claim grounding, unreserved token ownership, and a destination created between the initial existence check and atomic publication. All probes used temporary databases and files.

## Verdict: CHANGES_REQUESTED
