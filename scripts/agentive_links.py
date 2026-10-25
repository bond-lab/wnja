#!/usr/bin/env python3
"""Extract the agentive noun links of Bond and Wei (2019).

Ryan Lim Dao Wei generated candidate pairs of a verb sense and a noun
sense whose lemma is the verb (stem) plus an agentive suffix (者, 人, 手,
員, 士, 家, 機, 器: 運転+する → 運転者), and checked each pair by hand
twice, typing the good ones with the morphosemantic relations of Fellbaum
et al. (2009): agent, instrument, undergoer/patient, ...  The data is
data/sources/agents_240619.xlsx (sheet ``agents``; the Japanese rows) and
the method and results are described in
data/sources/agents_tagging_report_2019.docx.

A pair is used if the second check (column ``link_2nd check``) accepted it,
with the type from the second check, or the first if the second has none.
Suru-verbs are named by their stem, as in wnja (運転+する -> 運転).

Writes data/derived/agentive_links.tsv, read by tweak_wnja.py, which links
the verb sense to the noun sense as OEWN does: relType ``other`` with the
type in dc:type, plus ``derivation`` both ways.  (The GWA agent, patient,
instrument, ... relations are synset relations from the noun to the verb,
and cover only some of the types.)

Usage: uv run python -m scripts.agentive_links
"""

import csv
import logging
import sys
from collections import Counter

from build_wnja import AGENT_LINKS_FILE, AGENTS_FILE

log = logging.getLogger("agentive")

#: the report's link types -> the dc:type of OEWN's ``other`` sense relations
REL_TYPES = {
    "agent": "agent",
    "instrument": "instrument",
    "undergoer/patient": "undergoer",
    "vehicle": "vehicle",
    "body-part": "body_part",
    "inanimate agent/cause": "by_means_of",
    "purpose/function": "uses",
}
HEAD = ["verb_synset", "verb", "noun_synset", "noun", "suffix", "rel_type",
        "link_type", "first_check", "second_check", "sn"]


def link(row: dict) -> list | None:
    """The output row for one checked pair, or None if it is not used."""
    if row.get("lang") != "jpn" or row.get("link_2nd check") != "yes":
        return None
    label = row.get("link type_2nd check") or row.get("link type")
    if label not in REL_TYPES:
        log.warning("row %s: link type %r not known, skipping", row["S/N"], label)
        return None
    verb = row["verb"]  # wnja writes suru-verbs without する: 運転+する -> 運転
    verb = verb[:-3] if verb.endswith("+する") else verb.replace("+", "")
    return ["wnja-" + row["verbsynset"], verb,
            "wnja-" + row["nounsynset"], row["noun"], row["match type"],
            REL_TYPES[label], label, row["link"], row["link_2nd check"],
            row["S/N"]]


def main() -> None:
    import openpyxl

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    sheet = openpyxl.load_workbook(AGENTS_FILE, read_only=True)["agents"]
    rows = sheet.iter_rows(values_only=True)
    head = next(rows)
    out = [x for r in rows if (x := link(dict(zip(head, r))))]
    out.sort(key=lambda x: x[:4])
    with AGENT_LINKS_FILE.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(HEAD)
        w.writerows(out)
    log.info("%d links -> %s %s", len(out), AGENT_LINKS_FILE,
             dict(Counter(x[5] for x in out)))


if __name__ == "__main__":
    main()
