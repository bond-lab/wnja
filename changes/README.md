# Changes

From wnja 2.1 on, the master copy of wnja is

* the last release, `wnja-2.1.xml` (fetched from the GitHub release and
  checked against its checksum by `release.py`), and
* `wnja-2.1.jsonl`: every change made since, one per line, in order.

`uv run python release.py 2.2` applies the changes to the base and
writes `build/wnja-2.2.xml`.  After a release, the new XML is the base
and a new, empty `wnja-2.2.jsonl` is started; the old file stays as the
history.

Nothing is rebuilt from the NTU-MC export any more: `build_wnja.py`,
`tweak_wnja.py` and `corrections/` are how 2.1 and the first change log
were made, and are kept for the record.

## A change record

```json
{"id": "wnja-2.1-12", "action": "remove_sense", "lexicon": "wnja",
 "synset": "wnja-00908351-v", "lemma": "育種", "pos": "v",
 "user": "fcbond", "date": "2026-11-05", "source": "spot check: native checker",
 "reason": "...", "base": "wnja-2.1"}
```

To make a change, add a line at the end.  Never edit or delete an
earlier line: to undo a change, add the opposite one.

* `id` is unique; `user`, `source` (the evidence: a person, a
  dictionary, a corpus) and `reason` are required.
* A model alone is not a source for an addition.
* Where a word has several entries (反る ソル/カエル), give `reading`.

* A new adjective entry has no class tag: `release.py` lists adjectives
  that Kuroda's data classifies but the lexicon does not tag, with the
  `set_tag` record to add.
* `reject_sense_relation` records a decision that two senses are not
  related; adding that relation later is a conflict.

The actions (`add_sense`, `remove_sense`, `set_confidence`,
`set_definition`, `add_form`, `add_sense_relation`, ...) and the fields
each needs are in NTU-MC `docs/wordnet-model.md`.

## The first log

`wnja-2.1.jsonl` starts with the 2,133 changes between the 2.1 release
and the repository on 2026-11-13: 34 senses removed, 12 added, 3
confidences, 2 counts, 6 forms, 3 definitions and 2,073 sense relations
(the agentive noun links of Bond and Lim 2019).  They were made by
building once more the old way and comparing with 2.1
(`scripts/migrate_to_changes.py`); each has the source, reason and date
of its row in `corrections/`.

They are followed by 69 `reject_sense_relation` records: the Bond and
Lim pairs a review rejected (`corrections/agentive-removed.tsv`), which
were never in a release and so made no change
(`scripts/reject_agentive.py`).  The 70th, S/N 47610, is not recorded:
the same two senses are linked under another spelling (S/N 47607).
