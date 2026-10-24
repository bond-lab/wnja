"""Blind review of the JSemCor candidate senses (issue #11).

Like the issue #8 review: the candidates in
corrections/jsemcor-suggestions.tsv are shuffled under random ids and
split into batches of about 150, without counts or sources, so a
reviewer judges each sense only on its meaning and the example.

    uv run python -m audit.jsemcor_review batches   # write batchNN.tsv
    uv run python -m audit.jsemcor_review combine   # runs -> verdicts.tsv
    uv run python -m audit.jsemcor_review apply     # -> corrections/

Reviewers write ``batchNN-review-<run>.tsv`` next to each batch (columns
id, word, verdict, reason), with verdict one of:

    ADD     a correct sense as it is
    POS     right meaning, but the word is another part of speech
    MWE     only part of a longer expression meaning this
    WRONG   a loose translation, or not this meaning
    UNSURE
"""

import argparse
import csv
import datetime
import random
import re
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

SUGGESTIONS = Path("corrections/jsemcor-suggestions.tsv")
RELEASE = Path("wnja-2.0.xml")
REVIEW_DIR = Path("audit/jsemcor-review")
BATCH = 150
VERDICTS = ("ADD", "POS", "MWE", "WRONG", "UNSURE")


def release_synsets(path: Path) -> dict[str, tuple[list[str], str]]:
    """{synset: (Japanese lemmas, Japanese definition)} from the release."""
    lemmas: dict[str, list[str]] = {}
    defs: dict[str, str] = {}
    lemma = ""
    for _ev, el in ET.iterparse(path, events=("end",)):
        if el.tag == "Lemma":
            lemma = el.get("writtenForm")
        elif el.tag == "Sense":
            lemmas.setdefault(el.get("synset")[5:], []).append(lemma)
        elif el.tag == "Synset":
            d = el.find("Definition")
            if d is not None and d.text:
                defs[el.get("id")[5:]] = d.text
            el.clear()
    return {s: (lemmas.get(s, []), defs.get(s, "")) for s in set(lemmas) | set(defs)}


def make_batches(seed: int = 11) -> None:
    """Write shuffled, blind batches and the id key."""
    with SUGGESTIONS.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    known = release_synsets(RELEASE)
    rng = random.Random(seed)
    ids = rng.sample(range(10000, 99999), len(rows))
    rng.shuffle(rows)
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    cols = ["id", "synset", "word", "word_pos", "other_words_in_synset",
            "english_words", "english_definition", "japanese_definition",
            "example_en", "example_ja"]
    with (REVIEW_DIR / "key.tsv").open("w", encoding="utf-8") as key:
        key.write("id\tsynset\tlemma\n")
        for n in range(0, len(rows), BATCH):
            path = REVIEW_DIR / f"batch{n // BATCH + 1:02d}.tsv"
            with path.open("w", encoding="utf-8") as out:
                out.write("\t".join(cols) + "\n")
                for i, r in zip(ids[n:n + BATCH], rows[n:n + BATCH]):
                    synset = r["synset"]
                    others, jdef = known.get(synset.replace("-a", "-s"),
                                             known.get(synset, ([], "")))
                    out.write("\t".join([
                        str(i), synset, r["lemma"], r["jpos"],
                        " ".join(others), r["english_lemmas"], r["definition"],
                        jdef, r["example_en"], r["example_ja"]]) + "\n")
                    key.write(f"{i}\t{synset}\t{r['lemma']}\n")
    print(f"{len(rows)} candidates in {(len(rows) - 1) // BATCH + 1} batches")


def read_run(run: str) -> dict[str, tuple[str, str]]:
    """{id: (verdict, reason)} from all batchNN-review-<run>.tsv files."""
    out = {}
    for path in sorted(REVIEW_DIR.glob(f"batch*-review-{run}.tsv")):
        with path.open(encoding="utf-8") as fh:
            for r in csv.DictReader(fh, delimiter="\t"):
                v = (r.get("verdict") or "").strip().upper()
                out[r["id"]] = (v if v in VERDICTS else "UNSURE",
                                r.get("reason") or "")
    return out


