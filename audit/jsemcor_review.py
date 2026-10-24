"""Blind review of the JSemCor candidate senses (issue #11).

Like the issue #8 review: the candidates in
corrections/jsemcor-suggestions.tsv are shuffled under random ids and
split into batches of about 150, without counts or sources, so a
reviewer judges each sense only on its meaning and the example.

    uv run python -m audit.jsemcor_review batches   # write batchNN.tsv
    uv run python -m audit.jsemcor_review combine   # runs -> verdicts.tsv

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
import random
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


def main() -> None:
    """Command line entry point."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("batches")
    c = sub.add_parser("combine")
    c.add_argument("--runs", nargs="+", default=["run1", "run2"])
    args = ap.parse_args()
    if args.cmd == "batches":
        make_batches()
    else:
        combine(args.runs)


if __name__ == "__main__":
    main()
