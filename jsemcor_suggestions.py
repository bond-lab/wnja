#!/usr/bin/env python3
"""Candidate new senses from the Japanese SemCor (issue #11).

JSemCor was made by translating English SemCor; translators were offered
the Japanese wordnet synonyms of each sense-tagged English word.  When
they used another word, that word is a candidate new sense of the
English word's synset.  Two sources record this:

1. The aligned JSemCor (semcor_jpn.zip, 2012): each Japanese term links
   to the English SemCor token it translates, e.g.
   ``<externalRef resource="Semcor eng 3.0" reference="s72.90.7"/>``
   (pnum 72, snum 90, token 7; tokens are 1-based and include <punc>).
   A term whose word is in the wordnet but has no synset is a candidate:
   the English token's synset plus the Japanese lemma.
2. The translation database (traduku SQL dump, 2014): ``chosen`` words
   that were not among the ``word`` candidates offered for that token.
   Only the phrase, word and chosen tables are read (not users or log,
   which hold personal data).

Senses already in wnja-2.0.xml are left out.  Writes
corrections/jsemcor-suggestions.tsv, one row per (synset, lemma), for
review.

Usage:
    uv run python jsemcor_suggestions.py [--aligned ZIP] [--semcor DIR]
                                         [--traduku SQL.GZ]
"""

import argparse
import gzip
import logging
import re
import sys
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

import wn

DOWNLOADS = Path.home() / "Downloads"
DEFAULT_ALIGNED = DOWNLOADS / "semcor_jpn.zip"
DEFAULT_TRADUKU = DOWNLOADS / "2014-01-16-traduku.sql.gz"
DEFAULT_SEMCOR = Path("/home/bond/papers/students/NTU/liling/semcor/semcor3.0")
INDEX_SENSE = Path.home() / "nltk_data/corpora/wordnet/index.sense"
RELEASE = Path("wnja-2.0.xml")
OUTPUT = Path("corrections/jsemcor-suggestions.tsv")
JAPANESE = re.compile(r"[぀-ヿ一-鿿]")
POS = {"1": "n", "2": "v", "3": "a", "4": "r", "5": "a"}

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s",
                    stream=sys.stderr)
log = logging.getLogger("jsemcor")


@dataclass
class Token:
    """One English SemCor token."""

    word: str
    synset: str | None


@dataclass
class Candidate:
    """A suggested (synset, lemma) sense, with its evidence."""

    jpos: str = ""
    aligned: int = 0
    traduku: int = 0
    english: Counter = field(default_factory=Counter)
    example: tuple[str, str] = ("", "")


def load_sense_index(path: Path) -> dict[str, str]:
    """PWN 3.0 sense key -> synset id like 02604760-v (satellites as -a)."""
    keys = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            key, offset, *_ = line.split()
            keys[key] = f"{offset}-{POS[key.split('%')[1][0]]}"
    return keys


WF = re.compile(r"<(wf|punc)([^>]*)>([^<]*)</(?:wf|punc)>")
ATTR = re.compile(r"(\w+)=([^\s>]+)")


def read_semcor(path: Path, keys: dict[str, str]) -> dict[tuple[int, int], Token]:
    """Tokens of one SemCor file, by (snum, 1-based index including punc).

    Args:
        path: A SemCor 3.0 tagfile, e.g. brown1/tagfiles/br-a01.
        keys: Sense key index from load_sense_index.

    Returns:
        {(snum, index): Token}.
    """
    tokens: dict[tuple[int, int], Token] = {}
    snum = index = 0
    for line in open(path, encoding="latin-1"):
        if line.startswith("<s snum="):
            snum, index = int(line[8:].split(">")[0]), 0
        m = WF.search(line)
        if not m:
            continue
        index += 1
        a = dict(ATTR.findall(m.group(2)))
        synset = None
        if "lemma" in a and "lexsn" in a:
            synset = keys.get(f"{a['lemma']}%{a['lexsn'].split(';')[0]}")
        tokens[(snum, index)] = Token(m.group(3).replace("_", " "), synset)
    return tokens


