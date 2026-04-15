"""Tests for the wnja build pipeline and output XML.

Checks that key entries, forms, synsets and attributes are present
in the built wnja-2.0.xml output.
"""

import pytest
from pathlib import Path
from xml.etree import ElementTree as ET

OUTPUT = Path(__file__).parent.parent / "wnja-2.0.xml"


@pytest.fixture(scope="module")
def tree():
    if not OUTPUT.exists():
        pytest.skip(f"{OUTPUT} not found — run build_wnja.py first")
    return ET.parse(OUTPUT)


@pytest.fixture(scope="module")
def lexicon(tree):
    root = tree.getroot()
    lex = root.find("Lexicon")
    assert lex is not None
    return lex


@pytest.fixture(scope="module")
def entries_by_lemma(lexicon):
    """Index: {(writtenForm, partOfSpeech): [LexicalEntry elem, ...]}"""
    from collections import defaultdict
    idx = defaultdict(list)
    for entry in lexicon.findall("LexicalEntry"):
        lemma = entry.find("Lemma")
        if lemma is not None:
            key = (lemma.get("writtenForm"), lemma.get("partOfSpeech"))
            idx[key].append(entry)
    return dict(idx)


@pytest.fixture(scope="module")
def synsets_by_id(lexicon):
    return {ss.get("id"): ss for ss in lexicon.findall("Synset")}


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def test_lexicon_metadata(lexicon):
    assert lexicon.get("id") == "wnja"
    assert lexicon.get("language") == "ja"
    assert lexicon.get("version") == "2.0"
    assert lexicon.get("license") == "wordnet"


# ---------------------------------------------------------------------------
# Orthographic variants: 中 (naka / uchi / chuu)
# ---------------------------------------------------------------------------

def test_naka_entry_has_kana_and_hira(entries_by_lemma):
    """中 (読み: ナカ) should have Form ナカ and なか."""
    naka_entries = entries_by_lemma.get(("中", "n"), [])
    # Find the ナカ reading
    naka = None
    for e in naka_entries:
        forms = {f.get("writtenForm") for f in e.findall("Form")}
        if "ナカ" in forms:
            naka = e
            break
    assert naka is not None, "No 中(ナカ) entry found"
    forms = {f.get("writtenForm") for f in naka.findall("Form")}
    assert "なか" in forms, "Hiragana なか missing from 中(ナカ)"


def test_no_latin_forms(lexicon):
    """No Form or Lemma should carry script='latn' or 'latn-hepburn'."""
    for elem in lexicon.iter():
        script = elem.get("script", "")
        assert "latn" not in script, f"Latin script found: {ET.tostring(elem, encoding='unicode')}"


# ---------------------------------------------------------------------------
# Sahen (suru-verb) marking
# ---------------------------------------------------------------------------

def test_sahen_verb_has_note(entries_by_lemma):
    """呼吸 (v) is a suru-verb and should be marked note="sahen"."""
    kokyuu = entries_by_lemma.get(("呼吸", "v"), [])
    assert kokyuu, "呼吸(v) entry not found"
    notes = [e.get("note", "") for e in kokyuu]
    assert any(n == "sahen" for n in notes), f"呼吸(v) not marked sahen; notes={notes}"


def test_godan_verb_not_sahen(entries_by_lemma):
    """歩く (v) is a regular godan verb and must NOT be marked sahen."""
    aruku = entries_by_lemma.get(("歩く", "v"), [])
    if not aruku:
        pytest.skip("歩く(v) not in output")
    for e in aruku:
        assert e.get("note", "") != "sahen", "歩く(v) incorrectly marked sahen"


# ---------------------------------------------------------------------------
# +suru stripping
# ---------------------------------------------------------------------------

def test_no_suru_suffix_in_lemmas(lexicon):
    """No Lemma writtenForm should contain +する or +suru."""
    for entry in lexicon.findall("LexicalEntry"):
        lemma = entry.find("Lemma")
        if lemma is not None:
            wf = lemma.get("writtenForm", "")
            assert "+する" not in wf and "+suru" not in wf, \
                f"+suru not stripped from lemma: {wf}"


# ---------------------------------------------------------------------------
# Confidence scores
# ---------------------------------------------------------------------------

