#!/usr/bin/env python3
"""Build Japanese WordNet (wnja) v2.0 from NTU-MC XML and variant tab files.

Pipeline:
  1. Parse data/wn-ntumc-jpn.xml for base synsets and sense counts.
  2. Read data/vars_tk17.tab  → orthographic forms per (lemma, hno).
  3. Read data/wn+var_tk17.tab → authoritative (synset, lemma, hno) mappings
     with confidence scores.
  4. Merge: wn+var entries replace NTU-MC entries for covered synsets;
     uncovered NTU-MC senses are passed through.
  5. Detect sahen (suru) verbs with jamdict.
  6. Export wnja-2.0.xml via wn.lmf.dump (no Latin scripts).
"""

import logging
import sys
from collections import defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET

import wn as _wn
from wn import lmf
from wn_edit import (
    make_count,
    make_definition,
    make_example,
    make_form,
    make_lemma,
    make_lexical_entry,
    make_lexical_resource,
    make_lexicon,
    make_relation,
    make_sense,
    make_synset,
)

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------
DATA = Path("data")
VARS_FILE = DATA / "vars_tk17.tab"
WNVAR_FILE = DATA / "wn+var_tk17.tab"
NTUMC_XML = DATA / "wn-ntumc-jpn.xml"
SAHEN_FILE = DATA / "sahen_verbs.txt"
ILI_MAP_FILE = Path("../NTUMC/build/ili-map-pwn30.tab")
OUTPUT = Path("wnja-2.0.xml")
LOG_FILE = Path("wnja.log")

VERSION = "2.0"
CONF_MAP: dict[str, float] = {
    "hand": 1.0,
    "mlsn": 0.91,
    "multi": 0.72,
    "mono": 0.71,
}
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
GODAN_ENDINGS = frozenset(
    "うくぐすずつづぬふぶぷむゆるウクグスズツヅヌフプブムユル"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stderr),
    ],
    force=True,
)
log = logging.getLogger("wnja")


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def desuru(word: str) -> str:
    """Strip +suru / +する verb-forming suffixes."""
    if word.endswith("+する"):
        return word[:-3]
    if word.endswith("+suru"):
        return word[:-5]
    return word


def is_hiragana(word: str) -> bool:
    return bool(word) and all(c in HIRAGANA for c in word)


def is_katakana(word: str) -> bool:
    return bool(word) and all(c in KATAKANA for c in word)


def kana_script(word: str) -> str | None:
    """Return 'hira', 'kana', or None for the script of a word."""
    if is_hiragana(word):
        return "hira"
    if is_katakana(word):
        return "kana"
    return None


def ntumc_id_to_num(ntumc_id: str) -> str:
    """'ntumc-ja-00002684-n'  →  '00002684-n'"""
    return ntumc_id.removeprefix("ntumc-ja-")


def synset_num_to_wnja(num: str) -> str:
    """'00002684-n'  →  'wnja-00002684-n'"""
    return f"wnja-{num}"


# ---------------------------------------------------------------------------
# Read variant tab files
# ---------------------------------------------------------------------------

def read_vars(path: Path) -> dict[tuple[str, str], list[tuple[str, str]]]:
    """Parse vars_tk17.tab.

    Format per line: lemma | hno | kana_or_isyomi | extra_forms...

    Returns:
        {(lemma, hno): [(writtenForm, script), ...]}
        First element is always the canonical form.
        Scripts: '' (kanji/mixed), 'kana' (katakana), 'hira' (hiragana).
        Latin forms are NOT included.
    """
    forms: dict[tuple[str, str], list[tuple[str, str]]] = {}
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            if raw.startswith("#") or not raw.strip():
                continue
            row = [c.strip() for c in raw.split("\t")]
            lemma = desuru(row[0])
            hno = row[1]
            key = (lemma, hno)
            if key in forms:
                log.warning("duplicate key in vars: %s %s", lemma, hno)
                continue

            known: set[str] = set()
            tag1 = ""

            if row[2] == "isyomi":
                # The lemma itself is kana
                tag1 = "kana"
            else:
                # row[2] is the katakana reading
                known.add(row[2])
                entry_list: list[tuple[str, str]] = [(row[2], "kana")]
                if is_hiragana(row[0]):
                    tag1 = "hira"
            # Canonical form goes first
            known.add(lemma)
            if row[2] == "isyomi":
                entry_list = [(lemma, "kana")]
            else:
                entry_list.insert(0, (lemma, tag1))

            # Extra forms (skip latin: no latn / latn-hepburn)
            for alt in row[3:]:
                if not alt or alt in known:
                    continue
                known.add(alt)
                if is_hiragana(alt):
                    entry_list.append((alt, "hira"))
                else:
                    entry_list.append((alt, ""))

            forms[key] = entry_list
    return forms


