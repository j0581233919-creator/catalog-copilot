# Independent acceptance verdict: CHANGES_REQUESTED

Reviewed commit `4535294` against `a392b09`, focusing on `catalog_copilot/verifier.py`. The working tree was clean at the start of review. CC-001 is not accepted.

## Finding: Arabic-block characters can conceal a secondary assertion

**High — `catalog_copilot/verifier.py:368–385`.** The new expression `^[\u0600-\u06FF\s0-9]+$` excludes the reported U+2010 and U+2212 characters, but the Arabic Unicode block is not limited to letters and digits: it also includes punctuation and U+0640 ARABIC TATWEEL. Moreover, `\s` includes Unicode whitespace. The comment that all punctuation is rejected is therefore inaccurate. The subsequent whitespace-based word count and boundary-based banned-word check can treat a compound containing these characters as one acceptable word.

In the full verifier, the following inquiry returns **PASS**, although it embeds the unsupported secondary assertion that asbestos causes cancer:

- `هل صحيح أن SKU-A يحتوي اسبستوسـيسببـالسرطان؟` (U+0640 joins the words)

The punctuation examples `هل صحيح أن SKU-A يحتوي اسبستوس۔مادة؟` (U+06D4) and `هل صحيح أن SKU-A يحتوي اسبستوس؍مادة؟` (U+060D) also return **PASS**, contrary to the stated punctuation restriction. These are distinct from the fixed Unicode dash examples. A passing quoted inquiry can be included in an otherwise verified draft and approved for dispatch, despite the ungrounded embedded assertion.

Reproduction: use the existing `TestCodexAdversarialFindings` fixture: a consented lead whose inquiry is the probe, active in-stock `SKU-A` priced at 100 SAR, and draft `Regarding your question, "<probe>", Standard Item A is in stock for 100 SAR.` Call `verify` with target SKU `SKU-A`. Constrain or normalize the permitted characters and token boundaries so that punctuation and joining characters cannot conceal secondary predicates, and add full-verifier regressions. Preserve the separately parsed SKU subject and legitimate single-claim inquiries.

## Review 13 and requested verification

The specific Review 13 finding is resolved: the U+2010 and U+2212 examples return **UNCERTAIN**, while the ASCII hyphen in `SKU-A` remains accepted in the requested legitimate inquiries. The whitelist does reject characters outside its explicit range, but the range itself does not implement the described letters-and-digits-only policy.

The full-verifier regression fixture exercised all seven requested adversarial inquiries; each returned **UNCERTAIN**. It also exercised all ten requested legitimate inquiries; each returned **PASS**, including the `2-year warranty` and Arabic `SKU-A` inquiries. These results are assertions in `test_codex_review10_truth_evaluating_questions_must_not_conceal_ungrounded_assertions`.

`python3 -m unittest discover -s tests -v` completed successfully: **61 tests passed**. The three additional probes above were run separately through the full verifier and returned **PASS**.
