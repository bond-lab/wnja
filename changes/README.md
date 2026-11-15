# Changes

From wnja 2.1 on, the master copy of wnja is

* the last release, `wnja-2.1.xml` (fetched from the GitHub release and
  checked against its checksum by `release.py`), and
* `wnja-2.1.jsonl`: every change made since, one per line, in order.

`uv run python release.py 2.2` applies the changes to the base and
writes `build/wnja-2.2.xml`.  After a release, a new, empty
`wnja-2.2.jsonl` is started; the old file stays as the history.

Each lexicon is written twice: the minimal lexicon `ID-min-VERSION.xml`
(id `ID-min`), which says only what this wordnet itself says, and the
export `ID-VERSION.xml`, which is what is packaged.  For a concept CILI
does not have yet, the minimal lexicon holds the words and the
concept's identifier (`dc:identifier="ntumc00001"`); the English
definition and the relations are in the shared lexicon `omw-ntumc`, and
the export puts them back.  `release.py` fails if the export lacks
anything the wordnet said before it was split.  `omw-ntumc` is read from
a checkout beside this one, built at the version `release.py` pins
(`uv run build.py 0.1` at tag `v0.1`).  After a release, the minimal XML
is the base.

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
the same two senses are linked under another spelling (S/N 47607), and
the link stays (ruled by fcbond, 2026-11-13).

Then come 457 `add_sense` records (`scripts/add_back_ntumc.py`): senses
of the NTU-MC wordnet that the old build dropped because Kuribayashi's
data did not have them, added back where an NTU-MC annotator added the
sense (398) or the NTU-MC corpus is tagged with it (109).

## Instance relations turned round (2026-11-13)

In the Princeton data an instance points to its class
(`instance_hypernym`), but the instance links NTU-MC annotators made
from new synsets were entered the other way, so the export wrote them
backwards (AirAsia had airline as its *instance*).  All 447 such links
were read; 437 are wrong and are listed in omw-ntumc
`instance-links-turned.tsv`.  Each one in this lexicon is fixed by a
`remove_relation` and an `add_relation` record, in both directions.

## Linked to omw-ntumc (2026-11-14)

Each synset for a concept that Princeton WordNet does not have now
carries the concept's identifier from the omw-ntumc registry
(`dc:identifier="ntumc00392"`), set by a `set_identifier` record.  The
same concept has the same identifier in every lexicon.  This is the
first step towards a minimal lexicon whose new concepts keep their
definition and relations only in omw-ntumc.  The pronoun synsets (ids
beginning 77) are not linked here: they belong to omw-pronoun.

The pronoun synsets carry omw-pronoun's identifiers instead
(`dc:identifier="pronoun:1;Sg"`), from its table of NTU-MC synsets.

## Validation

`release.py` runs the validation of the `wn` module on the files it
writes (NTU-MC `ntumc.wordnet.validate`).  An error stops the release,
and so do an ILI on two synsets (`W302`) and a word twice in a synset
(`W202`).  Two entries for one written form in a synset (`W203`) do
not: there is an entry for each reading, so 物 (ぶつ, もの) is twice in
a synset.  That is so for 1,466 written forms.
