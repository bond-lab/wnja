"""The change records must be well formed and fit the base."""

from collections import Counter

import pytest

release = pytest.importorskip("release", reason="needs ../NTUMC (ntumc.wordnet)")

from ntumc.wordnet import changes, lmf  # noqa: E402


def test_every_record_is_well_formed() -> None:
    # Act
    records = list(changes.read(release.CHANGES))
    problems = [(r.id, p) for r in records for p in changes.validate(r)]
    ids = Counter(r.id for r in records)
    # Assert
    assert problems == []
    assert [i for i, n in ids.items() if n > 1] == []
    assert {r.lexicon for r in records} == {"wnja"}


def test_the_records_apply_to_the_base_without_conflict() -> None:
    # Arrange
    if not release.BASE.exists():
        pytest.skip("base release not downloaded: run release.py once")
    # Act
    _, conflicts = changes.apply(lmf.read(release.BASE), changes.read(release.CHANGES))
    # Assert
    assert conflicts == []


def test_no_ili_is_on_two_synsets() -> None:
    # Arrange
    if not release.BASE.exists():
        pytest.skip("base release not downloaded: run release.py once")
    # Act
    items, _ = changes.apply(lmf.read(release.BASE), changes.read(release.CHANGES))
    # Assert
    assert changes.shared_ilis(items) == []


def test_adjective_class_falls_back_to_the_stem() -> None:
    # Arrange
    classes = {"古代": "no", "堂々": "other"}
    # Act / Assert
    assert release.adjective_class("古代の", classes) == "no"
    assert release.adjective_class("堂々たる", classes) == "other"
    assert release.adjective_class("赤い", classes) is None


def test_untagged_adjectives_lists_only_the_classified(tmp_path) -> None:
    # Arrange
    from ntumc.wordnet.model import Entry, Tag
    path = tmp_path / "classes.tsv"
    path.write_text("lemma\tclass\n静か\tna\n古代\tno\n", encoding="utf-8")
    items = [Entry(lemma="静か", pos="a"),
             Entry(lemma="古代の", pos="a",
                   tags=[Tag(text="no", category="adjective-class")]),
             Entry(lemma="古代", pos="n"),
             Entry(lemma="赤い", pos="a")]
    # Act
    found = release.untagged_adjectives(items, path)
    # Assert
    assert found == [("静か", "na")]
