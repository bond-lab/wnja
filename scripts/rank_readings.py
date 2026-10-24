#!/usr/bin/env python3
"""Pre-compute how common each reading of a homograph is.

A spelling with several readings (e.g. 音 ネ / オト) is several entries in
wnja.  Corpus counts are tagged by spelling, not reading, so the build
gives each count to one reading only: the most frequent one according to
the JMdict priority tags (nfXX, news1, ichi1, ...).

Reads data/sources/vars_tk17.tab, checks every spelling with more than
one reading in jamdict, and writes data/derived/reading_rank.tsv.  Run this once (or
whenever vars_tk17.tab changes) before build_wnja.py, like
detect_sahen.py.
"""

import logging
import sys
from collections import defaultdict

import jamdict

from build_wnja import RANK_FILE, VARS_FILE, read_vars, reading

UNRANKED = 100  # score for readings JMdict does not mark as common

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s",
                    stream=sys.stderr)
log = logging.getLogger("rank")


def hiragana(kana: str) -> str:
    """Convert katakana to hiragana (other characters are unchanged)."""
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in kana)


def priority_score(pri: list[str]) -> int:
    """Turn JMdict priority tags into a rank: lower is more frequent.

    Args:
        pri: Priority tags of one reading, e.g. ['ichi1', 'news1', 'nf02'].

    Returns:
        The nfXX band (1-48) if present, 30 for a *1 tag, 60 for a *2 tag,
        otherwise UNRANKED.
    """
    score = UNRANKED
    for p in pri:
        if p.startswith("nf"):
            score = min(score, int(p[2:]))
        elif p[-1] == "1":
            score = min(score, 30)
        elif p[-1] == "2":
            score = min(score, 60)
    return score


def reading_score(jam: jamdict.Jamdict, spelling: str, kana: str) -> int:
    """Best JMdict rank of a spelling read as kana (lower is more frequent).

    Args:
        jam: An open Jamdict.
        spelling: The written form, e.g. 音.
        kana: The reading in katakana, e.g. オト.

    Returns:
        The best priority_score over JMdict entries with this spelling and
        reading, or UNRANKED if there are none.
    """
    hira = hiragana(kana)
    best = UNRANKED
    for entry in jam.lookup(spelling, strict_lookup=True).entries:
        if not any(k.text == spelling for k in entry.kanji_forms):
            continue
        for r in entry.kana_forms:
            if r.text in (hira, kana):
                best = min(best, priority_score(list(r.pri)))
    return best


def main() -> None:
    """Rank the readings of every spelling with more than one."""
    forms = read_vars(VARS_FILE)
    readings: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for (lemma, hno), form_list in forms.items():
        yomi = reading(form_list)
        if yomi:
            readings[lemma].append((hno, yomi))
    homographs = {k: v for k, v in readings.items() if len(v) > 1}
    log.info("%d spellings with more than one reading", len(homographs))

    jam = jamdict.Jamdict()
    with RANK_FILE.open("w", encoding="utf-8") as out:
        out.write("lemma\thno\treading\tscore\n")
        for lemma in sorted(homographs):
            for hno, yomi in sorted(homographs[lemma]):
                score = reading_score(jam, lemma, yomi)
                out.write(f"{lemma}\t{hno}\t{yomi}\t{score}\n")
    log.info("Wrote %s", RANK_FILE)


if __name__ == "__main__":
    main()
