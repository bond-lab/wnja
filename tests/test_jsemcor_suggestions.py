"""Unit tests for jsemcor_suggestions.py (issue #11)."""

import sqlite3
from collections import defaultdict

from jsemcor_suggestions import (
    Candidate,
    from_traduku,
    is_word,
    load_sense_index,
    read_semcor,
)

SEMCOR = """<contextfile concordance=brown>
<p pnum=72>
<s snum=90>
<punc>``</punc>
<wf cmd=ignore pos=EX>There</wf>
<wf cmd=done pos=VB lemma=be wnsn=2 lexsn=2:42:06::>was</wf>
<wf cmd=done pos=RB lemma=n't wnsn=0 lexsn=4:02:00::>n't</wf>
<wf cmd=done pos=RB lemma=a_bit wnsn=1 lexsn=4:02:00::>a_bit</wf>
<wf cmd=ignore pos=IN>of</wf>
<wf cmd=done pos=NN lemma=trouble wnsn=2 lexsn=1:11:01::>trouble</wf>
<punc>''</punc>
</s>
"""


def test_tokens_count_punctuation(tmp_path) -> None:
    """Richard's example: br-a01 s72.90.7 is 'trouble' (punc counts)."""
    path = tmp_path / "br-a01"
    path.write_text(SEMCOR, encoding="latin-1")
    keys = {"trouble%1:11:01::": "07289014-n", "be%2:42:06::": "02604760-v"}

    tokens = read_semcor(path, keys)

    assert tokens[(90, 7)].word == "trouble"
    assert tokens[(90, 7)].synset == "07289014-n"
    assert tokens[(90, 3)].synset == "02604760-v"
    assert tokens[(90, 2)].synset is None


def test_sense_index_normalises_satellites(tmp_path) -> None:
    """Sense keys map to synset ids; satellites (type 5) become -a."""
    index = tmp_path / "index.sense"
    index.write_text("recent%5:00:00:past:00 01730444 2 4\n"
                     "be%2:42:03:: 02604760 1 10742\n", encoding="utf-8")

    keys = load_sense_index(index)

    assert keys == {"recent%5:00:00:past:00": "01730444-a",
                    "be%2:42:03::": "02604760-v"}


def test_is_word_rejects_notes_and_fragments() -> None:
    """Gloss notes, English and punctuated fragments are not words."""
    assert is_word("知っている")
    assert is_word("ドライブ")
    assert not is_word("look up で、～の方に顔を上げる")
    assert not is_word("抜かした。")
    assert not is_word("不潔な、汚らわしい")
    assert not is_word("moon")


def test_traduku_suggestions_are_chosen_but_not_offered(tmp_path) -> None:
    """Only chosen Japanese words not among those offered are candidates."""
    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE file (file_id INTEGER, brown TEXT);
        CREATE TABLE phrase (id INTEGER, file_id INTEGER, sentence_id INTEGER,
                             lang_id INTEGER, text TEXT);
        CREATE TABLE word (id INTEGER, phrase_id INTEGER, lang_id INTEGER,
                           text TEXT);
        CREATE TABLE chosen (phrase_id INTEGER, translator TEXT,
                             lang_id INTEGER, word_id INTEGER, text TEXT);
        INSERT INTO file VALUES (1, 'br-a01');
        INSERT INTO phrase VALUES (10, 1, 90, 1, 'There was n''t trouble');
        INSERT INTO word VALUES (7, 10, 2, '面倒');
        INSERT INTO chosen VALUES (10, 't01', 2, 7, '面倒'),
                                  (10, 't02', 2, 7, '騒ぎ'),
                                  (10, 't03', 2, 7, 'trouble');
    """)
    (tmp_path / "br-a01").write_text(SEMCOR, encoding="latin-1")
    files = {"br-a01": tmp_path / "br-a01"}
    keys = {"trouble%1:11:01::": "07289014-n"}
    found = defaultdict(Candidate)

    from_traduku(db, files, keys, found)

    assert list(found) == [("07289014-n", "騒ぎ")]
    assert found[("07289014-n", "騒ぎ")].traduku == 1
