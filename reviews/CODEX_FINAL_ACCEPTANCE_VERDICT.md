# Independent acceptance verdict: CHANGES_REQUESTED

Reviewed commit `a392b09` against `2c38ada`. The working tree was clean at the start of the review. CC-001 is not accepted.

## Finding: Arabic claim validation permits other Unicode hyphens and dashes

**Medium — `catalog_copilot/verifier.py:368–378`.** The new Arabic check rejects ASCII hyphen-minus, underscore, em dash and en dash, but does not reject other dash characters. The subsequent whitespace split treats a compound containing one of these characters as one word. In the full verifier, both of the following returned **PASS**, contrary to the requirement that Arabic claim phrases disallow hyphens and dashes:

- `هل صحيح أن SKU-A يحتوي اسبستوس‐مادة؟` (U+2010 HYPHEN)
- `هل صحيح أن SKU-A يحتوي اسبستوس−مادة؟` (U+2212 MINUS SIGN)

Reproduction: use a consented lead whose inquiry is the probe, an active in-stock `SKU-A` priced at 100 SAR, and verify `Regarding your question, "<probe>", Standard Item A is in stock for 100 SAR.` with target SKU `SKU-A`. The accepted character can join words into an unchecked compound, undermining the intended fail-closed boundary. Reject Unicode dash/hyphen variants in Arabic claim text or constrain the permitted characters, and add full-verifier regressions. Preserve the ASCII hyphen in the SKU subject.

## Review 12 and English validation

The three reported hyphenated secondary predicates now return **UNCERTAIN** through the full verifier: `linked-to-cancer`, `known-to-cause-cancer`, and `proven-carcinogenic`. The English claim phrase is checked against the requested character pattern; each ASCII-hyphenated token must match the numeric measurement pattern; and alphabetic subwords are checked against the preposition and extra-verb lists and the `-ed`/`-ing` suffix rule, with existing short-word exceptions. These rules address the reported examples. They are heuristic filters, not proof that every possible secondary predicate is recognized.

## Verification performed

All probes used the full `verify` method with the consented catalog fixture and quoted-question draft described above.

- All seven requested adversarial inquiries returned **UNCERTAIN**, including the three hyphenated English examples, the two space-separated English examples, and both Arabic examples.
- All ten requested legitimate inquiries returned **PASS**, including the `2-year warranty` question. This warranty case was checked separately; it is not in the existing pure-inquiry regression list.
- `python3 -m unittest discover -s tests -v`: **61 tests passed**.
- The two additional Unicode Arabic punctuation probes above returned **PASS**, establishing the remaining finding despite the passing suite.
