#!/usr/bin/env python3
"""One-off: put the agentive pairs a review rejected into the change log.

corrections/agentive-removed.tsv lists 70 pairs of Bond and Lim (2019)
that a later review judged not to be linked.  wnja 2.1 had no agentive
links, so leaving them out made no change record; this adds one
``reject_sense_relation`` record for each, so that the pair cannot be
added again without the conflict being seen.

    PYTHONPATH=../NTUMC uv run python -m scripts.reject_agentive

Words are named by the lemma of their entry in the base where the sense
is there (the file may use another form), and as written where it is not.
A rejection of a pair that the log links (the source has the pair twice,
in two spellings, and only one was rejected) is not written but printed,
for a person to rule on.
"""

import csv

from ntumc.wordnet import changes, lmf
from ntumc.wordnet.model import Entry

from release import BASE, BASE_VERSION, CHANGES, ROOT, fetch_base

REMOVED = ROOT / "corrections" / "agentive-removed.tsv"


def main() -> None:
    """Entry point."""
    fetch_base()
    lemmas: dict[tuple[str, str], str] = {}
    for e in lmf.read(BASE):
        if isinstance(e, Entry):
            for s in e.senses:
                for form in [e.lemma, *(f.form for f in e.forms)]:
                    lemmas.setdefault((str(s.synset), str(form)), str(e.lemma))
    records = list(changes.read(CHANGES))
    if any(r.action == "reject_sense_relation" for r in records):
        raise SystemExit(f"{CHANGES} already has the rejections")
    linked = {(r.synset, r.lemma, r.target, r.target_lemma) for r in records
              if r.action == "add_sense_relation"}
    with REMOVED.open(encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            verb = row["verb"]  # as scripts/agentive_links.py: 運転+する -> 運転
            verb = verb[:-3] if verb.endswith("+する") else verb.replace("+", "")
            vs, ns = "wnja-" + row["verb_synset"], "wnja-" + row["noun_synset"]
            pair = (vs, lemmas.get((vs, verb), verb),
                    ns, lemmas.get((ns, row["noun"]), row["noun"]))
            if pair in linked:
                print("linked and rejected, left for a person:", row["sn"], *pair)
                continue
            records.append(changes.Change(
                id=f"wnja-{BASE_VERSION}-{len(records) + 1}",
                action="reject_sense_relation", lexicon="wnja",
                synset=pair[0], lemma=pair[1], target=pair[2], target_lemma=pair[3],
                user="fcbond", date=row["date"], source=row["source"],
                reason=f"{row['reason']} (S/N {row['sn']})",
                base=f"wnja-{BASE_VERSION}"))
    print(changes.write(CHANGES, records), "records")


if __name__ == "__main__":
    main()