def test_mono_entry_has_low_confidence(entries_by_lemma, synsets_by_id):
    """A 'mono' source entry should have confidenceScore=0.71."""
    # 西暦 00001837-r source=mono → wnja-00001837-r
    target_ss = "wnja-00001837-r"
    found = False
    for entry in sum(entries_by_lemma.values(), []):
        for sense in entry.findall("Sense"):
            if sense.get("synset") == target_ss:
                cs = sense.get("confidenceScore")
                if cs is not None:
                    assert float(cs) == pytest.approx(0.71, abs=0.01), \
                        f"Expected 0.71, got {cs}"
                    found = True
    if not found:
        pytest.skip(f"{target_ss} not present in output")


# ---------------------------------------------------------------------------
# Synset content
# ---------------------------------------------------------------------------

def test_synsets_have_definitions(synsets_by_id):
    """Spot-check that synsets carry Japanese definitions."""
    ss = synsets_by_id.get("wnja-00002684-n")
    assert ss is not None, "wnja-00002684-n not found"
    defs = [d.text for d in ss.findall("Definition")]
    assert defs, "wnja-00002684-n has no definitions"


def test_synset_ili_preserved(synsets_by_id):
    """ILI values from NTU-MC should be present on synsets that had them."""
    ss = synsets_by_id.get("wnja-00002684-n")
    assert ss is not None
    assert ss.get("ili") == "i35549", "ILI not preserved for wnja-00002684-n"


def test_synset_relations_renamed(synsets_by_id):
    """SynsetRelation targets should use wnja- not ntumc-ja- prefix."""
    for ss in synsets_by_id.values():
        for rel in ss.findall("SynsetRelation"):
            tgt = rel.get("target", "")
            assert not tgt.startswith("ntumc-ja-"), \
                f"ntumc-ja- not renamed: {tgt}"
            assert tgt.startswith("wnja-"), \
                f"Unexpected synset ID format: {tgt}"


# ---------------------------------------------------------------------------
# Variant merging: 物 should have TWO entries (ブツ and モノ readings)
# ---------------------------------------------------------------------------

def test_mono_has_two_readings(entries_by_lemma):
    """物(n) should appear twice: once with ブツ reading, once with モノ."""
    mono_entries = entries_by_lemma.get(("物", "n"), [])
    readings = set()
    for e in mono_entries:
        for f in e.findall("Form"):
            wf = f.get("writtenForm", "")
            if wf in ("ブツ", "モノ"):
                readings.add(wf)
    assert "ブツ" in readings, "物(n) missing ブツ reading entry"
    assert "モノ" in readings, "物(n) missing モノ reading entry"


# ---------------------------------------------------------------------------
# IDs use wnja- prefix throughout
# ---------------------------------------------------------------------------

def test_sense_synset_ids_are_wnja(lexicon):
    """All Sense synset attributes must start with 'wnja-'."""
    for sense in lexicon.iter("Sense"):
        ss = sense.get("synset", "")
        assert ss.startswith("wnja-"), f"Non-wnja synset ref in Sense: {ss}"


# ---------------------------------------------------------------------------
# Orthographic variants: protein (14728724-n)
# ---------------------------------------------------------------------------

def test_protein_entry_tanpakushitsu(entries_by_lemma):
    """蛋白質 entry should have katakana and hiragana forms."""
    entries = entries_by_lemma.get(("蛋白質", "n"), [])
    assert entries, "蛋白質(n) not found"
    forms = {f.get("writtenForm") for e in entries for f in e.findall("Form")}
    assert "タンパクシツ" in forms, "蛋白質 missing タンパクシツ"
    assert "たんぱくしつ" in forms, "蛋白質 missing たんぱくしつ"


def test_protein_entry_tanpaku(entries_by_lemma):
    """蛋白 entry should exist with タンパク form."""
    entries = entries_by_lemma.get(("蛋白", "n"), [])
    assert entries, "蛋白(n) not found"
    forms = {f.get("writtenForm") for e in entries for f in e.findall("Form")}
    assert "タンパク" in forms, "蛋白 missing タンパク"


