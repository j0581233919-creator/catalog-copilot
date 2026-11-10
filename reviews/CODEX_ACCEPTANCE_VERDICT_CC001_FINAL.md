# CC-001 Independent Acceptance Verdict

Reviewed commit: `021e35e` (HEAD), including `git diff fbee09b HEAD`. Verdict: **CHANGES_REQUESTED**. CC-001 should remain REVIEW_PENDING.

## Findings

1. **High — Quotation attribution still permits unsupported assertions.** In `catalog_copilot/verifier.py:281-300`, punctuation is removed before matching a quoted substring against the inquiry, and any attribution word anywhere in the draft causes that substring to be removed from grounding checks. With inquiry `Is it true SKU-A contains asbestos?` and a catalog with no asbestos information, `Regarding your question, "SKU-A contains asbestos". Standard Item A is in stock for 100 SAR.` returns **PASS**. The quoted assertion is not the customer's question; the verifier stripped it nonetheless. A reviewer can consequently approve an unsupported product claim. Require exact, properly attributed inquiry quotations with their interrogative meaning preserved, or verify all quoted content as merchant claims; fail closed when attribution or meaning is ambiguous.

2. **High — Recovery finalizes incomplete or duplicated dispatch records.** `catalog_copilot/approval.py:40-63` checks only the batch token and the set of draft IDs; it neither requires the dispatch columns and complete row values nor rejects duplicate IDs. I reserved drafts A and B, wrote a CSV containing only `batch_manifest_id,draft_id` and the two matching IDs, and ran recovery. It reported the token recovered and changed both drafts to EXPORTED despite the CSV containing no recipient or message. A CSV with A twice and B once also passes the proof helper, creating potential duplicate dispatch. This affects recovery, release and expired-lease reconciliation using the same helper. Validate exact record count and unique IDs, the full required schema and the actual reserved draft contents (or a durable digest of the staged artifact) before finalization; preserve ambiguous reservations.

## Verification and requested probes

- `python3 -m unittest discover -s tests -v`: **56 tests passed**.
- The original standalone `"SKU-A contains asbestos."` returned **UNCERTAIN** in an independent probe. An attributed assertion derived from a customer question still returned **PASS** as described in finding 1.
- Injected a stale manifest after the initial check but before the manifest hard link. Export succeeded, the draft became EXPORTED, and a second export returned zero and created no second CSV. This exercises the collision race, not only a preexisting manifest.
- In the same independent export, an edited and approved draft's EXPORTED audit entry recorded `EDITED_AND_APPROVED` as `previous_status`.
- The regression suite verifies that recovery preserves a reservation when an expected draft ID is missing. The implementation compares the full ID set in both recovery and lease reconciliation. However, independently tested incomplete records with all expected IDs still falsely finalize, as described in finding 2.
- Probes used temporary databases and files. Passing these focused checks does not establish the absence of other defects.

## Verdict: CHANGES_REQUESTED
