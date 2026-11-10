# Independent re-review — CC-001

Date: 2026-11-10  
Reviewed HEAD: `b07b9c5`  
Scope: `git diff HEAD~1`, prior review, implementation and tests. No product code was changed.

## Verification

`python3 -m unittest discover -s tests`: **33 tests passed**. I also exercised the public verifier and ledger against an isolated catalog and temporary SQLite database. The examples below are independent adversarial checks, not assertions that the test suite covers the entire safety boundary.

## Blocking findings

### Critical — Unrecognized assertions and contradictory stock still pass

[verifier.py](../catalog_copilot/verifier.py#L49) uses a short denylist rather than accounting for all commercial content. With SKU `A`, price 100 and zero stock, `A includes a two year warranty.` returns PASS without catalog warranty data. With the same item, `A is out of stock but available.` returns PASS: the global stock indicators suppress the available assertion when any out-of-stock phrase exists ([verifier.py](../catalog_copilot/verifier.py#L124)). SKU binding recognizes only a narrow three-part pattern and does not bind product names. Other unrecognized formats, specifications and claims can likewise escape. This affects custom generator output and human edits. The added tests fix the specific previous examples, but do not establish fail-closed handling of arbitrary claims or multiple statements. Use a constrained grammar or structured claims that accounts for the entire commercial content, handles polarity per assertion, and returns UNCERTAIN for unsupported content.

### Critical — Human approval and consent evidence remain bypassable

[approval.py](../catalog_copilot/approval.py#L102) accepts a new caller-supplied APPROVED draft and PASS report. I saved such a draft with no reviewer and confirmed `get_approved_for_dispatch()` returned it. Pending and rejected records can also be replaced with `INSERT OR REPLACE`; the read/check and writes are not conditional transactional transitions. The normal edit path now preserves the stored consent and blocks false consent, but [models.py](../catalog_copilot/models.py#L96) fabricates a default evidence string, and [workflow.py](../catalog_copilot/workflow.py#L61) never reads CSV evidence. There is no consent revocation/current-consent check or catalog version binding at export. Thus neither verified opt-in nor human approval is established by the persisted flags. Make creation insert-only and pending, bind evidence to recipient and channel, require actual human transitions and current verification/consent, and enforce state transitions atomically.

### High — Export remains lossy and duplicable

[workflow.py](../catalog_copilot/workflow.py#L126) selects without reservation, publishes via `os.replace`, then marks rows exported. Concurrent exporters can publish the same rows; failure or interruption after publication and before database commit makes retry duplicate them. A subsequent nonempty export to the same path silently replaces an earlier queue. The generated manifest ID is only an audit detail, not a durable recoverable manifest; no reservation or recovery protocol exists. `mark_exported` logs success even if its conditional update changed no row, and [mark_dispatched](../catalog_copilot/approval.py#L448) accepts any predecessor or nonexistent ID and logs a fictitious transition. Implement a durable reservation/manifest and recoverable publication protocol, protect existing queues, and validate transitions and affected row counts.

### High — Promotion validity remains unverified

The generator now avoids ambiguous search substitution and unsupported restock promises, and the loader rejects large discount inconsistencies. However, [models.py](../catalog_copilot/models.py#L36) has no offer validity dates and [generator.py](../catalog_copilot/generator.py#L162) calls any positive discount active. Expired or not-yet-active offers cannot be distinguished. [catalog.py](../catalog_copilot/catalog.py#L174) also tolerates discrepancies up to 0.50 and skips consistency checking when percent is zero, so conflicting price and percent may still be accepted. Validate offer activity and exact currency-appropriate consistency before claiming a promotion.

### Medium — Audit history and upgrades remain incomplete

[approval.py](../catalog_copilot/approval.py#L60) has no append-only enforcement or complete versioned snapshots. Export always logs previous status APPROVED even for edited approvals, and records events for unsuccessful updates. Edits do not retain complete prior verification and consent evidence. Moreover, `CREATE TABLE IF NOT EXISTS` does not migrate databases created by the prior schema, so existing ledgers lack the new consent columns and cannot safely continue normal operations. Provide a tested migration and truthful immutable snapshots and transitions.

### Medium — Input and batch robustness remain partial

Zero and non-finite price handling, CSV headers, and consent type parsing improved. However, consent evidence is fabricated as described above, contact validation only checks nonempty strings, and [workflow.py](../catalog_copilot/workflow.py#L112) persists one draft per transaction with random IDs. A later failure leaves a partial batch, and a retry creates duplicates. Provide atomic or resumable idempotent batch semantics and validate documented evidence and contact data.

## Assessment of prior findings

All eight prior findings were revisited. The original direct edit of a nonconsenting draft is blocked, the specific mixed-price and `not out of stock` examples are covered, unknown explicit SKUs are preserved, and export now distinguishes exported from delivered. These are useful improvements, but each original finding remains incompletely remediated for the reasons above. The passing suite does not justify transitioning CC-001 from REVIEW_PENDING to ACCEPTED.

## Verdict: CHANGES_REQUESTED
