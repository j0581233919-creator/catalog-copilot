# CC-001 independent acceptance verdict

Reviewed `08d5c1651e44ecb0f88bdea814dcb672faad0550` and `git diff 021e35e HEAD`. Verdict: **CHANGES_REQUESTED**. CC-001 remains REVIEW_PENDING.

## Findings

1. **High — An attributed customer assertion bypasses grounding.** `catalog_copilot/verifier.py:300-323` only requires a question mark in the quote when the inquiry itself contains one. With inquiry `SKU-A contains asbestos.` and no asbestos information in the catalog, the draft `Regarding your question, "SKU-A contains asbestos." Standard Item A is in stock for 100 SAR.` returns PASS. The customer-supplied assertion is reproduced in an outgoing message without verification. Require an actual preserved interrogative inquiry for this exemption and fail closed on declarative or ambiguous quotations; do not infer a question solely from the merchant's attribution.

2. **High — Recovery accepts altered dispatch content.** `catalog_copilot/approval.py:103-118` compares only lead ID, contact target, body and consent evidence. It does not compare the channel, subject, SKU, language, reviewer or other exported fields. In a two-draft reserved batch, changing the first CSV row's `contact_channel` from `email` to `sms` while leaving the checked fields intact caused `recover_interrupted_exports` to report the token recovered and mark both drafts EXPORTED. A changed subject can likewise carry an unverified commercial claim; a changed channel changes the dispatch route. The same proof helper is used by expired lease reconciliation and release. Compare every dispatch-relevant field to the reserved approved records, or verify a durable digest of the complete published artifact; reject duplicate headers and malformed rows as well.

## Verification

- `python3 -m unittest discover -s tests -v`: **58 tests passed**.
- With inquiry `Is it true SKU-A contains asbestos?`, the specified attributed assertion returned **UNCERTAIN**, while the exact attributed question returned **PASS**.
- Independently created two approved reserved drafts and tested full-schema duplicate IDs, a missing required column, an empty recipient, and an empty body. Recovery preserved the reservation in all four cases. With an expired lease, invoking reservation reconciliation also left both drafts APPROVED and reserved under the original token in all four cases.
- Separately tested the declarative inquiry and altered channel cases described above. Probes used temporary databases and files. These focused checks do not establish the absence of other defects.
