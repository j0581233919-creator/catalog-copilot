# CC-001 independent acceptance verdict

**CHANGES_REQUESTED** — Reviewed commit `6bddb756bd6d156bfd598067d3a0567c78dd1944` against `aea20e7`. CC-001 remains REVIEW_PENDING.

## Finding: secondary predicates still bypass grounding (high)

The new validator does not establish that the claim phrase contains no secondary verbs or predicates. In `catalog_copilot/verifier.py:261`, English checks a finite list of verbs, then accepts any remaining phrase of up to four words. Arabic checks only the enumerated predicates and auxiliaries at lines 313–319. Unlisted predicates therefore pass as if they were noun phrases, and the attributed inquiry is removed before grounding checks.

Reproduction: use a consented lead with the inquiry below, an active in-stock SKU-A priced at 100 SAR, and no catalog evidence about asbestos, cancer, or toxicity. Set the draft to `Regarding your question, "<inquiry>", Standard Item A is in stock for 100 SAR.` The full verifier returns **PASS** for each of:

- `Is it true SKU-A contains asbestos linked to cancer?` — the reduced relative predicate `linked to cancer` is unchecked.
- `هل صحيح أن SKU-A يحتوي اسبستوس يسبب السرطان؟` — the secondary predicate `يسبب السرطان` is unchecked.
- `هل صحيح أن المنتج سام خطير؟` — the additional predicate `خطير` is unchecked.

These violate the requested zero-secondary-predicate constraint and allow ungrounded statements to be repeated in a draft that passes verification. A finite blacklist plus a length limit cannot establish the required structure. Use a conservative, explicitly bounded grammar for claim phrases, or otherwise leave phrases whose structure cannot be established in the draft for grounding; add full-verifier regression tests for both languages.

## Assessment and verification

- The listed Review 10 connective examples are addressed: the six requested adversarial probes returned **UNCERTAIN** in the full-verifier regression test. The additional `although`, `and`, Arabic temporal, and multiple-question cases in that test also returned UNCERTAIN. The broader finding remains unresolved because other secondary predicates still bypass the exemption.
- All nine requested legitimate inquiries returned **PASS** in the same full-verifier test, using attributed inquiries and a grounded stock and price statement.
- The implementation checks selected punctuation and connectives, an initial subject anchor and linking predicate, and a four-word maximum. Those checks do not prove the absence of other delimiters, connectives, secondary verbs, or predicates; the reproduced failures demonstrate the missing structural guarantee.
- `python3 -m unittest discover -s tests -v`: **61 tests passed**. Passing the suite does not cover the counterexamples above.