def semcor_files(root: Path) -> dict[str, Path]:
    """Brown file name (br-a01) -> SemCor tagfile path."""
    return {p.name: p for p in root.glob("brown*/tagfiles/br-*")}


def sentence(tokens: dict[tuple[int, int], Token], snum: int) -> str:
    """The English text of one SemCor sentence."""
    words = [t.word for (s, _), t in sorted(tokens.items()) if s == snum]
    return " ".join(words)


def from_aligned(zip_path: Path, files: dict[str, Path], keys: dict[str, str],
                 found: dict[tuple[str, str], Candidate]) -> None:
    """Add candidates from the aligned JSemCor.

    Args:
        zip_path: semcor_jpn.zip, with semcor_jpn/br-*.xml.
        files: Brown file name -> SemCor tagfile.
        keys: Sense key index.
        found: Candidates by (synset, lemma), updated in place.
    """
    with zipfile.ZipFile(zip_path) as z:
        for name in sorted(n for n in z.namelist() if n.endswith(".xml")):
            brown = Path(name).stem
            tokens = read_semcor(files[brown], keys)
            root = ET.fromstring(z.read(name))
            words = {w.get("wid"): (w.get("sent"), w.text or "")
                     for w in root.iter("wf")}
            for term in root.iter("term"):
                refs = term.findall("externalReferences/externalRef")
                jwn = [r for r in refs if r.get("resource") == "WordNet jpn 1.1"]
                eng = [r.get("reference") for r in refs
                       if r.get("resource") == "Semcor eng 3.0"]
                if not jwn or not eng or any(r.findall("externalRef") for r in jwn):
                    continue
                for ref in eng:
                    _p, snum, idx = (int(x) for x in ref[1:].split("."))
                    token = tokens.get((snum, idx))
                    if token is None or token.synset is None:
                        continue
                    cand = found[(token.synset, term.get("lemma"))]
                    cand.jpos = jwn[0].get("reference").rsplit("-", 1)[1]
                    cand.aligned += 1
                    cand.english[token.word] += 1
                    if not cand.example[0]:
                        sent = words[term.find("span/target").get("id")][0]
                        ja = "".join(t for s, t in words.values() if s == sent)
                        cand.example = (sentence(tokens, snum), ja)


def mysql_rows(values: str):
    """Yield rows from the VALUES part of a MySQL INSERT statement."""
    row: list | None = None
    cell: list[str] = []
    quoted = False
    i = 0
    while i < len(values):
        c = values[i]
        if quoted:
            if c == "\\":
                cell.append({"n": "\n", "t": "\t"}.get(values[i + 1], values[i + 1]))
                i += 1
            elif c == "'":
                quoted = False
            else:
                cell.append(c)
        elif c == "'":
            quoted = True
        elif c == "(":
            row, cell = [], []
        elif c in ",)" and row is not None:
            text = "".join(cell)
            row.append(None if text == "NULL" else text)
            cell = []
            if c == ")":
                yield row
                row = None
        elif row is not None:
            cell.append(c)
        i += 1


