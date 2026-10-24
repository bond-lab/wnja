"""Unit tests for jsemcor_suggestions.py (issue #11)."""

from jsemcor_suggestions import load_sense_index, mysql_rows, read_semcor

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


def test_mysql_rows_handles_quotes_and_escapes() -> None:
    """Values with commas, escaped quotes and NULL parse correctly."""
    values = "(1,'mari',2,4,'暑い'),(2,'a\\'b',2,5,NULL),(3,'x, y',1,6,'')"

    rows = list(mysql_rows(values))

    assert rows == [["1", "mari", "2", "4", "暑い"],
                    ["2", "a'b", "2", "5", None],
                    ["3", "x, y", "1", "6", ""]]
