#!/usr/bin/env python3
"""Package wnja-VERSION.xml for the wn module.

Writes release/wnja/wnja-VERSION.xml with README.md, LICENSE,
CONTRIBUTORS.md and citation.bib, and release/wnja-VERSION.tar.xz.

Usage:
    uv run python package_wnja.py [--version 2.0]
"""

import argparse
import shutil
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> None:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", default="2.0")
    args = parser.parse_args()

    xml = ROOT / f"wnja-{args.version}.xml"
    outdir = ROOT / "release"
    pkg = outdir / "wnja"
    pkg.mkdir(parents=True, exist_ok=True)
    shutil.copy(xml, pkg / xml.name)
    for name in ("README.md", "LICENSE", "CONTRIBUTORS.md", "citation.bib"):
        shutil.copy(ROOT / name, pkg / name)
    tar = outdir / f"wnja-{args.version}.tar.xz"
    with tarfile.open(tar, "w:xz") as tf:
        tf.add(pkg, arcname="wnja")
    print(f"  wrote {tar}")


if __name__ == "__main__":
    main()