def read_wnvar(
    path: Path,
) -> tuple[dict[tuple[str, str], list[tuple[str, float]]], set[str]]:
    """Parse wn+var_tk17.tab.

    Format per line: synset_num | lemma | hno | source | (ignored) | ...

    Returns:
        l2senses: {(lemma, hno): [(wnja_synset_id, confidence), ...]}
        covered_synsets: set of synset_nums that appear in wn+var
    """
    l2senses: dict[tuple[str, str], list[tuple[str, float]]] = defaultdict(list)
    covered_synsets: set[str] = set()

    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            if raw.startswith("#") or not raw.strip():
                continue
            row = [c.strip() for c in raw.split("\t")]
            synset_num = row[0]
            lemma = desuru(row[1])
            hno = row[2]
            source = row[3]

            conf = CONF_MAP.get(source, 1.0)
            wnja_ss = synset_num_to_wnja(synset_num)
            key = (lemma, hno)
            entry = (wnja_ss, conf)
            if entry not in l2senses[key]:
                l2senses[key].append(entry)
            covered_synsets.add(synset_num)

    return dict(l2senses), covered_synsets


# ---------------------------------------------------------------------------
# Parse NTU-MC XML
# ---------------------------------------------------------------------------

def _strip_ns(tag: str) -> str:
    """Remove XML namespace prefix from a tag."""
    return tag.split("}")[-1] if "}" in tag else tag


def parse_ntumc_xml(path: Path) -> tuple[
    list[tuple[str, str, str, list[tuple[str, int | None]]]],
    dict[str, dict],
]:
    """Parse the NTU-MC LMF XML.

    Returns:
        entries: [(word_id, lemma, pos, [(synset_num, count_or_None), ...])]
        synsets: {synset_num: {ili, pos, definitions, relations, examples}}
    """
    entries = []
    synsets: dict[str, dict] = {}

    tree = ET.parse(path)
    root = tree.getroot()

    # Lexicon is typically the first (only) child of LexicalResource
    lexicon = root.find("Lexicon")
    if lexicon is None:
        lexicon = root  # fallback

    for child in lexicon:
        tag = _strip_ns(child.tag)

        if tag == "LexicalEntry":
            word_id = child.get("id", "")
            lemma_elem = child.find("Lemma")
            if lemma_elem is None:
                continue
            lemma = desuru(lemma_elem.get("writtenForm", ""))
            pos = lemma_elem.get("partOfSpeech", "")

            senses: list[tuple[str, int | None]] = []
            for sense_elem in child.findall("Sense"):
                raw_ss = sense_elem.get("synset", "")
                synset_num = ntumc_id_to_num(raw_ss)
                count_elem = sense_elem.find("Count")
                count = int(count_elem.text) if count_elem is not None and count_elem.text else None
                senses.append((synset_num, count))
            entries.append((word_id, lemma, pos, senses))

        elif tag == "Synset":
            raw_id = child.get("id", "")
            synset_num = ntumc_id_to_num(raw_id)
            ili = child.get("ili", "")
            pos = child.get("partOfSpeech", "")

            definitions = [
                d.text for d in child.findall("Definition") if d.text
            ]
            examples = [
                e.text for e in child.findall("Example") if e.text
            ]
            relations = []
            for rel in child.findall("SynsetRelation"):
                target_num = ntumc_id_to_num(rel.get("target", ""))
                rel_type = rel.get("relType", "")
                if target_num and rel_type:
                    relations.append((rel_type, target_num))

            synsets[synset_num] = {
                "ili": ili,
                "pos": pos,
                "definitions": definitions,
                "examples": examples,
                "relations": relations,
            }

    return entries, synsets


