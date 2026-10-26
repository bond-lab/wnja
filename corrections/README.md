# wnja Corrections

Manual corrections to the generated `build/wnja-2.0.xml`, applied by `tweak_wnja.py`
as the final build step.

Corrections are preferred over editing the XML directly because the XML is a
build artefact — it is regenerated from NTU-MC source data by `build_wnja.py`.
Storing corrections here keeps fixes auditable, reviewable as diffs, and
resilient to upstream rebuilds.

## Files

| File | Corrects |
|------|---------|
| `definitions.tsv` | Japanese definition text |
| `senses.tsv` | Wrong senses: remove, or lower confidence; malformed lemmas: rename |
| `agentive-removed.tsv` | Agentive noun pairs of Bond and Wei (2019) left out, by S/N (read by `scripts/agentive_links.py`; delete a row to restore the pair) |

Future files (not yet needed):
- `lemmas.tsv` — written forms and scripts
- `examples.tsv` — usage examples *(format will change when examples move from synset-level to sense-level)*

## Format: `definitions.tsv`

Tab-separated, UTF-8, with a header row:

```
synset_id	old_value	new_value	reason	source	date
```

| Column | Description |
|--------|-------------|
| `synset_id` | wnja synset ID, e.g. `wnja-11711537-n` |
| `old_value` | Current (wrong) definition text — used as a guard: the apply script aborts if this does not match the current build, catching stale corrections early |
| `new_value` | Corrected definition text |
| `reason` | One-line English explanation of what is wrong and why the new value is better |
| `source` | What found the error: `audit/ornamental`, `manual`, `audit/llm`, etc. |
| `date` | ISO 8601 date the correction was added, e.g. `2026-05-24` |

Only the **first definition** of each synset is currently supported (index 0).
Synsets with multiple definitions are rare in wnja; extend the format with a
`def_index` column if that changes.

## How to add a correction

1. Find the synset ID (use `wn.synset()` or search the XML).
2. Copy the current definition text exactly into `old_value` — paste from the
   XML or from `uv run python -c "import wn; ..."` to avoid typos.
3. Write the corrected text in `new_value`.
4. Add a brief `reason` and set `source` and `date`.
5. Run `uv run python tweak_wnja.py` and verify the log shows the correction
   applied without errors.
6. Open a PR; the CI build will confirm `old_value` still matches.

## Verification behaviour

If `old_value` does not match the definition currently in the XML, `tweak_wnja.py`
logs a **warning** and skips that correction rather than applying a wrong fix.
This means:

- A correction becomes a no-op (and warns) if upstream NTU-MC data fixes the
  same issue — safe, but worth reviewing to delete the now-redundant row.
- A correction warns if the XML was regenerated and the wording shifted —
  update `old_value` after inspecting the new text.

## Contributing

Corrections found by the audit pipeline are added automatically with
`source=audit/*`. Human reviewers can add corrections directly; the `reason`
field is the place to explain context that would not be obvious from the diff
alone (e.g. which Japanese term is standard in botanical contexts).

## Format: `senses.tsv`

Tab-separated, UTF-8, with a header row:

```
synset_id	lemma	action	confidence	reason	source	date	new_lemma
```

A sense is named by its synset ID and the entry's lemma (written form).
`action` is `remove` (delete the sense, and the entry if it has no senses
left), `lower` (set `confidenceScore` to `confidence` unless it is
already lower), `rename` (give the sense the written form `new_lemma`)
or `add` (a new sense with confidence `confidence`; applied by
`build_wnja.py` on the lemma's most frequent reading, the others by
`tweak_wnja.py`).
A renamed sense joins an entry with that lemma and part of speech only if
their hiragana readings match (or one has none), so that homographs such
as 物 (もの, ぶつ) stay apart, and is dropped if that entry already has the
synset; otherwise the old entry is renamed, or copied if it keeps other
senses.  Rows that match no sense are logged and skipped.

The first 2,464 rows (2026-10-24) come from wnja issue #8: senses flagged
by an LLM audit were reviewed twice, blind, by Claude Opus, and the
disagreements adjudicated; see `audit/reviews/issue8/issue8-opus-review.tsv` and
`docs/quality-sample/wnja-opus*/` in the NTU-MC repository.

Rows with source `form: mechanical` (2026-10-24, 235 rows) are made by
`scripts/form_fixes.py` in the NTU-MC repository: full-width digits and
Latin letters made half width (ＶＩＰ → VIP, ２塁打 → 2塁打; 232 lemmas),
and two lemmas typed with katakana okurigana (跡取リ → 跡取り, 走リ → 走り).

Rows with source `jwordnet bug report` come from error reports sent to
jwordnet@gmail.com.  They are senses from `data/sources/wn+var_tk17.tab`, which
fixes in the NTU-MC database cannot reach; definition and example fixes
from the same reports are in `scripts/fixwn.py` in the NTU-MC repository.

## Review files (`review/`)

Not applied by the build; for review before anything is added:

| File | Contents |
|------|---------|
| `kuroda-adjective-review.tsv` | Hand-marked or unclassifiable lines of Kuroda's adjective classes (issue #9) |
| `jsemcor-manual-review.tsv` | JSemCor candidates the review marked POS without a single existing corrected form, or MWE: 196 for a human (issue #11) |
| `jsemcor-suggestions.tsv` | Candidate new senses from the Japanese SemCor, made by `scripts/jsemcor_suggestions.py` (issue #11): the English token's synset, the Japanese lemma the translator used, counts from the aligned corpus and the translation database, whether the POS matches, and an example sentence pair |

Rows with source `jsemcor review` (`add`, 2026-10-24) are senses that
JSemCor translators used, accepted by a blind double Opus review with
adjudication (`audit/reviews/jsemcor/`, issue #11): confidence 0.9 where
both runs agreed, 0.8 if adjudicated or the POS was corrected.  The same
senses are in NTU-MC `scripts/jsemcor-senses.tsv` (applied by `fixwn.py`).
