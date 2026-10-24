#!/usr/bin/env python3
"""Count sense frequencies in the Japanese SemCor (JSemCor).

JSemCor (Bond et al. 2012, https://github.com/bond-lab/jsemcor) is
sense-tagged with Japanese WordNet 1.1.  Each tagged term looks like:

    <term lemma="金曜日" ...>
      <externalReferences>
        <externalRef reference="jpn-11-金曜日-n">
          <externalRef reference="jpn-11-15164463-n"/>

Writes data/derived/wn-freq-jpn-jsemcor.tsv (synset, lemma, freq), the same
format as the NTU-MC frequency files.  A term tagged with two synsets
counts once for each.

Usage:
    uv run python -m scripts.count_jsemcor [JSEMCOR_DATA_DIR]
"""

import logging
import sys
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

from build_wnja import JSEMCOR_FILE as FREQ_FILE

DEFAULT_DIR = Path("../jsemcor/data")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s",
                    stream=sys.stderr)
log = logging.getLogger("jsemcor")


def synset_of(reference: str) -> str:
    """Synset number from a JSemCor reference, e.g. jpn-11-15164463-n.

    Satellites (-s) are normalised to -a, as in the wnja build.
    """
    num = reference.removeprefix("jpn-11-")
    return num[:-1] + "a" if num.endswith("-s") else num


def count_file(path: Path) -> Counter:
    """Count (synset, lemma) pairs in one JSemCor KAF file.

    Args:
        path: A KAF XML file.

    Returns:
        Counter of (synset, lemma) pairs.
    """
    counts: Counter = Counter()
    for term in ET.parse(path).getroot().iter("term"):
        lemma = term.get("lemma")
        for outer in term.iterfind("externalReferences/externalRef"):
            for inner in outer.findall("externalRef"):
                counts[(synset_of(inner.get("reference")), lemma)] += 1
    return counts


def main() -> None:
    """Count all JSemCor files and write the frequency file."""
    data_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DIR
    files = sorted(data_dir.glob("*.xml"))
    if not files:
        log.error("no JSemCor files in %s", data_dir)
        sys.exit(1)
    counts: Counter = Counter()
    for path in files:
        counts.update(count_file(path))
    with FREQ_FILE.open("w", encoding="utf-8") as out:
        out.write(f"# Frequency for jpn from JSemCor ({len(files)} files)\n")
        out.write("synset\tlemma\tfreq\n")
        for (synset, lemma), n in sorted(counts.items()):
            out.write(f"{synset}\t{lemma}\t{n}\n")
    log.info("%d tags, %d (synset, lemma) pairs -> %s",
             sum(counts.values()), len(counts), FREQ_FILE)


if __name__ == "__main__":
    main()