def test_protein_has_multiple_lemmas(synsets_by_id, lexicon):
    """synset 14728724-n should be referenced by multiple lemmas."""
    ss_id = "wnja-14728724-n"
    lemmas_for_ss = set()
    for entry in lexicon.findall("LexicalEntry"):
        lemma = entry.find("Lemma")
        for sense in entry.findall("Sense"):
            if sense.get("synset") == ss_id and lemma is not None:
                lemmas_for_ss.add(lemma.get("writtenForm"))
    assert len(lemmas_for_ss) >= 3, \
        f"Expected ≥3 lemmas for protein synset, got: {lemmas_for_ss}"


# ---------------------------------------------------------------------------
# Orthographic variants: absorb (02765464-v)
# ---------------------------------------------------------------------------

def test_absorb_suikomu_has_forms(entries_by_lemma):
    """吸い込む should have スイコム (kana) and すいこむ (hira) forms."""
    entries = entries_by_lemma.get(("吸い込む", "v"), [])
    assert entries, "吸い込む(v) not found"
    forms = {f.get("writtenForm") for e in entries for f in e.findall("Form")}
    assert "スイコム" in forms, "吸い込む missing スイコム"
    assert "すいこむ" in forms, "吸い込む missing すいこむ"


def test_absorb_kyuushu_is_sahen(entries_by_lemma):
    """吸収 (absorb) is a suru-verb and should be marked note='sahen'."""
    entries = entries_by_lemma.get(("吸収", "v"), [])
    if not entries:
        pytest.skip("吸収(v) not in output")
    assert any(e.get("note") == "sahen" for e in entries), \
        "吸収(v) not marked sahen"


def test_absorb_nomikomu_has_variants(entries_by_lemma):
    """飲み込む should have 呑み込む as an alternate kanji form."""
    entries = entries_by_lemma.get(("飲み込む", "v"), [])
    assert entries, "飲み込む(v) not found"
    forms = {f.get("writtenForm") for e in entries for f in e.findall("Form")}
    # Should have at least one alternate kanji variant
    assert "呑み込む" in forms or "呑込む" in forms, \
        f"飲み込む missing 呑 variants; got {forms}"


def test_absorb_synset_has_multiple_lemmas(lexicon):
    """synset 02765464-v should be covered by multiple lemma entries."""
    ss_id = "wnja-02765464-v"
    lemmas_for_ss = set()
    for entry in lexicon.findall("LexicalEntry"):
        lemma = entry.find("Lemma")
        for sense in entry.findall("Sense"):
            if sense.get("synset") == ss_id and lemma is not None:
                lemmas_for_ss.add(lemma.get("writtenForm"))
    assert len(lemmas_for_ss) >= 3, \
        f"Expected ≥3 lemmas for absorb synset, got: {lemmas_for_ss}"


# ---------------------------------------------------------------------------
# Japan-specific synsets (long-tail, pass-through from NTU-MC)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("synset_id,expected_lemma,expected_def_fragment", [
    ("wnja-80001626-n", "蕎麦", "そば粉"),
    ("wnja-80002377-n", "築城", "城"),
    ("wnja-90000315-n", "ハジャ", "巡礼"),
    ("wnja-80001731-n", "留学生", "学生"),
    ("wnja-80000338-n", "春闘", "労働組合"),
])
def test_japan_specific_synset(
    synset_id, expected_lemma, expected_def_fragment,
    synsets_by_id, lexicon
):
    """Japan-specific synsets should be in the output with definitions and lemmas."""
    ss = synsets_by_id.get(synset_id)
    assert ss is not None, f"{synset_id} not found in output"

    # Definition present and contains expected text
    defs = [d.text or "" for d in ss.findall("Definition")]
    assert any(expected_def_fragment in d for d in defs), \
        f"{synset_id} definition missing '{expected_def_fragment}'; got {defs}"

    # At least one lemma points to this synset
    lemmas_for_ss = set()
    for entry in lexicon.findall("LexicalEntry"):
        lemma = entry.find("Lemma")
        for sense in entry.findall("Sense"):
            if sense.get("synset") == synset_id and lemma is not None:
                lemmas_for_ss.add(lemma.get("writtenForm"))
    assert expected_lemma in lemmas_for_ss, \
        f"{synset_id}: expected lemma '{expected_lemma}' not found; got {lemmas_for_ss}"


# ---------------------------------------------------------------------------
# Temperature words
# ---------------------------------------------------------------------------

def test_samui_entry_exists(entries_by_lemma):
    """寒い(a) should be present (cold, as felt)."""
    assert entries_by_lemma.get(("寒い", "a")), "寒い(a) not found"