def from_traduku(sql_gz: Path, files: dict[str, Path], keys: dict[str, str],
                 found: dict[tuple[str, str], Candidate]) -> None:
    """Add candidates from words translators chose that were not offered.

    The phrase table's file_id is matched to a Brown file through its
    sentences; sentence_id is the SemCor snum.

    Args:
        sql_gz: The traduku MySQL dump.
        files: Brown file name -> SemCor tagfile.
        keys: Sense key index.
        found: Candidates by (synset, lemma), updated in place.
    """
    tables: dict[str, list[list]] = defaultdict(list)
    insert = re.compile(r"INSERT INTO `(phrase|word|chosen)` VALUES (.*);\s*$")
    with gzip.open(sql_gz, "rt", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = insert.match(line)
            if m:
                tables[m.group(1)].extend(mysql_rows(m.group(2)))

    semcor = {name: read_semcor(path, keys) for name, path in files.items()}
    by_text = {}
    for name, tokens in semcor.items():
        for snum in {s for s, _ in tokens}:
            by_text.setdefault(sentence(tokens, snum).replace(" ", ""), (name, snum))
    phrases = {}
    file_of: dict[str, Counter] = defaultdict(Counter)
    for pid, fid, sid, _lang, text in tables["phrase"]:
        phrases[pid] = (fid, int(sid))
        hit = by_text.get(text.replace(" ", "").replace("_", ""))
        if hit and hit[1] == int(sid):
            file_of[fid][hit[0]] += 1
    brown = {fid: c.most_common(1)[0][0] for fid, c in file_of.items()}
    log.info("traduku: %d of %d files matched to SemCor",
             len(brown), len({f for f, _ in phrases.values()}))

    offered = defaultdict(set)
    for wid, pid, lang, text in tables["word"]:
        if lang == "2":
            offered[(pid, wid)].add(text)
    for pid, _user, lang, wid, text in tables["chosen"]:
        if lang != "2" or not text or not JAPANESE.search(text):
            continue
        if text in offered[(pid, wid)]:
            continue
        fid, snum = phrases[pid]
        if fid not in brown:
            continue
        token = semcor[brown[fid]].get((snum, int(wid)))
        if token is None or token.synset is None:
            continue
        cand = found[(token.synset, text)]
        cand.traduku += 1
        cand.english[token.word] += 1
        if not cand.example[0]:
            cand.example = (sentence(semcor[brown[fid]], snum), "")


def release_senses(path: Path) -> set[tuple[str, str]]:
    """(synset, lemma) pairs already in the release (satellites as -a)."""
    senses = set()
    lemma = ""
    for _ev, el in ET.iterparse(path, events=("start",)):
        if el.tag == "Lemma":
            lemma = el.get("writtenForm")
        elif el.tag == "Sense":
            senses.add((el.get("synset")[5:].replace("-s", "-a"), lemma))
    return senses


def english_synset(en: wn.Wordnet, synset: str) -> tuple[str, str]:
    """English lemmas and definition of a PWN 3.0 synset, from omw-en."""
    for pos in (synset[-1], "s"):
        try:
            ss = en.synset(f"omw-en-{synset[:-1]}{pos}")
        except wn.Error:
            continue
        return ", ".join(ss.lemmas()[:4]), ss.definition() or ""
    return "", ""


def main() -> None:
    """Collect the candidates and write the review file."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--aligned", type=Path, default=DEFAULT_ALIGNED)
    ap.add_argument("--semcor", type=Path, default=DEFAULT_SEMCOR)
    ap.add_argument("--traduku", type=Path, default=DEFAULT_TRADUKU)
    args = ap.parse_args()

    keys = load_sense_index(INDEX_SENSE)
    files = semcor_files(args.semcor)
    found: dict[tuple[str, str], Candidate] = defaultdict(Candidate)
    from_aligned(args.aligned, files, keys, found)
    log.info("aligned JSemCor: %d candidates", len(found))
    if args.traduku.exists():
        from_traduku(args.traduku, files, keys, found)
        log.info("with traduku: %d candidates", len(found))

    known = release_senses(RELEASE)
    en = wn.Wordnet("omw-en:2.0")
    rows = []
    for (synset, lemma), c in found.items():
        if (synset, lemma) in known:
            continue
        en_lemmas, en_def = english_synset(en, synset)
        source = "+".join(s for s, n in (("aligned", c.aligned),
                                        ("traduku", c.traduku)) if n)
        pos_match = "yes" if not c.jpos or c.jpos == synset[-1] else "no"
        rows.append((c.aligned + c.traduku, synset, lemma, c.jpos, pos_match,
                     c.aligned, c.traduku, source,
                     ", ".join(w for w, _ in c.english.most_common(3)),
                     en_lemmas, en_def, *c.example))
    rows.sort(key=lambda r: (-r[0], r[1], r[2]))
    with OUTPUT.open("w", encoding="utf-8") as out:
        out.write("synset\tlemma\tjpos\tpos_match\taligned\ttraduku\tsource\t"
                  "english_words\tenglish_lemmas\tdefinition\texample_en\t"
                  "example_ja\n")
        for r in rows:
            out.write("\t".join(" ".join(str(x).split()) for x in r[1:]) + "\n")
    log.info("%d candidates not in the release -> %s", len(rows), OUTPUT)


if __name__ == "__main__":
    main()
