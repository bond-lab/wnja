"""Stage 2: LLM-assisted lemma appropriateness check.

For each lexical entry in the current build, verifies that:
  1. The lemma (writtenForm) actually exists as a Japanese word — confirmed
     via web lookup on kotobank and/or ja.wiktionary.
  2. The lemma is semantically appropriate for its synset — checked by the
     LLM given the English definition, the Japanese lemma, and any dictionary
     excerpts retrieved in step 1.

Verdicts
--------
OK      Lemma exists and is appropriate for the synset.
DUBIOUS Lemma exists as a word, but the synset membership seems questionable.
MISSING Lemma not found on kotobank or wiktionary — possibly non-existent.
SKIP    No English definition available to compare against.

Results are stored per (synset_id, 'lemma', lemma_form, model) so that
every (synset, lemma) pair has its own row.

Prompt design
-------------
The system prompt describes the task once; each user turn contains a
batch of (synset, lemma, EN def, dictionary excerpt) tuples.
The LLM should respond with one line per entry in the same pipe-delimited
format used by the definition check.
"""
from __future__ import annotations

import logging
import re
import time

from audit.db import AuditDB
from audit.llm import Generator
from audit.loader import SynsetData
from audit.web_lookup import WebCache, lookup_lemma

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a computational linguistics expert auditing the Japanese WordNet (wnja).
For each entry you are given:
  - A synset ID and its English definition
  - A Japanese lemma (word form) claimed to belong to that synset
  - A dictionary excerpt for the lemma (may be empty if not found online)

Decide whether the Japanese lemma is an appropriate member of the synset.

Verdicts:
  OK      — lemma exists and is a correct/reasonable translation of the synset
  DUBIOUS — lemma exists as a word but seems wrong for this synset (wrong sense, register, etc.)
  MISSING — lemma does not appear to be a real Japanese word

Reply with EXACTLY one line per entry. No extra text:
  <synset_id> | <lemma> | OK | <brief note>
  <synset_id> | <lemma> | DUBIOUS | <brief note>
  <synset_id> | <lemma> | MISSING | <brief note>\
"""

_FEW_SHOT_EXAMPLES = """\
Here are three labelled examples:

  wnja-07471246-n | レスラー | OK | correct translation of "wrestler"
  EN: a person who engages in wrestling
  JA lemma: レスラー
  Dict: レスラー【wrestler】（名詞）プロレスまたはアマチュアレスリングの選手。

  wnja-01107715-v | 跳躍する | DUBIOUS | this synset is about tumbling/somersaults, not general jumping
  EN: do gymnastics, roll and turn skillfully
  JA lemma: 跳躍する
  Dict: 跳躍する（動詞）高く跳び上がること。

  wnja-SYNTH-MISSING | 磯巾着 | MISSING | marine anemone term placed in wrong synset; meaning unrelated
  EN: a person who follows or serves another
  JA lemma: 磯巾着
  Dict: （検索結果なし）

