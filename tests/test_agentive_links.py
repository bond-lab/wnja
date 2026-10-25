"""Tests for the agentive noun links (Bond and Wei 2019)."""

from pathlib import Path
from types import SimpleNamespace

from scripts.agentive_links import link
from tweak_wnja import apply_agentive_links

HEADER = ("verb_synset\tverb\tnoun_synset\tnoun\tsuffix\trel_type\tlink_type"
          "\tfirst_check\tsecond_check\tsn\n")


def row(**kw: str) -> dict:
    """A spreadsheet row: a Japanese pair accepted by both checks."""
    base = {"S/N": 1, "lang": "jpn", "verbsynset": "01930874-v", "verb": "運転+する",
            "nounsynset": "10034096-n", "noun": "運転者", "match type": "者",
            "link": "yes", "link_2nd check": "yes", "link type": "agent",
            "link type_2nd check": "agent"}
    return base | kw


def test_link_uses_second_check() -> None:
    """The second check decides; its type wins; suru-verbs lose する."""
    assert link(row())[:6] == ["wnja-01930874-v", "運転", "wnja-10034096-n",
                               "運転者", "者", "agent"]
    assert link(row(**{"link_2nd check": "no"})) is None
    assert link(row(lang="eng")) is None
    assert link(row(verb="読+み"))[1] == "読み"
    assert link(row(**{"link type_2nd check": "undergoer/patient"}))[5] == "undergoer"
    assert link(row(**{"link type_2nd check": None,
                       "link type": "inanimate agent/cause"}))[5] == "by_means_of"


def editor() -> SimpleNamespace:
    """A verb with two readings' worth of senses and one noun."""
    entries = [
        {"id": "e1", "lemma": {"writtenForm": "運転", "partOfSpeech": "v"},
         "senses": [{"id": "v1", "synset": "wnja-drive-v"}]},
        {"id": "e2", "lemma": {"writtenForm": "運転者", "partOfSpeech": "n"},
         "forms": [{"writtenForm": "うんてんしゃ", "script": "hira"}],
         "senses": [{"id": "n1", "synset": "wnja-driver-n"}]},
    ]
    return SimpleNamespace(_lexicon={"entries": entries})


def test_apply_adds_relation_once(tmp_path: Path) -> None:
    """other/agent and derivation are added once; missing senses skip."""
    tsv = tmp_path / "links.tsv"
    pair = "wnja-drive-v\t運転\twnja-driver-n\t運転者\t者\tagent\tagent\tyes\tyes"
    gone = "wnja-drive-v\t運転\twnja-gone-n\t運転手\t手\tagent\tagent\tyes\tyes"
    tsv.write_text(HEADER + f"{pair}\t1\n{pair}\t2\n{gone}\t3\n", encoding="utf-8")
    ed = editor()

    assert apply_agentive_links(ed, tsv) == 1
    verb = ed._lexicon["entries"][0]["senses"][0]
    noun = ed._lexicon["entries"][1]["senses"][0]
    assert [(r["target"], r["relType"], (r.get("meta") or {}).get("type"))
            for r in verb["relations"]] == [("n1", "other", "agent"),
                                            ("n1", "derivation", None)]
    assert [(r["target"], r["relType"]) for r in noun["relations"]] == [
        ("v1", "derivation")]
