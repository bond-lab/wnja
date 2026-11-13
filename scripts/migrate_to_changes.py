#!/usr/bin/env python3
"""One-off: turn the work done since wnja 2.1 into change records.

wnja 2.1 was built by build_wnja.py and tweak_wnja.py from the NTU-MC
export and corrections/.  From 2.1 on, the released XML is the base and
changes/ is the master (see changes/README.md).  This script made the
first change log:

    uv run python build_wnja.py && uv run python tweak_wnja.py
    PYTHONPATH=../NTUMC uv run python -m ntumc.wordnet diff \
        build/base/wnja-2.1.xml build/wnja-2.1.xml build/since-2.1.jsonl \
        --user fcbond --source build --reason "since 2.1" --base wnja-2.1
    PYTHONPATH=../NTUMC uv run python -m scripts.migrate_to_changes \
        build/since-2.1.jsonl changes/wnja-2.1.jsonl

It gives each record from the diff the source, reason and date of the
row in corrections/ (or data/derived/agentive_links.tsv) that caused it.
A sense added since 2.1 may come from a ``keep`` or ``lower`` row: a
removal made before 2.1 and reversed after it.  An agentive link may
name its words in another spelling than the entry's lemma, so links are
also matched on the two synsets alone.
"""

import csv
import sys
from pathlib import Path

from ntumc.wordnet import changes

ROOT = Path(__file__).resolve().parent.parent
AGENTIVE = ("Bond and Lim (2019): pairs checked twice by hand "
            "(data/derived/agentive_links.tsv)")


def rows(path: Path) -> list[dict[str, str]]:
    """The rows of a tab-separated file with a header."""
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def main() -> None:
    """Entry point."""
    raw, out = Path(sys.argv[1]), Path(sys.argv[2])
    senses: dict[tuple[str, str, str], dict[str, str]] = {}
    by_lemma: dict[str, dict[str, str]] = {}
    renamed: dict[str, dict[str, str]] = {}
    for r in rows(ROOT / "corrections" / "senses.tsv"):
        kind = "add" if r["action"] == "add" else "change"
        senses[(kind, r["synset_id"], r["lemma"])] = r
        by_lemma[r["lemma"]] = r
        if r["action"] == "rename":
            senses[("add", r["synset_id"], r["new_lemma"])] = r
            renamed[r["new_lemma"]] = r
    corrections = ROOT / "corrections"
    defs = {r["synset_id"]: r for r in rows(corrections / "definitions.tsv")}
    forms = {(r["lemma"], r["form"]): r for r in rows(corrections / "forms.tsv")}
    links = {}
    for r in rows(ROOT / "data" / "derived" / "agentive_links.tsv"):
        links[(r["verb_synset"], r["verb"], r["noun_synset"], r["noun"])] = r
        links[(r["noun_synset"], r["noun"], r["verb_synset"], r["verb"])] = r
        links[(r["verb_synset"], r["noun_synset"])] = r
        links[(r["noun_synset"], r["verb_synset"])] = r
    records, unmatched = [], 0
    for n, c in enumerate(changes.read(raw), start=1):
        c.id = f"wnja-2.1-{n}"
        row = None
        if c.action in ("remove_sense", "set_confidence"):
            row = senses.get(("change", c.synset, c.lemma))
        elif c.action == "add_sense":
            row = (senses.get(("add", c.synset, c.lemma))
                   or senses.get(("change", c.synset, c.lemma)))
        elif c.action == "set_definition":
            row = defs.get(c.synset)
        elif c.action == "add_form":
            row = (forms.get((c.lemma, c.new)) or renamed.get(c.lemma)
                   or by_lemma.get(c.lemma))
        elif c.action == "set_count":
            c.source = f"{c.corpus} (scripts/count_jsemcor.py)"
            c.reason = "a sense added since 2.1"
            records.append(c)
            continue
        elif c.action == "add_sense_relation":
            link = (links.get((c.synset, c.lemma, c.target, c.target_lemma))
                    or links.get((c.synset, c.target)))
            if link:
                c.source = AGENTIVE
                c.reason = f"agentive noun link, {link['link_type']} (S/N {link['sn']})"
                records.append(c)
                continue
        if row is None:
            unmatched += 1
        else:
            c.source, c.reason, c.date = row["source"], row["reason"], row["date"]
            if row.get("action") == "rename":
                c.reason = f"renamed {row['lemma']} to {row['new_lemma']}: {c.reason}"
        records.append(c)
    n = changes.write(out, records)
    print(f"{n} records, {unmatched} without a corrections row")


if __name__ == "__main__":
    main()
