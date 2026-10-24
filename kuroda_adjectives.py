#!/usr/bin/env python3
"""Extract Kuroda's adjective classes (issue #9).

Kow Kuroda marked how each Japanese adjectival ends when it modifies a
noun (Kuroda et al. 2011, https://www.anlp.jp/proceedings/annual_meeting/
2011/pdf_dir/A4-1.pdf):

    a. i      イ形容詞, ending in (し)い — the default, not in the file
    b. na     ナ形容詞 (形容動詞), ending in な
    c. no     ノ形容詞, ending in の, e.g. 本当の
    d. na-no  な or の, な more natural
    e. no-na  の or な, の more natural
    f. other  ending in たる or なる

data/adjective_kuroda_2011.tsv has one line per (synset, lemma), with
the endings after a +, e.g. ``ルーズ+な`` or ``真+の,な``.  Some lines
carry hand marks: ``*`` (an error), ``#`` (a comment), ``X<=Y`` (lemma
corrected from Y), ``?Y=>X`` (suggested replacement) and ``?`` in an
ending (uncertain).  Those, and endings outside the classes above, are
not used; they are written to a review file instead.

The class belongs to the word, so it is kept per lemma; when a lemma's
synsets disagree, the most common class wins and the lemma is listed
for review.

Writes data/adjective_class.tsv (lemma, class), read by build_wnja.py,
and corrections/kuroda-adjective-review.tsv.
"""

import logging
import sys
from collections import Counter, defaultdict
from pathlib import Path

SOURCE = Path("data/adjective_kuroda_2011.tsv")
CLASS_FILE = Path("data/adjective_class.tsv")
REVIEW_FILE = Path("corrections/kuroda-adjective-review.tsv")

# Kuroda's endings -> class name
CLASSES = {
    "な": "na",
    "(的)な": "na",
    "の": "no",
    "な,の": "na-no",
    "の,な": "no-na",
    "たる": "other",
    "なる": "other",
    "とした,たる": "other",
}

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s",
                    stream=sys.stderr)
log = logging.getLogger("kuroda")


def classify(annotation: str) -> tuple[str | None, str | None, str]:
    """Read one annotation, e.g. ``ルーズ+な``.

    Args:
        annotation: The second column of the source file.

    Returns:
        (lemma, class, reason): class is None when the line needs review,
        and reason then says why; lemma is None if there is none.
    """
    if annotation[:1] in "*#":
        return None, None, "marked as error or comment"
    if "<=" in annotation or "=>" in annotation:
        return None, None, "lemma correction"
    if "+" not in annotation:
        return None, None, "no ending given"
    lemma, ending = annotation.split("+", 1)
    if lemma.startswith("?"):
        return None, None, "uncertain lemma"
    if "?" in ending:
        return lemma, None, "uncertain ending"
    if ending == "":
        return lemma, None, "no ending: bound form"
    if ending not in CLASSES:
        return lemma, None, f"ending {ending} not one of the classes"
    return lemma, CLASSES[ending], ""


def main() -> None:
    """Write the class file and the review file."""
    votes: dict[str, Counter] = defaultdict(Counter)
    review: list[tuple[str, str, str]] = []
    for line in SOURCE.read_text(encoding="utf-8").splitlines():
        synset, annotation = line.split("\t")
        lemma, cls, reason = classify(annotation)
        if cls is None:
            review.append((synset, annotation, reason))
        else:
            votes[lemma][cls] += 1

    with CLASS_FILE.open("w", encoding="utf-8") as out:
        out.write("lemma\tclass\n")
        for lemma in sorted(votes):
            cls = votes[lemma].most_common(1)[0][0]
            out.write(f"{lemma}\t{cls}\n")
            if len(votes[lemma]) > 1:
                seen = ", ".join(f"{c} {n}" for c, n in votes[lemma].items())
                review.append(("", lemma, f"classes differ ({seen}); used {cls}"))

    with REVIEW_FILE.open("w", encoding="utf-8") as out:
        out.write("synset\tannotation\treason\n")
        for row in review:
            out.write("\t".join(row) + "\n")
    log.info("%d lemmas classified -> %s; %d lines for review -> %s",
             len(votes), CLASS_FILE, len(review), REVIEW_FILE)


if __name__ == "__main__":
    main()
