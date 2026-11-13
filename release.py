#!/usr/bin/env python3
"""Make the next wnja release: the last release plus the change records.

    uv run python release.py VERSION     # e.g. 2.2 -> build/wnja-2.2.xml
    uv run python package_wnja.py --version VERSION

The base is the released wnja-BASE_VERSION.xml, downloaded once to
build/base/ and checked against its checksum.  The changes are
changes/wnja-BASE_VERSION.jsonl (see changes/README.md).  A change that
does not fit the base is a conflict: they are listed, and nothing is
written until they are settled.

Uses ntumc.wordnet from a checkout of NTU-MC beside this one (../NTUMC).
"""

import argparse
import hashlib
import io
import logging
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "NTUMC"))

from ntumc.wordnet import changes, lmf  # noqa: E402
from ntumc.wordnet.model import Lexicon  # noqa: E402

BASE_VERSION = "2.1"
BASE_URL = ("https://github.com/bond-lab/wnja/releases/download/"
            f"v{BASE_VERSION}/wnja-{BASE_VERSION}.tar.xz")
BASE_XZ_SHA256 = "e6f141db243a78c729f5ef29a0dd2bdf434087dae170ad49b01034f19d9ff584"
BASE_XML_SHA256 = "0e08760cd3fd82ac583b846d210a7db25282378af28e8d6c1612aaac51139919"
BASE = ROOT / "build" / "base" / f"wnja-{BASE_VERSION}.xml"
CHANGES = ROOT / "changes" / f"wnja-{BASE_VERSION}.jsonl"

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


def release(version: str, out: Path) -> list[str]:
    """Apply the change records to the base and write the new release.

    Args:
        version: the version of the new release
        out: the XML file to write; not written if there are conflicts

    Returns:
        The conflicts, one line each.
    """
    fetch_base()
    items, conflicts = changes.apply(lmf.read(BASE), changes.read(CHANGES))
    if conflicts:
        return conflicts
    next(i for i in items if isinstance(i, Lexicon)).version = version
    lmf.write(out, items)
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
