# Independent acceptance verdict: CHANGES_REQUESTED

Reviewed commit `2c38ada` against `6bddb75`, focusing on `catalog_copilot/verifier.py` and the requested inquiry handling. The working tree was clean at the start of review. This verdict does not certify the CC-001 lifecycle transition to ACCEPTED.

## High: Hyphenated secondary predicates bypass the inquiry grammar

The English complement validator permits internal hyphens (verifier.py:244), splits the claim phrase only on whitespace (line 282), and applies its preposition, verb and suffix checks to whole whitespace tokens (lines 303–308). Consequently `linked-to-cancer`, `known-to-cause-cancer`, and `proven-carcinogenic` each evade the checks and count as one word. The full verifier returns **PASS** for all of these inquiries when they are quoted in a draft:

- `Is it true SKU-A contains asbestos linked-to-cancer?`
- `Is it true SKU-A contains asbestos known-to-cause-cancer?`
- `Is it true SKU-A contains asbestos proven-carcinogenic?`

Reproduction: use a consented lead whose inquiry is the probe, a catalog containing active in-stock `SKU-A` at 100 SAR, and verify the draft `Regarding your question, "<probe>", Standard Item A is in stock for 100 SAR.` with target SKU `SKU-A`. The embedded reduced relative asserts an unverified health claim as background to the question; a PASS can permit human approval and dispatch despite the fail-closed requirement. The four exact Review 11 examples are addressed, but the general finding remains open.

Token boundaries must prevent internal punctuation from hiding secondary verbs and prepositions. Reject ambiguous constructions or validate their components while preserving legitimate SKU handling; add full-verifier regression cases for these examples.

## Grammar assessment

The implementation enforces the requested word-count bounds for the tested English and Arabic patterns, and catches the listed space-separated banned words and suffixes. It does not establish that accepted words are actually adjectives or nouns: the checks are token counts and finite deny lists. Internal hyphens further defeat those deny lists as demonstrated above. Arabic adjective predicates reject following words, and the listed secondary verbs and predicates are rejected in verb complements, but the noun requirement likewise is not independently established. These limitations mean the claimed general elimination of secondary predicates is not demonstrated.

## Verification performed

- Full verifier, quoted-question draft and consented lead: all four specified adversarial probes returned `UNCERTAIN`.
- The nine specified legitimate inquiries returned `PASS` under the same full-verifier regression fixture.
- `python3 -m unittest discover -s tests -v`: **61 tests passed**. The existing `test_codex_review10_truth_evaluating_questions_must_not_conceal_ungrounded_assertions` exercises all 13 specified probes through `verify`.
- The three additional hyphenated probes above were run separately through `verify` and each returned `PASS`.
