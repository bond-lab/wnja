#!/usr/bin/env python3
"""Post-build tweaks for wnja-2.0.xml.

Applies manual corrections that cannot be derived from the NTU-MC export:
  1. Temperature antonyms: add direct antonym SynsetRelations between the
     Japan-specific temperature adjective synsets (暑い↔寒い, 熱い↔冷たい,
     暖かい↔涼しい, 温かい↔冷たい).
  2. Orphan synsets: add Japanese entries for JP-origin synsets that have
     definitions but no entries in the NTU-MC data.

Run after build_wnja.py:
  uv run python build_wnja.py
  uv run python tweak_wnja.py
"""

import csv
import logging
import sys
from pathlib import Path

from wn_edit import WordnetEditor, make_lemma, make_sense, make_lexical_entry

CORRECTIONS_DIR = Path("corrections")
LOG_FILE = Path("wnja.log")
OUTPUT = Path("wnja-2.0.xml")

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8", mode="a"),
        logging.StreamHandler(sys.stderr),
    ],
    force=True,
)
log = logging.getLogger("tweak")

# ---------------------------------------------------------------------------
# Antonym pairs to add (bidirectional)
# ---------------------------------------------------------------------------
# Each tuple (A, B) adds A→antonym→B and B→antonym→A if not already present.
TEMP_ANTONYMS: list[tuple[str, str]] = [
    # atmospheric: 暑い (hot air) ↔ 寒い (cold air)
    ("wnja-80002364-a", "wnja-80002366-a"),
    # physical object: 熱い (hot to touch) ↔ 冷たい (cold to touch)
    ("wnja-80002365-a", "wnja-80002367-a"),
    # pleasant outdoor: 暖かい (warm) ↔ 涼しい (cool)
    ("wnja-80002368-a", "wnja-80002381-a"),
    # warm food/body: 温かい (warm) ↔ 冷たい (cold to touch)
    ("wnja-80002369-a", "wnja-80002367-a"),
]


def _existing_antonyms(editor: WordnetEditor, synset_id: str) -> set[str]:
    synset = editor._synset_by_id.get(synset_id)
    if synset is None:
        return set()
    return {
        r["target"]
        for r in synset.get("relations", [])
        if r.get("relType") == "antonym"
    }


def apply_temperature_antonyms(editor: WordnetEditor) -> int:
    """Add bidirectional antonym relations between JP temperature synsets."""
    added = 0
    for a, b in TEMP_ANTONYMS:
        for src, tgt in ((a, b), (b, a)):
            if src not in editor._synset_by_id:
                log.warning("synset %s not found, skipping", src)
                continue
            if tgt in _existing_antonyms(editor, src):
                continue
            editor.add_synset_relation(src, tgt, "antonym", validate=False)
            added += 1
            log.info("  added antonym: %s → %s", src, tgt)
    return added


# ---------------------------------------------------------------------------
# Orphan synsets: JP-specific synsets with definitions but no NTU-MC entries
# ---------------------------------------------------------------------------
# Format: (synset_id, pos, [(writtenForm, script_or_None), ...])
# 'script' values: None (kanji/mixed), 'kana' (katakana), 'hira' (hiragana)
ORPHAN_ENTRIES: list[tuple[str, str, list[tuple[str, str | None]]]] = [
    # wnja-80001271-n: honorific prefix (敬意を表するために…名称の前に置かれる敬称)
    ("wnja-80001271-n", "n", [("お", "hira"), ("御", None), ("ご", "hira")]),
    # wnja-80002384-a: tandoori (cooked in an Indian clay oven)
    ("wnja-80002384-a", "a", [("タンドーリ", "kana"), ("タンドール", "kana")]),
    # wnja-80002385-v: to pre-fry / sauté first before final cooking
    ("wnja-80002385-v", "v", [("下炒めする", None), ("炒め通す", None)]),
]


def apply_corrections(editor: WordnetEditor, corrections_dir: Path) -> int:
    """Apply manual definition corrections from corrections/definitions.tsv.

    Skips (with a warning) any row whose old_value does not match the current
    definition — this catches stale corrections after upstream data changes.

    Returns the number of corrections applied.
    """
    tsv = corrections_dir / "definitions.tsv"
    if not tsv.exists():
        log.info("No definitions.tsv found in %s, skipping corrections", corrections_dir)
        return 0

    applied = 0
    with tsv.open(encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            ss_id = row["synset_id"]
            synset = editor._synset_by_id.get(ss_id)
            if synset is None:
                log.warning("corrections: synset %s not found, skipping", ss_id)
                continue
            defs = synset.get("definitions") or []
            if not row["old_value"]:
                # Addition: add a new definition when none exists
                if defs:
                    log.warning("corrections: %s already has a definition, skipping addition", ss_id)
                    continue
                synset.setdefault("definitions", []).append({"meta": None, "text": row["new_value"]})
                applied += 1
                log.info("  added def %s: %s", ss_id, row["new_value"][:60])
                continue
            if not defs:
                log.warning("corrections: %s has no definitions, skipping", ss_id)
                continue
            current = defs[0]["text"]
            if current != row["old_value"]:
                log.warning(
                    "corrections: %s old_value mismatch — correction may be stale\n"
                    "  expected: %s\n"
                    "  current:  %s",
                    ss_id, row["old_value"], current,
                )
                continue
            defs[0]["text"] = row["new_value"]
            applied += 1
            log.info("  corrected %s: %s → %s", ss_id, row["old_value"][:60], row["new_value"][:60])
    return applied


def apply_orphan_entries(editor: WordnetEditor) -> int:
    """Add Japanese entries for JP-origin orphan synsets."""
    added = 0
    existing_entries = len(editor._lexicon.get("entries", []))
    for synset_id, pos, forms in ORPHAN_ENTRIES:
        if synset_id not in editor._synset_by_id:
            log.warning("orphan synset %s not found, skipping", synset_id)
            continue
        canonical_wf, canonical_script = forms[0]
        lemma = make_lemma(canonical_wf, pos, script=canonical_script)
        sense_id = f"{synset_id}-tweak"
        sense = make_sense(sense_id, synset_id)
        extra_forms = [
            {"writtenForm": wf, **({"script": sc} if sc else {})}
            for wf, sc in forms[1:]
        ] if len(forms) > 1 else None
        entry_id = f"tweak-{synset_id}"
        entry = make_lexical_entry(entry_id, lemma, forms=extra_forms, senses=[sense])
        editor._lexicon.setdefault("entries", []).append(entry)
        added += 1
        log.info("  added entry %s → %s", canonical_wf, synset_id)
    return added


def main() -> None:
    if not OUTPUT.exists():
        log.error("%s not found — run build_wnja.py first", OUTPUT)
        sys.exit(1)

    log.info("Loading %s …", OUTPUT)
    editor = WordnetEditor.load_from_file(OUTPUT)
    log.info("  %d synsets loaded", len(editor._synset_by_id))

    log.info("Applying definition corrections …")
    n = apply_corrections(editor, CORRECTIONS_DIR)
    log.info("  %d corrections applied", n)

    log.info("Applying temperature antonyms …")
    n = apply_temperature_antonyms(editor)
    log.info("  %d antonym relations added", n)

    log.info("Adding entries for orphan synsets …")
    n2 = apply_orphan_entries(editor)
    log.info("  %d orphan entries added", n2)

    editor.export(OUTPUT)
    log.info("Written to %s", OUTPUT)


if __name__ == "__main__":
    main()
