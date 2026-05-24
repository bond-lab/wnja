# wnja Corrections

Manual corrections to the generated `wnja-2.0.xml`, applied by `tweak_wnja.py`
as the final build step.

Corrections are preferred over editing the XML directly because the XML is a
build artefact — it is regenerated from NTU-MC source data by `build_wnja.py`.
Storing corrections here keeps fixes auditable, reviewable as diffs, and
resilient to upstream rebuilds.

## Files

| File | Corrects |
|------|---------|
| `definitions.tsv` | Japanese definition text |

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