def test_tsumetai_entry_exists(entries_by_lemma):
    """冷たい(a) should be present (cold to touch)."""
    assert entries_by_lemma.get(("冷たい", "a")), "冷たい(a) not found"


def test_atsui_adj_entry_exists(entries_by_lemma):
    """暑い and 熱い (a) should both be present."""
    assert entries_by_lemma.get(("暑い", "a")), "暑い(a) not found"
    assert entries_by_lemma.get(("熱い", "a")), "熱い(a) not found"


def test_cold_hot_antonym_relation(synsets_by_id):
    """寒い(cold) synset should have an antonym relation to 暑い/熱い(hot)."""
    cold_ss = synsets_by_id.get("wnja-01251128-a")
    if cold_ss is None:
        pytest.skip("wnja-01251128-a not in output")
    # NOTE: antonym is a sense-level relation in WN-LMF; this tests what
    # is currently in the NTU-MC data (synset-level) pending migration.
    targets = {
        r.get("target")
        for r in cold_ss.findall("SynsetRelation")
        if r.get("relType") == "antonym"
    }
    assert targets, \
        "wnja-01251128-a (寒い/cold) has no antonym SynsetRelation"


# ---------------------------------------------------------------------------
# Classifiers (x POS)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("synset_id,expected_lemma,def_fragment", [
    ("wnja-76100129-x", "羽", "鳥"),          # birds and rabbits
    ("wnja-76100099-x", "人", "人間"),         # people (lawyers, doctors)
    ("wnja-76100106-x", "個", "無生物"),       # inanimate objects
    ("wnja-76100107-x", "冊", "本"),           # books and bound items
    ("wnja-76100098-x", "両", "車両"),         # vehicles
])
def test_classifier_synset(synset_id, expected_lemma, def_fragment,
                           synsets_by_id, lexicon):
    """Sortal classifier synsets should be present with definitions and lemmas."""
    ss = synsets_by_id.get(synset_id)
    assert ss is not None, f"{synset_id} not found"
    assert ss.get("partOfSpeech") == "x", f"{synset_id} has wrong POS"
    defs = [d.text or "" for d in ss.findall("Definition")]
    assert any(def_fragment in d for d in defs), \
        f"{synset_id}: '{def_fragment}' not in defs {defs}"
    lemmas = set()
    for entry in lexicon.findall("LexicalEntry"):
        lemma = entry.find("Lemma")
        for sense in entry.findall("Sense"):
            if sense.get("synset") == synset_id and lemma is not None:
                lemmas.add(lemma.get("writtenForm"))
    assert expected_lemma in lemmas, \
        f"{synset_id}: expected lemma '{expected_lemma}' not found; got {lemmas}"


# ---------------------------------------------------------------------------
# Greetings / exclamatives (x POS)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("synset_id,expected_lemma,def_fragment", [
    ("wnja-80000665-x", "おはようございます", "朝"),    # good morning
    ("wnja-80000668-x", "さようなら",         "別れ"),  # goodbye
    ("wnja-80000658-x", "え",                 "驚き"),  # surprise exclamation
    ("wnja-80000674-x", "お久しぶりです",      "長い"),  # long time no see
    ("wnja-80000945-x", "まあ",               "驚き"),  # admiration/surprise
])
def test_greeting_exclamative_synset(synset_id, expected_lemma, def_fragment,
                                     synsets_by_id, lexicon):
    """Greeting and exclamative synsets should be present with definitions and lemmas."""
    ss = synsets_by_id.get(synset_id)
    assert ss is not None, f"{synset_id} not found"
    assert ss.get("partOfSpeech") == "x", f"{synset_id} has wrong POS"
    defs = [d.text or "" for d in ss.findall("Definition")]
    assert any(def_fragment in d for d in defs), \
        f"{synset_id}: '{def_fragment}' not in defs {defs}"
    lemmas = set()
    for entry in lexicon.findall("LexicalEntry"):
        lemma = entry.find("Lemma")
        for sense in entry.findall("Sense"):
            if sense.get("synset") == synset_id and lemma is not None:
                lemmas.add(lemma.get("writtenForm"))
    assert expected_lemma in lemmas, \
        f"{synset_id}: expected lemma '{expected_lemma}' not found; got {lemmas}"
