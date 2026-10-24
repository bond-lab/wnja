"""Tests for apply_sense_corrections in tweak_wnja.py."""

from pathlib import Path
from types import SimpleNamespace

from tweak_wnja import apply_sense_corrections

HEADER = "synset_id\tlemma\taction\tconfidence\treason\tsource\tdate\n"


def editor() -> SimpleNamespace:
    """A minimal stand-in for WordnetEditor: two entries, two synsets."""
    entries = [
        {"id": "e1", "lemma": {"writtenForm": "髪", "partOfSpeech": "n"},
         "senses": [{"id": "s1", "synset": "wnja-mane-n"},
                    {"id": "s2", "synset": "wnja-hair-n"}]},
        {"id": "e2", "lemma": {"writtenForm": "鬣", "partOfSpeech": "n"},
         "senses": [{"id": "s3", "synset": "wnja-mane-n",
                     "meta": {"confidenceScore": 0.71}}]},
    ]
    synsets = {"wnja-mane-n": {"members": ["s1", "s3"]},
               "wnja-hair-n": {"members": ["s2"]}}
    return SimpleNamespace(_lexicon={"entries": entries}, _synset_by_id=synsets)


def write(tmp_path: Path, rows: str) -> Path:
    """Write a senses.tsv with the given rows into tmp_path."""
    (tmp_path / "senses.tsv").write_text(HEADER + rows, encoding="utf-8")
    return tmp_path


def test_remove_drops_sense_and_member(tmp_path: Path) -> None:
    """Removing 髪 from mane leaves its hair sense and updates members."""
    ed = editor()
    d = write(tmp_path, "wnja-mane-n\t髪\tremove\t\tx\taudit\t2026-10-24\n")

    removed, lowered = apply_sense_corrections(ed, d)

    assert (removed, lowered) == (1, 0)
    assert [s["id"] for s in ed._lexicon["entries"][0]["senses"]] == ["s2"]
    assert ed._synset_by_id["wnja-mane-n"]["members"] == ["s3"]


def test_entry_without_senses_is_dropped(tmp_path: Path) -> None:
    """An entry whose only sense is removed disappears."""
    ed = editor()
    d = write(tmp_path, "wnja-mane-n\t鬣\tremove\t\tx\taudit\t2026-10-24\n")

    apply_sense_corrections(ed, d)

    assert [e["id"] for e in ed._lexicon["entries"]] == ["e1"]


def test_lower_only_lowers(tmp_path: Path) -> None:
    """lower sets confidence 0.5 but never raises an existing lower score."""
    ed = editor()
    d = write(tmp_path,
              "wnja-hair-n\t髪\tlower\t0.5\tx\taudit\t2026-10-24\n"
              "wnja-mane-n\t鬣\tlower\t0.8\tx\taudit\t2026-10-24\n")

    removed, lowered = apply_sense_corrections(ed, d)

    assert (removed, lowered) == (0, 1)
    assert ed._lexicon["entries"][0]["senses"][1]["meta"]["confidenceScore"] == 0.5
    assert ed._lexicon["entries"][1]["senses"][0]["meta"]["confidenceScore"] == 0.71


def kana_entry(eid: str, lemma: str, reading: str, synsets: list[str]) -> dict:
    """An entry with one hiragana reading and senses in the given synsets."""
    return {"id": eid, "lemma": {"writtenForm": lemma, "partOfSpeech": "n"},
            "forms": [{"writtenForm": reading, "script": "hira"}],
            "senses": [{"id": f"{s}-{eid}", "synset": s} for s in synsets]}


def rename_editor(entries: list[dict]) -> SimpleNamespace:
    """A stand-in WordnetEditor whose synsets list all the senses as members."""
    synsets: dict[str, dict] = {}
    for e in entries:
        for s in e["senses"]:
            synsets.setdefault(s["synset"], {"members": []})["members"].append(s["id"])
    return SimpleNamespace(_lexicon={"entries": entries}, _synset_by_id=synsets)


def test_rename_merges_with_same_reading_only(tmp_path: Path) -> None:
    # Arrange: ４月 (しがつ) joins 4月 (しがつ); 跡取リ has no 跡取り, so is renamed;
    # 物 with reading ぶつ must not join 物 read もの
    ed = rename_editor([
        kana_entry("e1", "４月", "しがつ", ["wnja-apr-n", "wnja-x-n"]),
        kana_entry("e2", "4月", "しがつ", ["wnja-apr-n"]),
        kana_entry("e3", "跡取リ", "あととり", ["wnja-heir-n"]),
        kana_entry("e4", "物", "もの", ["wnja-thing-n"]),
        kana_entry("e5", "ブツ", "ぶつ", ["wnja-thing-n"])])
    renames = [("wnja-apr-n", "４月", "4月"), ("wnja-x-n", "４月", "4月"),
               ("wnja-heir-n", "跡取リ", "跡取り"), ("wnja-thing-n", "ブツ", "物")]
    rows = "".join(f"{ss}\t{old}\trename\t\tx\tform\t2026-10-24\t{new}\n"
                   for ss, old, new in renames)
    head = HEADER.replace("date\n", "date\tnew_lemma\n")
    (tmp_path / "senses.tsv").write_text(head + rows, encoding="utf-8")

    # Act
    removed, lowered = apply_sense_corrections(ed, tmp_path)

    # Assert
    by = {e["id"]: e for e in ed._lexicon["entries"]}
    assert (removed, lowered) == (1, 0)
    assert "e1" not in by
    assert [s["synset"] for s in by["e2"]["senses"]] == ["wnja-apr-n", "wnja-x-n"]
    assert by["e3"]["lemma"]["writtenForm"] == "跡取り"
    assert [s["synset"] for s in by["e4"]["senses"]] == ["wnja-thing-n"]
    assert by["e5"]["lemma"]["writtenForm"] == "物"
    assert ed._synset_by_id["wnja-apr-n"]["members"] == ["wnja-apr-n-e2"]
