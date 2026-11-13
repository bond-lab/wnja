#!/usr/bin/env python3
"""One-off: add back NTU-MC senses that the old wnja build dropped.

The old build took Kuribayashi's data (data/sources/wn+var_tk17.tab) as
the authority for every synset it covers, so a Japanese sense of the
NTU-MC wordnet in such a synset was left out unless that file had it.
Two kinds have a human source and are added back here as ``add_sense``
records: senses added in NTU-MC by an annotator (src ``ntumc``), and
senses that the NTU-MC corpus is tagged with.  The other dropped senses
(Japanese WordNet 1.1 senses that are not in the 2017 data and not used
in the corpus) stay out.

    PYTHONPATH=../NTUMC uv run python -m scripts.add_back_ntumc CANDIDATES.tsv

CANDIDATES.tsv (synset, lemma, pos, reading, src, usr, corpus,
ntumc_lemma) was made from the NTU-MC wordnet database frozen on
2026-11-13 and the Japanese corpus: lemmas are respelled as wnja writes
them (発語+する -> 発語), and where wnja has several entries for a
spelling the sense goes on the most frequent reading
(data/derived/reading_rank.tsv), as the old build's additions did.
"""

import csv
import sys
from pathlib import Path

from ntumc.wordnet import changes

from release import BASE_VERSION, CHANGES


def main() -> None:
    """Entry point."""
    records = list(changes.read(CHANGES))
    if any("dropped by the old build" in (r.reason or "") for r in records):
        raise SystemExit(f"{CHANGES} already has these senses")
    with Path(sys.argv[1]).open(encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            source = []
            if row["src"] == "ntumc":
                by = f" by {row['usr']}" if row["usr"] else ""
                source.append(f"NTU-MC wordnet: sense added{by}")
            if int(row["corpus"]):
                source.append(f"NTU-MC corpus: {row['corpus']} concepts tagged with it")
            reason = "dropped by the old build (not in wn+var_tk17); added back"
            if row["ntumc_lemma"] != row["lemma"]:
                reason += f"; {row['ntumc_lemma']} in NTU-MC"
            records.append(changes.Change(
                id=f"wnja-{BASE_VERSION}-{len(records) + 1}", action="add_sense",
                lexicon="wnja", synset=row["synset"], lemma=row["lemma"],
                pos=row["pos"], reading=row["reading"] or None, user="fcbond", date="2026-11-13",
                source="; ".join(source), reason=reason, base=f"wnja-{BASE_VERSION}"))
    print(changes.write(CHANGES, records), "records")


if __name__ == "__main__":
    main()