# ---------------------------------------------------------------------------
# Sahen (suru-verb) detection
# ---------------------------------------------------------------------------

def load_sahen_set(path: Path) -> frozenset[str]:
    """Load the pre-computed sahen verb set from detect_sahen.py output.

    If the file does not exist, raises FileNotFoundError with a helpful message.
    Run detect_sahen.py first to generate data/sahen_verbs.txt.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Run `uv run python detect_sahen.py` first."
        )
    words: set[str] = set()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                words.add(line)
    log.info("Loaded %d sahen verbs from %s", len(words), path)
    return frozenset(words)


# ---------------------------------------------------------------------------
# Build output LMF structures
# ---------------------------------------------------------------------------

def _merge_by_lemma_pos(entries: list[dict]) -> list[dict]:
    """Merge LexicalEntry dicts that share (writtenForm, partOfSpeech).

    Combines senses (deduplicated by synset id) and forms (deduplicated by
    writtenForm). The first-seen entry's id and metadata are kept.

    Args:
        entries: List of LexicalEntry dicts as produced by make_lexical_entry.

    Returns:
        New list with one entry per (writtenForm, partOfSpeech) pair.
    """
    merged: dict[tuple[str, str], dict] = {}
    for entry in entries:
        lf = entry["lemma"]["writtenForm"]
        pos = entry["lemma"]["partOfSpeech"]
        key = (lf, pos)
        if key not in merged:
            merged[key] = entry
        else:
            existing = merged[key]
            existing_ss = {s["synset"] for s in existing.get("senses", [])}
            for sense in entry.get("senses", []):
                if sense["synset"] not in existing_ss:
                    existing_ss.add(sense["synset"])
                    existing.setdefault("senses", []).append(sense)
            existing_wf = {
                existing["lemma"]["writtenForm"],
                *(f.get("writtenForm", "") for f in existing.get("forms", [])),
            }
            for form in entry.get("forms", []):
                if form.get("writtenForm", "") not in existing_wf:
                    existing_wf.add(form["writtenForm"])
                    existing.setdefault("forms", []).append(form)
    return list(merged.values())


def build_entries(
    l2senses: dict[tuple[str, str], list[tuple[str, float]]],
    forms: dict[tuple[str, str], list[tuple[str, str]]],
    ntumc_counts: dict[tuple[str, str], int],
    sahen_set: frozenset[str],
) -> list[dict]:
    """Build wn_edit LexicalEntry dicts for all wn+var entries."""
    entries = []

    # Group (lemma, hno) by canonical key, collect senses per pos
    for idx, ((lemma, hno), senses) in enumerate(l2senses.items()):
        # Get orthographic forms
        if (lemma, hno) in forms:
            form_list = forms[(lemma, hno)]
        elif (lemma, "0") in forms and hno != "0":
            log.warning("no forms for (%s, %s); falling back to hno=0", lemma, hno)
            form_list = forms[(lemma, "0")]
        else:
            log.warning("no forms for (%s, %s); using bare lemma", lemma, hno)
            form_list = [(lemma, "")]

        # Group senses by pos (last char of synset id 'wnja-XXXXXXXX-p')
        pos_senses: dict[str, list[tuple[str, float]]] = defaultdict(list)
        for wnja_ss, conf in senses:
            pos = wnja_ss[-1]
            pos_senses[pos].append((wnja_ss, conf))

        canonical = form_list[0][0]

        for pos, ps_list in pos_senses.items():
            # Sahen check (only for verbs)
            note = None
            if pos == "v" and canonical in sahen_set:
                note = "sahen"

            # Lemma element (first form)
            lf, ls = form_list[0]
            lemma_obj = make_lemma(lf, pos, script=ls or None)

            # Additional forms (no latin)
            form_objs = [
                make_form(f, script=s or None)
                for f, s in form_list[1:]
            ]

            # Senses with counts from NTU-MC
            sense_objs = []
            for wnja_ss, conf in ps_list:
                synset_num = wnja_ss[len("wnja-"):]
                sense_id = f"wnja-{synset_num}-{idx}"
                count = ntumc_counts.get((synset_num, lemma))
                meta = {"confidenceScore": conf} if conf < 1.0 else None
                count_objs = [make_count(count)] if count else []
                sense_objs.append(
                    make_sense(sense_id, wnja_ss, counts=count_objs, meta=meta)
                )

            entry = make_lexical_entry(
                f"wnja-{pos}-{idx}",
                lemma_obj,
                forms=form_objs or None,
                senses=sense_objs,
                meta={"note": note} if note else None,
            )
            entries.append(entry)

    deduped = _merge_by_lemma_pos(entries)
    if len(deduped) < len(entries):
        log.info("  merged %d entries into %d by (lemma, pos)",
                 len(entries), len(deduped))
    return deduped


def build_passthrough_entries(
    ntumc_entries: list[tuple[str, str, str, list[tuple[str, int | None]]]],
    covered_synsets: set[str],
    forms: dict[tuple[str, str], list[tuple[str, str]]],
    sahen_set: frozenset[str],
    start_idx: int,
) -> list[dict]:
    """Build entries for NTU-MC senses not covered by wn+var.

    For each NTU-MC entry, pass through any senses that point to synsets NOT
    in covered_synsets (e.g. Japan-specific synsets). Senses pointing to
    covered synsets are dropped (wn+var is authoritative for those).
    A lemma may appear in both wn+var (for Princeton WN synsets) and here
    (for Japan-specific synsets).
    """
    entries = []
    # Track which (lemma_pt, pos) groups we've already emitted
    # to avoid duplicate entries across multiple NTU-MC word_ids
    seen: dict[tuple[str, str], int] = {}

    for _wid, lemma, pos, senses in ntumc_entries:
        uncovered = [
            (sn, cnt) for sn, cnt in senses if sn not in covered_synsets
        ]
        if not uncovered:
            continue

        key = (lemma, pos)
        if key in seen:
            # Merge additional senses into existing entry
            existing = entries[seen[key]]
            existing_ss = {s["synset"] for s in existing["senses"]}
            for sn, cnt in uncovered:
                wnja_ss = synset_num_to_wnja(sn)
                if wnja_ss in existing_ss:
                    continue
                existing_ss.add(wnja_ss)
                sense_id = f"wnja-{sn}-{seen[key]}x"
                count_objs = [make_count(cnt)] if cnt else []
                existing["senses"].append(
                    make_sense(sense_id, wnja_ss, counts=count_objs)
                )
            continue

        idx = start_idx + len(entries)
        note = None
        if pos == "v" and lemma in sahen_set:
            note = "sahen"

        # Forms: check vars_tk17 for (lemma, '0')
        if (lemma, "0") in forms:
            form_list = forms[(lemma, "0")]
        else:
            form_list = [(lemma, "")]

        lf, ls = form_list[0]
        lemma_obj = make_lemma(lf, pos, script=ls or kana_script(lf))
        form_objs = [make_form(f, script=s or kana_script(f)) for f, s in form_list[1:]]

        sense_objs = []
        for sn, cnt in uncovered:
            wnja_ss = synset_num_to_wnja(sn)
            sense_id = f"wnja-{sn}-{idx}"
            count_objs = [make_count(cnt)] if cnt else []
            sense_objs.append(make_sense(sense_id, wnja_ss, counts=count_objs))

        entry = make_lexical_entry(
            f"wnja-{pos}-{idx}",
            lemma_obj,
            forms=form_objs or None,
            senses=sense_objs,
            meta={"note": note} if note else None,
        )
        seen[key] = len(entries)
        entries.append(entry)

    return entries


def build_synsets(
    synsets_data: dict[str, dict],
    all_synset_nums: set[str],
) -> list[dict]:
    """Build wn_edit Synset dicts, renaming IDs from ntumc-ja-* to wnja-*."""
    synset_objs = []
    for synset_num, data in synsets_data.items():
        if synset_num not in all_synset_nums:
            continue

        wnja_ss = synset_num_to_wnja(synset_num)
        pos = data["pos"]
        # Normalise 's' → 'a' for adjective satellites
        if pos == "s":
            pos = "a"

        defs = [make_definition(d) for d in dict.fromkeys(data["definitions"])]
        exes = [make_example(e) for e in data["examples"]]
        rels = [
            make_relation(synset_num_to_wnja(tgt), rel_type)
            for rel_type, tgt in data["relations"]
        ]

        synset_objs.append(
            make_synset(
                wnja_ss,
                pos,
                ili=data["ili"] or None,
                definitions=defs or None,
                relations=rels or None,
                examples=exes or None,
            )
        )
    return synset_objs


def load_ili_map(path: Path) -> dict[str, str]:
    """Load ILI map: {synset_num -> ili_id} (e.g. '00003700-s' -> 'i11').

    Handles both original POS chars and the wnja 's'→'a' normalisation by
    storing entries under their original key only; callers must try both.
    """
    if not path.exists():
        log.warning("ILI map not found at %s; stub synsets will lack ILI", path)
        return {}
    ili_map: dict[str, str] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) >= 2:
                ili_map[parts[1]] = parts[0]
    return ili_map


def build_stub_synsets(
    synset_objs: list[dict],
    ili_map: dict[str, str],
) -> list[dict]:
    """Build stub synsets for relation targets not already in the output.

    Each stub carries the ILI and an English definition from omw-en:2.0 so
    the file is self-contained; senses are added when wordnets are combined.
    """
    included = {ss["id"] for ss in synset_objs}
    missing: set[str] = set()
    for ss in synset_objs:
        for rel in ss.get("relations", []):
            tgt = rel["target"]
            if tgt not in included:
                missing.add(tgt)

    if not missing:
        return []

    stubs = []
    no_defn = 0
    for wnja_id in sorted(missing):
        num = wnja_id.removeprefix("wnja-")   # e.g. '00003700-a'
        pos = num[-1]
        # ILI map uses original PWN POS: adj satellites are 's', not 'a'
        ili = ili_map.get(num) or ili_map.get(num[:-1] + "s")
        defn = None
        if ili:
            ss_list = _wn.synsets(ili=ili)
            if ss_list:
                defn = ss_list[0].definition()
        # Fallback: direct omw-en ID lookup — gives both ILI and definition
        # without needing the ILI map file (omw-en IDs are omw-en-XXXXXXXX-{a,s})
        if defn is None:
            for suffix in (num, num[:-1] + "s"):
                try:
                    en_ss = _wn.synset(f"omw-en-{suffix}")
                    if ili is None:
                        ili = en_ss.ili
                    defn = en_ss.definition()
                    if defn:
                        break
                except Exception:
                    pass
        # Always emit the stub so the relation target resolves in wn.add();
        # a definition is desirable but not required
        if defn is None:
            no_defn += 1
        stub = make_synset(wnja_id, pos, ili=ili or None,
                           definitions=[make_definition(defn)] if defn else [])
        stub["lexicalized"] = False  # WN-LMF: no Japanese entries for this synset
        stubs.append(stub)

    if no_defn:
        log.warning("  %d stub targets had no definition (created empty stubs)", no_defn)
    return stubs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    log.info("Reading variant files …")
    forms = read_vars(VARS_FILE)
    l2senses, covered_synsets = read_wnvar(WNVAR_FILE)

    log.info(
        "vars: %d form groups | wn+var: %d (lemma,hno) entries, %d synsets",
        len(forms),
        len(l2senses),
        len(covered_synsets),
    )

    log.info("Parsing NTU-MC XML (this may take a moment) …")
    ntumc_entries, synsets_data = parse_ntumc_xml(NTUMC_XML)
    log.info(
        "NTU-MC: %d word entries, %d synsets",
        len(ntumc_entries),
        len(synsets_data),
    )

    # Aggregate NTU-MC sense counts per (synset_num, lemma) for merging
    ntumc_counts: dict[tuple[str, str], int] = defaultdict(int)
    for _wid, lemma, _pos, senses in ntumc_entries:
        for synset_num, count in senses:
            if count:
                ntumc_counts[(synset_num, lemma)] += count

    log.info("Loading sahen verb list …")
    sahen_set = load_sahen_set(SAHEN_FILE)

    log.info("Building merged entries from wn+var …")
    main_entries = build_entries(l2senses, forms, ntumc_counts, sahen_set)
    log.info("  %d entries built", len(main_entries))

    log.info("Building pass-through entries from NTU-MC …")
    pass_entries = build_passthrough_entries(
        ntumc_entries,
        covered_synsets,
        forms,
        sahen_set,
        start_idx=len(main_entries),
    )
    log.info("  %d pass-through entries added", len(pass_entries))

    combined = main_entries + pass_entries
    all_entries = _merge_by_lemma_pos(combined)
    n_cross = len(combined) - len(all_entries)
    if n_cross:
        log.info("  merged %d cross-pipeline duplicate (lemma, pos) entries", n_cross)

    # Collect all synset nums referenced by output senses
    all_synset_nums: set[str] = set()
    for entry in all_entries:
        for sense in entry.get("senses", []):
            ss = sense["synset"]
            all_synset_nums.add(ss[len("wnja-"):])

    # Also include synsets that are relation targets of included synsets and
    # have NTU-MC content (definitions) — prevents dangling SynsetRelation refs.
    extra: set[str] = set()
    for synset_num in all_synset_nums:
        data = synsets_data.get(synset_num)
        if data:
            for _rel_type, tgt_num in data["relations"]:
                tgt = synsets_data.get(tgt_num)
                if tgt and tgt["definitions"] and tgt_num not in all_synset_nums:
                    extra.add(tgt_num)
    if extra:
        log.info("  %d additional synsets added as relation targets", len(extra))
    all_synset_nums |= extra

    log.info("Building synsets …")
    synset_objs = build_synsets(synsets_data, all_synset_nums)
    log.info("  %d synsets", len(synset_objs))

    log.info("Loading ILI map and building stub synsets for missing relation targets …")
    ili_map = load_ili_map(ILI_MAP_FILE)
    stub_objs = build_stub_synsets(synset_objs, ili_map)
    log.info("  %d stub synsets added", len(stub_objs))
    synset_objs.extend(stub_objs)

    log.info("Assembling lexicon …")
    lexicon = make_lexicon(
        id="wnja",
        label="Japanese Wordnet",
        language="ja",
        email="jwordnet@gmail.com",
        license="wordnet",
        version=VERSION,
        url="https://bond-lab.github.io/wnja/",
        citation=(
            "Hitoshi Isahara, Francis Bond, Kiyotaka Uchimoto, Masao Utiyama "
            "and Kyoko Kanzaki, Development of Japanese WordNet. "
            "In LREC-2008, Marrakech."
        ),
        entries=all_entries,
        synsets=synset_objs,
    )
    resource = make_lexical_resource([lexicon])

    log.info("Exporting to %s …", OUTPUT)
    lmf.dump(resource, OUTPUT)
    log.info("Done. %d entries, %d synsets written.", len(all_entries), len(synset_objs))


if __name__ == "__main__":
    main()