Now evaluate the following entries:
"""

_FEW_SHOT_IDS = {"wnja-07471246-n", "wnja-01107715-v"}

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_LINE_RE = re.compile(
    r"(wnja-\S+)\s*\|\s*(\S+)\s*\|\s*(OK|DUBIOUS|MISSING)\s*\|?\s*(.*)",
    re.IGNORECASE,
)


def _parse_response(
    response: str,
    expected: list[tuple[str, str]],  # [(synset_id, lemma), ...]
) -> dict[tuple[str, str], tuple[str, str]]:
    """Parse model output into {(synset_id, lemma): (verdict, note)}."""
    results: dict[tuple[str, str], tuple[str, str]] = {}
    for line in response.splitlines():
        m = _LINE_RE.search(line)
        if m:
            synset_id = m.group(1)
            lemma = m.group(2)
            verdict = m.group(3).upper()
            note = m.group(4).strip()
            results[(synset_id, lemma)] = (verdict, note)
    missing = [(s, l) for s, l in expected if (s, l) not in results]
    if missing:
        log.debug("Missing %d pairs in response: %s", len(missing), missing[:3])
    return results


# ---------------------------------------------------------------------------
# Batch formatting
# ---------------------------------------------------------------------------


def _format_user_turn(
    batch: list[tuple[str, str, str, str]],  # (synset_id, lemma, en_def, excerpt)
    prompt_style: str,
) -> str:
    """Build user-turn text for one batch."""
    parts: list[str] = []
    if prompt_style == "few-shot":
        parts.append(_FEW_SHOT_EXAMPLES)
    for i, (synset_id, lemma, en_def, excerpt) in enumerate(batch, 1):
        parts.append(
            f"{i}. ID: {synset_id} | Lemma: {lemma}\n"
            f"   EN: {en_def}\n"
            f"   Dict: {excerpt or '（検索結果なし）'}"
        )
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Main check
# ---------------------------------------------------------------------------


def run(
    current_synsets: dict[str, SynsetData],
    eng_synsets: dict[str, SynsetData],
    db: AuditDB,
    generator: Generator,
    web_cache: WebCache,
    *,
    prompt_style: str = "zero-shot",
    batch_size: int = 10,
    retry_limit: int = 2,
    use_kotobank: bool = True,
    use_wiktionary: bool = True,
) -> tuple[int, int, int, int]:
    """Run the lemma appropriateness check and write results to *db*.

    For each (synset, lemma) pair, first checks web sources, then asks the
    LLM whether the lemma is appropriate.

    Args:
        current_synsets: Synset data from the current wnja build.
        eng_synsets: Synset data from wn-ntumc-eng.xml (EN defs).
        db: Audit checkpoint database.
        generator: Configured Generator instance.
        web_cache: WebCache wrapping db.conn.
        prompt_style: 'zero-shot' or 'few-shot'.
        batch_size: (synset, lemma) pairs per LLM call.
        retry_limit: Max retries per batch on parse failure.
        use_kotobank: Whether to fetch from kotobank.jp.
        use_wiktionary: Whether to fetch from ja.wiktionary.org.

    Returns:
        (n_ok, n_dubious, n_missing, n_skipped) counts.
    """
    model = generator.model_id

    def _ntumc_id(wnja_id: str) -> str:
        return wnja_id.replace("wnja-", "ntumc-en-", 1)

    n_ok = n_dubious = n_missing = n_skipped = 0

    # Build list of (synset_id, lemma, en_def, excerpt) to process
    todo: list[tuple[str, str, str, str]] = []

    for wnja_id, synset in current_synsets.items():
        for lemma in sorted(synset.forms):
            if db.is_done(wnja_id, "lemma", item=lemma, model=model):
                n_skipped += 1
                continue
            en = eng_synsets.get(_ntumc_id(wnja_id))
            if not en or not en.definitions:
                db.save_result(
                    wnja_id, "lemma", lemma, "SKIP",
                    model=model, evidence="no English definition",
                )
                n_skipped += 1
                continue

            # Web lookup (cached)
            results = lookup_lemma(
                lemma, web_cache,
                use_kotobank=use_kotobank,
                use_wiktionary=use_wiktionary,
            )
            web_found = any(r.found for r in results.values())
            excerpts = []
            for source, r in results.items():
                if r.definitions:
                    excerpts.append(f"[{source}] " + "; ".join(r.definitions[:2]))
            excerpt = " | ".join(excerpts)[:300] if excerpts else ""

            # If definitively not found anywhere, skip LLM and mark MISSING
            if not web_found and (use_kotobank or use_wiktionary):
                evidence = "not found on " + (
                    "+".join(
                        s for s, r in results.items() if not r.found
                    )
                )
                db.save_result(
                    wnja_id, "lemma", lemma, "MISSING",
                    model=model, evidence=evidence,
                    source_url=next(
                        (r.url for r in results.values()), None
                    ),
                )
                n_missing += 1
                continue

            todo.append((wnja_id, lemma, en.definitions[0], excerpt))

    log.info(
        "Lemma check [%s, %s]: %d (synset, lemma) pairs to evaluate, %d skipped/pre-scored",
        model, prompt_style, len(todo), n_skipped + n_missing,
    )

    for batch_start in range(0, len(todo), batch_size):
        batch = todo[batch_start : batch_start + batch_size]
        expected = [(synset_id, lemma) for synset_id, lemma, _, _ in batch]
        user_turn = _format_user_turn(batch, prompt_style)

        parsed: dict[tuple[str, str], tuple[str, str]] = {}
        for attempt in range(retry_limit + 1):
            try:
                response = generator.chat(
                    system=_SYSTEM_PROMPT,
                    user=user_turn,
                    max_tokens=batch_size * 40,
                )
                parsed = _parse_response(response, expected)
            except Exception as exc:
                log.warning("LLM error (attempt %d): %s", attempt + 1, exc)
                time.sleep(2)
                continue
            if len(parsed) >= max(1, len(batch) // 2):
                break
            log.warning(
                "Batch %d: only %d/%d parsed (attempt %d), retrying",
                batch_start // batch_size, len(parsed), len(batch), attempt + 1,
            )

        for synset_id, lemma, en_def, excerpt in batch:
            key = (synset_id, lemma)
            if key in parsed:
                verdict, note = parsed[key]
            else:
                verdict, note = "DUBIOUS", "parse failure — not in model output"
            db.save_result(
                synset_id, "lemma", lemma, verdict,
                model=model, evidence=note,
            )
            if verdict == "OK":
                n_ok += 1
            elif verdict == "DUBIOUS":
                n_dubious += 1
            else:
                n_missing += 1

        batch_num = batch_start // batch_size + 1
        total_batches = (len(todo) + batch_size - 1) // batch_size
        if batch_num % 100 == 0 or batch_num == total_batches:
            log.info(
                "  batch %d/%d | OK=%d DUBIOUS=%d MISSING=%d",
                batch_num, total_batches, n_ok, n_dubious, n_missing,
            )

    log.info(
        "Lemma check complete: %d OK, %d DUBIOUS, %d MISSING, %d skipped",
        n_ok, n_dubious, n_missing, n_skipped,
    )
    return n_ok, n_dubious, n_missing, n_skipped
