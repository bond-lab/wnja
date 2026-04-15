#!/usr/bin/env python3
"""Generate a TOML quality report for wnja-2.0.xml.

Output: reports/wnja-2.0-quality.toml

Sections:
  [meta]              — build metadata
  [summary]           — aggregate counts and distributions
  [issues.*]          — per-issue totals
  Appendices (full lists):
    [[appendix.no_pronunciation.entries]]
    [[appendix.no_definition.synsets]]
    [[appendix.low_confidence.entries]]
    [[appendix.single_lemma_synsets.synsets]]
    [[appendix.duplicate_lemma_pos.groups]]
"""

import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from xml.etree import ElementTree as ET

import tomli_w

OUTPUT = Path("wnja-2.0.xml")
REPORT_DIR = Path("reports")
REPORT_FILE = REPORT_DIR / "wnja-2.0-quality.toml"

HIRAGANA = frozenset(
    "ぁあぃいぅうぇえぉおかがきぎくぐけげこごさざしじすずせぜそぞた"
    "だちぢっつづてでとどなにぬねのはばぱひびぴふぶぷへべぺほぼぽま"
    "みむめもゃやゅゆょよらりるれろゎわゐゑをんゔゕゖゝゞ・ー＝"
)
KATAKANA = frozenset(
    "ァアィイゥウェエォオカガキギクグケゲコゴサザシジスズセゼソゾタ"
    "ダチヂッツヅテデトドナニヌネノハバパヒビピフブプヘベペホボポマ"
    "ミムメモャヤュユョヨラリルレロヮワヰヱヲンヴヵヶヽヾ・ー"
)
JAPAN_PREFIXES = tuple(
    f"wnja-{p}" for p in
    ("70", "71", "72", "73", "74", "75", "76",
     "77", "80", "81", "82", "83", "84", "85",
     "90", "91", "92", "93", "94", "95")
)
POS_LABELS = {"n": "noun", "v": "verb", "a": "adj", "r": "adv", "s": "adj-sat",
              "x": "classifier/exclamative", "u": "unknown"}


def _is_kana(word: str) -> bool:
    return bool(word) and all(c in HIRAGANA or c in KATAKANA for c in word)


def _entry_lemma(entry) -> str:
    lemma = entry.find("Lemma")
    return lemma.get("writtenForm", "") if lemma is not None else ""


def _entry_pos(entry) -> str:
    lemma = entry.find("Lemma")
    return lemma.get("partOfSpeech", "") if lemma is not None else ""


def _sense_conf(sense) -> float | None:
    cs = sense.get("confidenceScore")
    return float(cs) if cs is not None else None


def _is_japan_specific(ss_id: str) -> bool:
    return any(ss_id.startswith(p) for p in JAPAN_PREFIXES)


def load_xml(path: Path):
    if not path.exists():
        print(f"ERROR: {path} not found. Run build_wnja.py first.", file=sys.stderr)
        sys.exit(1)
    tree = ET.parse(path)
    root = tree.getroot()
    lex = root.find("Lexicon")
    return lex if lex is not None else root


