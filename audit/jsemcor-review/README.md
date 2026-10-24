# Review of the JSemCor candidate senses (issue #11)

`jsemcor_suggestions.py` found 2,795 (synset, lemma) senses that JSemCor
translators used but wnja 2.0 lacks (from JSemCor 1.1: the English
alignment and the traduku translation database).  After the junk filter
(gloss notes and punctuated fragments) 2,785 remain in
`corrections/jsemcor-suggestions.tsv`; the review covered all 2,795.

## Method

As for issue #8 (`../issue8-opus-review.tsv`):

1. `uv run python -m audit.jsemcor_review batches` shuffled the
   candidates under random ids (`key.tsv`) into 19 batches of 150
   (`batchNN.tsv`): synset, word and its POS, the other Japanese words in
   the synset, English lemmas and definition, Japanese definition and an
   example sentence pair.  Counts and sources were left out.
2. Two independent blind runs by Claude Opus (`batchNN-review-run1.tsv`,
   `batchNN-review-run2.tsv`), each with the same instructions: is the
   word a normal Japanese word whose general meaning matches the synset?
   Verdicts: ADD, POS (right meaning, wrong part of speech or form; the
   corrected form starts the reason), MWE (only part of a longer
   expression), WRONG (loose or contextual translation, hypernym or
   hyponym, copula, not a word), UNSURE.
3. The 494 disagreements were adjudicated with both verdicts and reasons
   shown (`adjudicate{1,2}.tsv` -> `batchA{1,2}-review-adjudicated.tsv`).
4. `uv run python -m audit.jsemcor_review combine` wrote `verdicts.tsv`.

## Results

The runs agreed on 2,301 of 2,795 (82%; issue #8: 74%).

| Final | Senses | Both runs | Adjudicated |
|---|---:|---:|---:|
| ADD | 1,284 | 1,172 | 112 |
| WRONG | 1,252 | 956 | 296 |
| POS | 242 | 168 | 74 |
| MWE | 17 | 5 | 12 |

No reviewer used UNSURE.

## Caveats

- The two adjudicators were not equally strict: of the ADD/WRONG splits
  they chose ADD for 35% (68/196) and 19% (37/194).  The 112 adjudicated
  ADDs are less certain than the 1,172 where both runs agreed.
- The 10 junk candidates later removed by the filter are WRONG in
  `verdicts.tsv`.
- Some candidates are inflected forms (質素な, 残って); reviewers marked
  them POS with the dictionary form.
