# CC-001 independent acceptance verdict

**Verdict: CHANGES_REQUESTED.** Reviewed commit `b483b56` and `git diff 08d5c16 HEAD`. CC-001 remains REVIEW_PENDING.

## Findings

1. **High — A question mark exempts an entire mixed assertion from grounding.** [verifier.py:286-316](../catalog_copilot/verifier.py) treats any inquiry and matching quote containing `?` or `؟` as an interrogative, then strips the whole quote. With no asbestos information in the catalog, inquiry `SKU-A contains asbestos. Is it available?` and draft `Regarding your question, "SKU-A contains asbestos. Is it available?", Standard Item A is in stock for 100 SAR.` return PASS. The quoted declarative assertion is sent without grounding. Separate assertions from interrogative clauses and fail closed on mixed or ambiguous quotations; a question mark alone does not establish that the entire quotation is a question.

2. **Medium — Recovery accepts malformed rows and altered approval metadata.** [approval.py:70-145](../catalog_copilot/approval.py) accepts a row with extra CSV fields because `DictReader` stores them under `None` and the code ignores them. It also never compares `approval_status` or `reviewed_at` to the reserved record. In independent one-draft probes, appending an extra field to an otherwise valid row, or changing `approval_status` from `APPROVED` to `REJECTED`, caused recovery to report the token recovered and mark the draft EXPORTED. The resulting dispatch artifact contains malformed or false audit information. Reject rows with missing or extra fields and compare all exported metadata to the approved record (or validate a durable digest of the exact artifact).

## Verification

- `python3 -m unittest discover -s tests -v`: **58 tests passed**.
- Independent temporary-catalog probes: the specified declarative quotation returned **UNCERTAIN**; the preserved `Is it true SKU-A contains asbestos?` quotation returned **PASS**. The mixed assertion/question above returned **PASS**.
- Independent temporary SQLite and CSV probes: changing `contact_channel` to `sms`, changing the subject, or adding a duplicate header each prevented recovery. After forcing the reservation lease to expire, reconciliation left the draft APPROVED and reserved, with no new reservation. Duplicate-header probe used the complete schema.
- Recovery accepted both an extra row field and a changed approval status as described above. These focused probes do not establish the absence of other defects.
