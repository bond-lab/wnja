# Japanese definitions and examples for synsets wnja has no word for

wnja only holds synsets that have a Japanese word.  The NTU-MC wordnet
database also has Japanese definitions and examples (from the Japanese
WordNet's translation of the Princeton glosses) for synsets with no
Japanese word.  They are kept here so that a synset added later
(`add_synset` in `changes/`) can take its definition and examples from
them and not be written again.

| file | content |
|---|---|
| `definitions.tsv` | `synset_id`, `n` (order of the part within the synset), `text` |
| `examples.tsv` | the same, for examples |

A definition in several parts is joined with `; ` in wnja, in the order
of `n`.  The synset id is the wnja id (`wnja-` and the Princeton WordNet
3.0 offset, or an NTU-MC id).  Taken on 2026-11-13 from the NTU-MC
database as frozen that day, for every synset not in wnja 2.1.

Nothing here is in a release.
