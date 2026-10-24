#!/usr/bin/env python3
"""Post-build tweaks for wnja-2.0.xml.

Applies manual corrections that cannot be derived from the NTU-MC export:
  1. Temperature antonyms: add direct antonym SynsetRelations between the
     Japan-specific temperature adjective synsets (暑い↔寒い, 熱い↔冷たい,
     暖かい↔涼しい, 温かい↔冷たい).
  2. Orphan synsets: add Japanese entries for JP-origin synsets that have
     definitions but no entries in the NTU-MC data.
  3. New synsets: add synsets that exist in NTU-MC with definitions but no
     Japanese entries, requiring both a synset and a new lexical entry.

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


# ---------------------------------------------------------------------------
# New synsets: NTU-MC synsets with definitions but no Japanese entries,
# requiring creation of both the synset and a new lexical entry.
# ---------------------------------------------------------------------------
# Format: (synset_id, pos, ili, definition, [(writtenForm, script_or_None), ...])
# 'script' values: None (kanji/mixed), 'kana' (katakana), 'hira' (hiragana)
# Convention: entries should include all three forms where applicable —
#   forms[0]: kanji/mixed canonical lemma  (script=None)
#   forms[1]: hiragana reading             (script='hira')
#   forms[2]: katakana pronunciation       (script='kana')
NEW_SYNSETS: list[tuple[str, str, str, str, list[tuple[str, str | None]]]] = [
    # wnja-06562436-n: libel (legal pleading) — written complaint explaining
    # the cause of action (defamation) and any relief sought by the plaintiff.
    # EN: "the written statement of a plaintiff explaining the cause of action
    #      (the defamation) and any relief he seeks" ⟪i70943⟫
    (
        "wnja-06562436-n", "n", "i70943",
        "名誉毀損（訴因）と原告が求める救済を説明した書面。",
        [("訴状", None), ("そじょう", "hira"), ("ソジョウ", "kana")],
    ),
]


def _add_entry_or_sense(
    editor: WordnetEditor, entry_id: str, synset_id: str, pos: str,
    forms: list[tuple[str, str | None]],
) -> str:
    """Add a sense of forms[0] to synset_id; return the entry id used.

    If the lemma already has an entry with this POS, the sense (and any
    new forms) go there, so the same (lemma, pos) is never two entries.
    """
    canonical_wf, canonical_script = forms[0]
    sense = make_sense(f"{synset_id}-tweak", synset_id)
    extra_forms = [
        {"writtenForm": wf, **({"script": sc} if sc else {})}
        for wf, sc in forms[1:]
    ]
    existing = next(
        (e for e in editor._lexicon.get("entries", [])
         if e["lemma"]["writtenForm"] == canonical_wf
         and e["lemma"]["partOfSpeech"] == pos),
        None,
    )
    if existing is not None:
        if all(s["synset"] != synset_id for s in existing["senses"]):
            existing["senses"].append(sense)
        have = {f["writtenForm"] for f in existing.get("forms", [])}
        existing.setdefault("forms", []).extend(
            f for f in extra_forms if f["writtenForm"] not in have)
        return existing["id"]
    lemma = make_lemma(canonical_wf, pos, script=canonical_script)
    entry = make_lexical_entry(entry_id, lemma, forms=extra_forms or None,
                               senses=[sense])
    editor._lexicon.setdefault("entries", []).append(entry)
    return entry_id


def apply_new_synsets(editor: WordnetEditor) -> int:
    """Create synsets that exist in NTU-MC but have no Japanese entries.

    Idempotent: skips any synset_id already present in the editor.
    Returns the number of synsets added.
    """
    added = 0
    for synset_id, pos, ili, definition, forms in NEW_SYNSETS:
        if synset_id in editor._synset_by_id:
            log.info("  new synset %s already exists, skipping", synset_id)
            continue
        editor.create_synset(
            pos=pos,
            definition=definition,
            ili=ili,
            synset_id=synset_id,
        )
        used = _add_entry_or_sense(editor, f"tweak-new-{synset_id}",
                                   synset_id, pos, forms)
        canonical_wf = forms[0][0]
        added += 1
        log.info("  added new synset %s (%s, entry %s) → %s", synset_id,
                 canonical_wf, used, definition[:60])
    return added


def apply_orphan_entries(editor: WordnetEditor) -> int:
    """Add Japanese entries for JP-origin orphan synsets."""
    added = 0
    existing_entries = len(editor._lexicon.get("entries", []))
    for synset_id, pos, forms in ORPHAN_ENTRIES:
        if synset_id not in editor._synset_by_id:
            log.warning("orphan synset %s not found, skipping", synset_id)
            continue
        used = _add_entry_or_sense(editor, f"tweak-{synset_id}",
                                   synset_id, pos, forms)
        canonical_wf = forms[0][0]
        added += 1
        log.info("  added %s → %s (entry %s)", canonical_wf, synset_id, used)
    return added


def _readings(entry: dict) -> frozenset[str]:
    """The hiragana readings of an entry."""
    forms = entry.get("forms", [])
    return frozenset(f["writtenForm"] for f in forms if f.get("script") == "hira")


def _rename_senses(
    entries: list[dict], moved: dict[tuple[str, str], list[dict]],
) -> int:
    """Give senses taken from their entries a new written form.

    The senses of (entry id, new lemma) go to an entry with that lemma and
    part of speech whose readings match (or where either has none), without
    duplicating a synset, and the target gains the old entry's other forms;
    failing that, the old entry is renamed in place if it has no senses
    left, or copied under the new lemma.  A form equal to the new lemma is
    dropped from the forms.  Returns the number of senses
    dropped as duplicates.
    """
    by_id = {e["id"]: e for e in entries}
    by_lemma: dict[tuple[str, str], list[dict]] = {}
    for e in entries:
        key = (e["lemma"]["writtenForm"], e["lemma"]["partOfSpeech"])
        by_lemma.setdefault(key, []).append(e)
    dropped = 0
    for (eid, new), senses in moved.items():
        old = by_id[eid]
        pos = old["lemma"]["partOfSpeech"]
        target = next((e for e in by_lemma.get((new, pos), [])
                       if not _readings(e) or not _readings(old)
                       or _readings(e) == _readings(old)), None)
        if target is None:
            forms = [f for f in old.get("forms", []) if f["writtenForm"] != new]
            if old["senses"]:
                target = {**old, "id": f"{eid}-r", "senses": [], "forms": forms}
                entries.append(target)
            else:
                target = old
                target["forms"] = forms
            target["lemma"] = {**old["lemma"], "writtenForm": new}
            by_lemma.setdefault((new, pos), []).append(target)
        else:
            known = {new} | {f["writtenForm"] for f in target.get("forms", [])}
            extra = [f for f in old.get("forms", []) if f["writtenForm"] not in known]
            if extra:
                target["forms"] = target.get("forms", []) + extra
        have = {se["synset"] for se in target["senses"]}
        for sense in senses:
            if sense["synset"] in have:
                dropped += 1
            else:
                target["senses"].append(sense)
                have.add(sense["synset"])
    return dropped


def apply_sense_corrections(
    editor: WordnetEditor, corrections_dir: Path,
) -> tuple[int, int]:
    """Apply sense corrections from corrections/senses.tsv.

    Each row names a sense by synset_id and lemma (written form).  Action
    ``remove`` deletes the sense (and the entry if it is left with none);
    ``lower`` sets its confidenceScore to the given value unless already
    lower; ``rename`` gives the sense the written form ``new_lemma`` (see
    _rename_senses).  Rows that match no sense are logged and skipped.

    Returns (removed, lowered).
    """
    tsv = corrections_dir / "senses.tsv"
    if not tsv.exists():
        log.info("No senses.tsv found in %s, skipping", corrections_dir)
        return 0, 0
    rows: dict[tuple[str, str], dict] = {}
    with tsv.open(encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            rows[(row["synset_id"], row["lemma"])] = row
    removed = lowered = 0
    seen: set[tuple[str, str]] = set()
    moved: dict[tuple[str, str], list[dict]] = {}  # (entry id, new lemma) -> senses
    entries = editor._lexicon.get("entries", [])
    for entry in entries:
        lemma = entry["lemma"]["writtenForm"]
        kept = []
        for sense in entry.get("senses", []):
            row = rows.get((sense["synset"], lemma))
            if row is None:
                kept.append(sense)
                continue
            seen.add((sense["synset"], lemma))
            if row["action"] == "remove":
                removed += 1
                continue
            if row["action"] == "rename":
                moved.setdefault((entry["id"], row["new_lemma"]), []).append(sense)
                continue
            new = float(row["confidence"])
            meta = sense.get("meta") or {}
            if float(meta.get("confidenceScore", 1.0)) > new:
                meta["confidenceScore"] = new
                sense["meta"] = meta
                lowered += 1
            kept.append(sense)
        entry["senses"] = kept
    removed += _rename_senses(entries, moved)
    editor._lexicon["entries"] = [e for e in entries if e.get("senses")]
    # keep synset member lists in step with the removed senses
    live = {s["id"] for e in editor._lexicon["entries"] for s in e["senses"]}
    for ss in editor._synset_by_id.values():
        if ss.get("members"):
            ss["members"] = [m for m in ss["members"] if m in live]
    for key in rows.keys() - seen:
        log.warning("senses.tsv: no sense %s %s, skipping", *key)
    return removed, lowered


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

    log.info("Applying sense corrections …")
    removed, lowered = apply_sense_corrections(editor, CORRECTIONS_DIR)
    log.info("  %d senses removed, %d lowered", removed, lowered)

    log.info("Applying temperature antonyms …")
    n = apply_temperature_antonyms(editor)
    log.info("  %d antonym relations added", n)

    log.info("Adding new synsets …")
    n = apply_new_synsets(editor)
    log.info("  %d new synsets added", n)

    log.info("Adding entries for orphan synsets …")
    n2 = apply_orphan_entries(editor)
    log.info("  %d orphan entries added", n2)

    editor.export(OUTPUT)
    log.info("Written to %s", OUTPUT)


if __name__ == "__main__":
    main()
