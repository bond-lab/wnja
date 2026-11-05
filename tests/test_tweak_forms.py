"""Tests for apply_form_corrections in tweak_wnja.py."""

from pathlib import Path
from types import SimpleNamespace

from tweak_wnja import apply_form_corrections

HEADER = "lemma\tpos\treading\tform\tscript\treason\tsource\tdate\n"


def editor() -> SimpleNamespace:
    """A minimal stand-in for WordnetEditor: two homographs of 散々-like words."""
    entries = [
        {"id": "e1", "lemma": {"writtenForm": "散り散り", "partOfSpeech": "n"},
         "forms": [{"writtenForm": "ちりぢり", "script": "hira"}], "senses": []},
        {"id": "e2", "lemma": {"writtenForm": "散り散り", "partOfSpeech": "a"},
         "forms": [{"writtenForm": "ちりぢり", "script": "hira"}], "senses": []},
    ]
    return SimpleNamespace(_lexicon={"entries": entries}, _synset_by_id={})


def test_adds_form_to_matching_entry_once(tmp_path: Path) -> None:
    """The form goes on the noun only, and a second identical row adds nothing."""
    ed = editor()
    row = "散り散り\tn\tちりぢり\t散々\t\tx\tchecker\t2026-11-05\n"
    (tmp_path / "forms.tsv").write_text(HEADER + row + row, encoding="utf-8")

    added = apply_form_corrections(ed, tmp_path)

    noun, adj = ed._lexicon["entries"]
    assert added == 1
    assert noun["forms"][-1] == {"writtenForm": "散々"}
    assert len(adj["forms"]) == 1


def test_wrong_reading_matches_nothing(tmp_path: Path) -> None:
    """A row whose reading the entry lacks is skipped."""
    ed = editor()
    row = "散り散り\tn\tさんざん\t散々\t\tx\tchecker\t2026-11-05\n"
    (tmp_path / "forms.tsv").write_text(HEADER + row, encoding="utf-8")

    assert apply_form_corrections(ed, tmp_path) == 0
