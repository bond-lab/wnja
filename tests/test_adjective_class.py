"""Tests for Kuroda's adjective classes (issue #9)."""

from build_wnja import tag_adjective_classes
from kuroda_adjectives import classify


def adj(lemma: str, pos: str = "a") -> dict:
    """A minimal LexicalEntry dict."""
    return {"lemma": {"writtenForm": lemma, "partOfSpeech": pos}}


def test_classify_main_classes() -> None:
    """The four main endings and たる map to their classes."""
    assert classify("ルーズ+な") == ("ルーズ", "na", "")
    assert classify("本当+の") == ("本当", "no", "")
    assert classify("多才+な,の") == ("多才", "na-no", "")
    assert classify("真+の,な") == ("真", "no-na", "")
    assert classify("漠+たる") == ("漠", "other", "")


def test_classify_sends_hand_marks_to_review() -> None:
    """Errors, corrections and uncertain endings are not used."""
    assert classify("*先ず")[1:] == (None, "marked as error or comment")
    assert classify("鈍ら+の,な<=なまくら+の,な")[1:] == (None, "lemma correction")
    assert classify("ほげ+?の,?な")[1:] == (None, "uncertain ending")
    assert classify("大+")[1:] == (None, "no ending: bound form")


def test_tags_adjectives_only() -> None:
    """Only adjective entries are tagged; stems with an ending match."""
    entries = [adj("静か"), adj("静か", "n"), adj("古代の"), adj("漠たる"),
               adj("赤い")]
    classes = {"静か": "na", "古代": "no", "漠": "other"}

    n = tag_adjective_classes(entries, classes)

    assert n == 3
    assert [e["lemma"].get("tags") for e in entries] == [
        [{"category": "adjective-class", "text": "na"}], None,
        [{"category": "adjective-class", "text": "no"}],
        [{"category": "adjective-class", "text": "other"}], None]