def combine(runs: list[str]) -> None:
    """Join the runs and any adjudication into verdicts.tsv."""
    with (REVIEW_DIR / "key.tsv").open(encoding="utf-8") as fh:
        key = list(csv.DictReader(fh, delimiter="\t"))
    results = [read_run(r) for r in runs]
    adjudicated = read_run("adjudicated")
    final = Counter()
    with (REVIEW_DIR / "verdicts.tsv").open("w", encoding="utf-8") as out:
        out.write("synset\tlemma\t" + "\t".join(runs)
                  + "\tfinal\tdecided_by\treason\n")
        for k in key:
            vs = [r.get(k["id"], ("", ""))[0] for r in results]
            if k["id"] in adjudicated:
                v, why = adjudicated[k["id"]]
                by = "adjudicated"
            elif vs and all(v == vs[0] for v in vs) and vs[0]:
                v, why, by = vs[0], results[0][k["id"]][1], "all runs"
            else:
                v, why, by = "", "", "disagree"
            final[v or "disagree"] += 1
            out.write("\t".join([k["synset"], k["lemma"], *vs, v, by, why]) + "\n")
    print(dict(final))


SENSES = Path("corrections/senses.tsv")
MANUAL = Path("corrections/jsemcor-manual-review.tsv")
NTUMC_SENSES = Path("../NTUMC/scripts/jsemcor-senses.tsv")
ARROW = re.compile(r"^→\s*([^\s,;(（/]+)")
SOURCE = "jsemcor review"


def known_lemmas(path: Path) -> set[str]:
    """Lemmas of all entries in the release."""
    return {el.get("writtenForm")
            for _ev, el in ET.iterparse(path, events=("start",))
            if el.tag == "Lemma"}


def accepted(rows: list[dict], lemmas: set[str]) -> tuple[list, list]:
    """Split the verdicts into senses to add and ones for a human.

    ADD is added at confidence 0.9 if both runs agreed, 0.8 if
    adjudicated.  POS is added at 0.8 in its corrected form when the
    reason gives one word that is already a wnja lemma.  Other POS and
    all MWE verdicts are for a human.

    Args:
        rows: verdicts.tsv rows.
        lemmas: Lemmas already in the release.

    Returns:
        (adds as (synset, lemma, confidence, reason), rows for review).
    """
    adds, manual = [], []
    for r in rows:
        if r["final"] == "ADD":
            conf = 0.9 if r["decided_by"] == "all runs" else 0.8
            adds.append((r["synset"], r["lemma"], conf,
                         f"JSemCor translation ({r['decided_by']})"))
        elif r["final"] in ("POS", "MWE"):
            m = ARROW.match(r["reason"].strip())
            form = m.group(1).rstrip(".;") if m else ""
            if r["final"] == "POS" and form in lemmas:
                adds.append((r["synset"], form, 0.8,
                             f"JSemCor translation {r['lemma']}, POS corrected"))
            else:
                manual.append(r)
    return adds, manual


def apply() -> None:
    """Write the accepted senses as add rows, and the rest for review."""
    with (REVIEW_DIR / "verdicts.tsv").open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    adds, manual = accepted(rows, known_lemmas(RELEASE))
    with SENSES.open(encoding="utf-8") as fh:
        existing = {(r["synset_id"], r["lemma"])
                    for r in csv.DictReader(fh, delimiter="\t")}
    today = datetime.date.today().isoformat()
    new = [(f"wnja-{s}", lemma, conf, why) for s, lemma, conf, why in adds
           if (f"wnja-{s}", lemma) not in existing]
    with SENSES.open("a", encoding="utf-8") as out:
        for sid, lemma, conf, why in sorted(set(new)):
            out.write(f"{sid}\t{lemma}\tadd\t{conf}\t{why}\t{SOURCE}\t{today}\t\n")
    with MANUAL.open("w", encoding="utf-8") as out:
        out.write("synset\tlemma\tverdict\tdecided_by\treason\n")
        for r in manual:
            out.write(f"{r['synset']}\t{r['lemma']}\t{r['final']}\t"
                      f"{r['decided_by']}\t{r['reason']}\n")
    if NTUMC_SENSES.parent.exists():
        with NTUMC_SENSES.open("w", encoding="utf-8") as out:
            out.write("synset\tlemma\tconfidence\n")
            for s, lemma, conf, _why in sorted(set(adds)):
                out.write(f"{s}\t{lemma}\t{conf}\n")
    print(f"{len(set(new))} add rows -> {SENSES}; {len(manual)} for review "
          f"-> {MANUAL}; {len(set(adds))} senses -> {NTUMC_SENSES}")


def main() -> None:
    """Command line entry point."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("batches")
    c = sub.add_parser("combine")
    c.add_argument("--runs", nargs="+", default=["run1", "run2"])
    sub.add_parser("apply")
    args = ap.parse_args()
    if args.cmd == "batches":
        make_batches()
    elif args.cmd == "combine":
        combine(args.runs)
    else:
        apply()


if __name__ == "__main__":
    main()
