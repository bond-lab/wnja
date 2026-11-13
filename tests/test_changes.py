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
