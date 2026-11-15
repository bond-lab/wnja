#!/usr/bin/env python3
"""Make the next wnja release: the last release plus the change records.

    uv run python release.py VERSION     # e.g. 2.2 -> build/wnja-2.2.xml
    uv run python package_wnja.py --version VERSION

The base is the released wnja-BASE_VERSION.xml, downloaded once to
build/base/ and checked against its checksum.  The changes are
changes/wnja-BASE_VERSION.jsonl (see changes/README.md).  A change that
does not fit the base is a conflict: they are listed, and nothing is
written until they are settled.

Adjective entries that have a class in data/derived/adjective_class.tsv
(Kuroda et al. 2011) but no adjective-class tag are listed, with the
set_tag record that would tag them: an entry made by add_sense has no
tags until a record gives it one.

Two lexicons are written: ID-min-VERSION.xml, the minimal lexicon (the
master), which leaves the English definition and the relations of the
concepts CILI does not have yet to the shared lexicon omw-ntumc; and
ID-VERSION.xml, the export, with those put back.  omw-ntumc is read
from a checkout beside this one, built at its tag
(uv run build.py SHARED_VERSION).

Uses ntumc.wordnet from a checkout of NTU-MC beside this one (../NTUMC).
"""

import argparse
import csv
import hashlib
import io
import logging
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "NTUMC"))

from ntumc.wordnet import changes, lmf, shared  # noqa: E402
from ntumc.wordnet.model import Entry, Item, Lexicon  # noqa: E402

BASE_VERSION = "2.1"
BASE_URL = ("https://github.com/bond-lab/wnja/releases/download/"
            f"v{BASE_VERSION}/wnja-{BASE_VERSION}.tar.xz")
BASE_XZ_SHA256 = "e6f141db243a78c729f5ef29a0dd2bdf434087dae170ad49b01034f19d9ff584"
BASE_XML_SHA256 = "0e08760cd3fd82ac583b846d210a7db25282378af28e8d6c1612aaac51139919"
BASE = ROOT / "build" / "base" / f"wnja-{BASE_VERSION}.xml"
CHANGES = ROOT / "changes" / f"wnja-{BASE_VERSION}.jsonl"
SHARED_VERSION = "0.1"
SHARED = ROOT.parent / "omw-ntumc" / "build" / f"omw-ntumc-{SHARED_VERSION}.xml"
SHARED_SHA256 = "6ee5f6a254907d4fbdaa0e4238dddf0c3012ee92ef27fdd7d79815c46ff7c3f0"
ADJ_CLASS_FILE = ROOT / "data" / "derived" / "adjective_class.tsv"
ADJ_CLASS = "adjective-class"

log = logging.getLogger("release")


def fetch_base(path: Path = BASE) -> None:
    """Download the base release to path, unless it is there; check it.

    Raises:
        ValueError: if the download or the file does not match its checksum.
    """
    if not path.exists():
        log.info("Downloading %s", BASE_URL)
        with urllib.request.urlopen(BASE_URL) as resp:
            data = resp.read()
        if hashlib.sha256(data).hexdigest() != BASE_XZ_SHA256:
            raise ValueError(f"{BASE_URL}: checksum mismatch")
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:xz") as tf:
            member = tf.extractfile(f"wnja/wnja-{BASE_VERSION}.xml")
            if member is None:
                raise ValueError(f"{BASE_URL}: no wnja-{BASE_VERSION}.xml inside")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(member.read())
    if hashlib.sha256(path.read_bytes()).hexdigest() != BASE_XML_SHA256:
        raise ValueError(f"{path} is not the released wnja {BASE_VERSION}")


def adjective_class(lemma: str, classes: dict[str, str]) -> str | None:
    """The class of an adjective lemma; one written with its ending takes its stem's.

    Args:
        lemma: the written form, e.g. 古代の
        classes: {lemma: class} from data/derived/adjective_class.tsv

    Returns:
        na, no, na-no, no-na or other; None if the lemma is not classified
    """
    stems = [lemma]
    if lemma[-1:] in ("な", "の"):
        stems.append(lemma[:-1])
    if lemma[-2:] in ("たる", "なる"):
        stems.append(lemma[:-2])
    return next((classes[s] for s in stems if s in classes), None)


def untagged_adjectives(
    items: list[Item], path: Path = ADJ_CLASS_FILE
) -> list[tuple[str, str]]:
    """Adjective entries with a class in Kuroda's data but no class tag.

    Args:
        items: the lexicon's items
        path: lemma and class, tab-separated, with a header

    Returns:
        (lemma, class) for each such entry
    """
    with path.open(encoding="utf-8") as f:
        classes = {r["lemma"]: r["class"] for r in csv.DictReader(f, delimiter="\t")}
    return [(str(e.lemma), cls) for e in items
            if isinstance(e, Entry) and e.pos == "a"
            and not any(t.category == ADJ_CLASS for t in e.tags)
            and (cls := adjective_class(str(e.lemma), classes))]


def release(version: str, out: Path) -> list[str]:
    """Apply the change records to the base and write the new release.

    Args:
        version: the version of the new release
        out: the export's XML file; the minimal lexicon goes beside it.
            Neither is written if there are conflicts

    Returns:
        The conflicts, one line each.

    Raises:
        ValueError: if the export lacks something the lexicon said before
            it was split.
    """
    fetch_base()
    items, conflicts = changes.apply(lmf.read(BASE), changes.read(CHANGES))
    if conflicts:
        return conflicts
    if changes.shared_ilis(items):
        return changes.shared_ilis(items)
    for lemma, cls in untagged_adjectives(items):
        log.warning('%s (a) has no %s tag; Kuroda has %s: {"action": "set_tag", '
                    '"lemma": "%s", "pos": "a", "category": "%s", "new": "%s"}',
                    lemma, ADJ_CLASS, cls, lemma, ADJ_CLASS, cls)
    next(i for i in items if isinstance(i, Lexicon)).version = version
    shared.write_release(items, shared.load(SHARED, SHARED_SHA256), out)
    return []


def main() -> None:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version", help="version of the new release, e.g. 2.2")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    out = ROOT / "build" / f"wnja-{args.version}.xml"
    conflicts = release(args.version, out)
    if conflicts:
        for line in conflicts:
            log.error(line)
        sys.exit(1)
    log.info("Wrote %s", out)


if __name__ == "__main__":
    main()
