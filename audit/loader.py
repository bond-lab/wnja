"""Parse wnja LMF XML files into audit data structures."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET


@dataclass
class SynsetData:
    """All audit-relevant data for one synset."""

    synset_id: str
    pos: str
    definitions: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    # All writtenForms of all LexicalEntries with a Sense pointing here
    forms: set[str] = field(default_factory=set)


def load_lmf(path: Path) -> dict[str, SynsetData]:
    """Parse a WN-LMF XML file and return {synset_id: SynsetData}.

    Collects definitions and examples from <Synset> elements, and all
    writtenForms (Lemma + Form) from <LexicalEntry> elements, associating
    the latter with every synset reachable via <Sense>.

    Args:
        path: Path to a WN-LMF XML file (plain or .xz).

    Returns:
        Dict mapping synset id strings to SynsetData instances.

    Raises:
        ValueError: If the file contains no <Lexicon> element.
    """
    tree = ET.parse(path)
    root = tree.getroot()
    lex = root.find("Lexicon")
    if lex is None:
        raise ValueError(f"No <Lexicon> element found in {path}")

    synsets: dict[str, SynsetData] = {}

    for ss_elem in lex.findall("Synset"):
        ss_id = ss_elem.get("id", "")
        pos = ss_elem.get("partOfSpeech", "")
        data = SynsetData(synset_id=ss_id, pos=pos)
        for d in ss_elem.findall("Definition"):
            if d.text:
                data.definitions.append(d.text.strip())
        for e in ss_elem.findall("Example"):
            if e.text:
                data.examples.append(e.text.strip())
        synsets[ss_id] = data

    for entry_elem in lex.findall("LexicalEntry"):
        lemma_elem = entry_elem.find("Lemma")
        if lemma_elem is None:
            continue
        all_forms: set[str] = {lemma_elem.get("writtenForm", "")}
        for form_elem in entry_elem.findall("Form"):
            wf = form_elem.get("writtenForm", "")
            if wf:
                all_forms.add(wf)
        all_forms.discard("")
        for sense_elem in entry_elem.findall("Sense"):
            ss_id = sense_elem.get("synset", "")
            if ss_id in synsets:
                synsets[ss_id].forms.update(all_forms)

    return synsets