def build_report(lex) -> dict:
    entries = lex.findall("LexicalEntry")
    synsets = lex.findall("Synset")

    # ------------------------------------------------------------------
    # [meta]
    # ------------------------------------------------------------------
    meta = {
        "generated": str(date.today()),
        "source": str(OUTPUT),
        "lexicon_id": lex.get("id", ""),
        "version": lex.get("version", ""),
        "language": lex.get("language", ""),
    }

    # ------------------------------------------------------------------
    # [summary]
    # ------------------------------------------------------------------
    pos_counts: Counter = Counter()
    conf_buckets: Counter = Counter()
    no_sense_count = 0
    sahen_count = 0

    for entry in entries:
        pos_counts[_entry_pos(entry)] += 1
        senses = entry.findall("Sense")
        if not senses:
            no_sense_count += 1
        for sense in senses:
            cs = _sense_conf(sense)
            if cs is None:
                conf_buckets["hand_1.00"] += 1
            elif cs >= 0.90:
                conf_buckets["mlsn_0.91"] += 1
            elif cs >= 0.72:
                conf_buckets["multi_0.72"] += 1
            else:
                conf_buckets["mono_0.71"] += 1
        if entry.get("note") == "sahen":
            sahen_count += 1

    ss_no_def = sum(1 for ss in synsets if not ss.findall("Definition"))
    ss_no_ili = sum(1 for ss in synsets if not ss.get("ili"))
    ss_no_rel = sum(1 for ss in synsets if not ss.findall("SynsetRelation"))
    ss_japan = sum(1 for ss in synsets if _is_japan_specific(ss.get("id", "")))
    ss_x = sum(1 for ss in synsets if ss.get("partOfSpeech") == "x")

    summary = {
        "total_entries": len(entries),
        "total_synsets": len(synsets),
        "japan_specific_synsets": ss_japan,
        "classifier_exclamative_synsets": ss_x,
        "entries_no_senses": no_sense_count,
        "sahen_verbs": sahen_count,
        "pos_breakdown": {
            f"{POS_LABELS.get(p, p)}_({p})": n
            for p, n in sorted(pos_counts.items(), key=lambda x: -x[1])
        },
        "confidence_senses": dict(sorted(conf_buckets.items())),
        "synset_health": {
            "no_definition": ss_no_def,
            "no_ili": ss_no_ili,
            "no_relations": ss_no_rel,
        },
    }

    # ------------------------------------------------------------------
    # Build index: synset_id → [lemma, ...]
    # ------------------------------------------------------------------
    ss_to_lemmas: dict[str, list[str]] = defaultdict(list)
    for entry in entries:
        lemma = _entry_lemma(entry)
        for sense in entry.findall("Sense"):
            ss_to_lemmas[sense.get("synset", "")].append(lemma)

    # ------------------------------------------------------------------
    # Appendix: entries with no pronunciation
    # ------------------------------------------------------------------
    no_pron: list[dict] = []
    for entry in entries:
        lemma_elem = entry.find("Lemma")
        if lemma_elem is None:
            continue
        script = lemma_elem.get("script", "")
        lemma_wf = lemma_elem.get("writtenForm", "")
        if script in ("kana", "hira") or _is_kana(lemma_wf):
            continue
        has_kana = any(
            f.get("script") in ("kana", "hira") or _is_kana(f.get("writtenForm", ""))
            for f in entry.findall("Form")
        )
        if not has_kana:
            senses = entry.findall("Sense")
            no_pron.append({
                "lemma": lemma_wf,
                "pos": _entry_pos(entry),
                "synsets": [s.get("synset", "") for s in senses],
            })
    no_pron.sort(key=lambda x: x["lemma"])

    # ------------------------------------------------------------------
    # Appendix: synsets with no definition
    # ------------------------------------------------------------------
    no_def: list[dict] = []
    for ss in synsets:
        if not ss.findall("Definition"):
            sid = ss.get("id", "")
            no_def.append({
                "id": sid,
                "pos": ss.get("partOfSpeech", ""),
                "lemmas": ss_to_lemmas.get(sid, []),
            })
    no_def.sort(key=lambda x: x["id"])

    # ------------------------------------------------------------------
    # Appendix: entries where all senses are low confidence (≤ 0.72)
    # ------------------------------------------------------------------
    low_conf: list[dict] = []
    for entry in entries:
        senses = entry.findall("Sense")
        if not senses:
            continue
        confs = [_sense_conf(s) for s in senses]
        if all(c is not None and c <= 0.72 for c in confs):
            low_conf.append({
                "lemma": _entry_lemma(entry),
                "pos": _entry_pos(entry),
                "max_confidence": max(c for c in confs if c is not None),
                "synsets": [s.get("synset", "") for s in senses],
            })
    low_conf.sort(key=lambda x: (x["max_confidence"], x["lemma"]))

    # ------------------------------------------------------------------
    # Appendix: Japan-specific synsets with only one lemma
    # ------------------------------------------------------------------
    single_lemma: list[dict] = []
    for ss in synsets:
        sid = ss.get("id", "")
        if not _is_japan_specific(sid):
            continue
        lemmas = ss_to_lemmas.get(sid, [])
        if len(lemmas) == 1:
            defs = [d.text or "" for d in ss.findall("Definition")]
            single_lemma.append({
                "id": sid,
                "pos": ss.get("partOfSpeech", ""),
                "lemma": lemmas[0],
                "definition": defs[0] if defs else "",
            })
    single_lemma.sort(key=lambda x: x["id"])

    # ------------------------------------------------------------------
    # Appendix: classifier / exclamative (x POS) synsets — full inventory
    # ------------------------------------------------------------------
    x_synsets: list[dict] = []
    for ss in synsets:
        if ss.get("partOfSpeech") != "x":
            continue
        sid = ss.get("id", "")
        defs = [d.text or "" for d in ss.findall("Definition")]
        rels = [
            {"type": r.get("relType", ""), "target": r.get("target", "")}
            for r in ss.findall("SynsetRelation")
        ]
        x_synsets.append({
            "id": sid,
            "lemmas": ss_to_lemmas.get(sid, []),
            "definition": defs[0] if defs else "",
            "relations": rels,
        })
    x_synsets.sort(key=lambda x: x["id"])

    # ------------------------------------------------------------------
    # Appendix: duplicate (lemma, pos) pairs
    # ------------------------------------------------------------------
    counts: Counter = Counter()
    for entry in entries:
        lemma = entry.find("Lemma")
        if lemma is not None:
            key = (lemma.get("writtenForm", ""), lemma.get("partOfSpeech", ""))
            counts[key] += 1

    dupe_groups: list[dict] = []
    for (wf, pos), n in sorted(counts.items(), key=lambda x: -x[1]):
        if n > 1:
            dupe_groups.append({"lemma": wf, "pos": pos, "entry_count": n})

    # ------------------------------------------------------------------
    # [issues] summary (totals only)
    # ------------------------------------------------------------------
    issues = {
        "no_pronunciation": {"total": len(no_pron)},
        "no_definition": {"total": len(no_def)},
        "low_confidence_all_senses": {"total": len(low_conf), "threshold": 0.72},
        "single_lemma_japan_synsets": {"total": len(single_lemma)},
        "homograph_readings": {"total": len(dupe_groups)},
    }

    # ------------------------------------------------------------------
    # Assemble
    # ------------------------------------------------------------------
    return {
        "meta": meta,
        "summary": summary,
        "issues": issues,
        "appendix": {
            "no_pronunciation": {"entries": no_pron},
            "no_definition": {"synsets": no_def},
            "low_confidence": {"entries": low_conf},
            "single_lemma_synsets": {"synsets": single_lemma},
            "homograph_readings": {"groups": dupe_groups},
            "classifier_exclamative_synsets": {"synsets": x_synsets},
        },
    }


def main() -> None:
    lex = load_xml(OUTPUT)
    report = build_report(lex)

    REPORT_DIR.mkdir(exist_ok=True)
    with open(REPORT_FILE, "wb") as fh:
        tomli_w.dump(report, fh)

    issues = report["issues"]
    s = report["summary"]
    print(f"Report written to {REPORT_FILE}")
    print(f"  {s['total_entries']:,} entries  |  {s['total_synsets']:,} synsets")
    print(f"  Issues: "
          f"{issues['no_pronunciation']['total']:,} missing kana  |  "
          f"{issues['no_definition']['total']:,} no def  |  "
          f"{issues['low_confidence_all_senses']['total']:,} low-conf  |  "
          f"{issues['single_lemma_japan_synsets']['total']:,} single-lemma JP synsets  |  "
          f"{issues['homograph_readings']['total']:,} homograph readings  |  "
          f"{report['summary']['classifier_exclamative_synsets']:,} x-pos synsets")


if __name__ == "__main__":
    main()
