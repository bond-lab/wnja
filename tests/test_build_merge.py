"""Unit tests for entry merging in build_wnja.py."""

from build_wnja import _merge_by_lemma_pos, reading


def entry(lemma: str, forms: list[str], synsets: list[str],
          yomi: str | None = None) -> dict:
    """A minimal LexicalEntry dict, as build_entries makes them."""
    e = {
        "id": f"{lemma}-{yomi}",
        "lemma": {"writtenForm": lemma, "partOfSpeech": "v"},
        "forms": [{"writtenForm": f} for f in forms],
        "senses": [{"id": f"{s}-{yomi}", "synset": s} for s in synsets],
    }
    if yomi is not None:
        e["_reading"] = yomi
    return e


def test_reading_is_the_kana_form() -> None:
    """The reading is the katakana form; none if there is no kana form."""
    assert reading([("反る", ""), ("ソル", "kana"), ("そる", "hira")]) == "ソル"
    assert reading([("反る", "")]) is None


def test_homographs_stay_separate() -> None:
    """Different readings of one spelling are different words."""
    soru = entry("反る", ["ソル", "そる"], ["wnja-00140188-v"], yomi="ソル")
    kaeru = entry("反る", ["カエル", "かえる"], ["wnja-00387310-v"], yomi="カエル")

    merged = _merge_by_lemma_pos([soru, kaeru])

    assert [[f["writtenForm"] for f in e["forms"]] for e in merged] == [
        ["ソル", "そる"], ["カエル", "かえる"]]


def test_same_reading_merges() -> None:
    """Two hno with the same reading are one word (宿借り 1 and 2)."""
    one = entry("宿借り", ["ヤドカリ"], ["wnja-01976957-n"], yomi="ヤドカリ")
    two = entry("宿借り", ["ヤドカリ"], ["wnja-10678937-n"], yomi="ヤドカリ")

    merged = _merge_by_lemma_pos([one, two])

    assert len(merged) == 1
    assert len(merged[0]["senses"]) == 2


def test_entry_without_reading_joins_first() -> None:
    """An entry with no known reading joins the first entry for its lemma."""
    kaeru = entry("反る", ["カエル"], ["wnja-00387310-v"], yomi="カエル")
    soru = entry("反る", ["ソル"], ["wnja-00140188-v"], yomi="ソル")
    extra = entry("反る", [], ["wnja-80000001-v"])

    merged = _merge_by_lemma_pos([kaeru, soru, extra])

    synsets = [[s["synset"] for s in e["senses"]] for e in merged]
    assert synsets == [["wnja-00387310-v", "wnja-80000001-v"],
                       ["wnja-00140188-v"]]
