"""Stage 1: batched LLM definition accuracy check.

For each synset, compares the Japanese definition in the current build
against the English source definition from wn-ntumc-eng.xml (or omw-en:2.0
as a fallback).  Synsets are batched 10 per prompt for throughput.

ID mapping: ``wnja-XXXXXXXX-p`` ↔ ``ntumc-en-XXXXXXXX-p`` (prefix swap).

Verdicts:
    OK      Faithful translation; minor paraphrasing is fine.
    DRIFT   Related but shifted: too narrow/broad, imprecise emphasis.
    WRONG   Substantially different meaning; likely a translation error
            or the wrong English synset was used as source.
    SKIP    Synset has no Japanese or English definition to compare.

Results are written to the AuditDB checkpoint table keyed on
(synset_id, 'definition', ''), so the run can be interrupted and resumed.
"""
from __future__ import annotations

import logging
import re
import time

from audit.db import AuditDB
from audit.llm import Generator
from audit.loader import SynsetData

log = logging.getLogger(__name__)

# Matches one output line from the model:  wnja-XXXXXXXX-p | VERDICT | note
_LINE_RE = re.compile(
    r"(wnja-\S+)\s*\|\s*(OK|DRIFT|WRONG)\s*\|?\s*(.*)",
    re.IGNORECASE,
)

_PROMPT_HEADER = """\
You are auditing Japanese WordNet definitions. For each numbered synset below,
decide whether the Japanese definition (JA) accurately conveys the same meaning
as the English definition (EN).

Verdicts:
  OK    — faithful translation, even if not word-for-word
  DRIFT — related but shifted: too narrow, too broad, or imprecise
  WRONG — substantially different meaning

Reply with EXACTLY one line per synset, no extra text:
  <ID> | OK | <brief note in English>
  <ID> | DRIFT | <brief note in English>
  <ID> | WRONG | <brief note in English>

Synsets:
"""


def _ntumc_id(wnja_id: str) -> str:
    """Convert a wnja synset id to its ntumc-en counterpart."""
    return wnja_id.replace("wnja-", "ntumc-en-", 1)


def _format_batch(batch: list[tuple[str, SynsetData, SynsetData]]) -> str:
    """Render a list of (wnja_id, ja_data, en_data) triples as prompt text."""
    lines = [_PROMPT_HEADER]
    for i, (wnja_id, ja, en) in enumerate(batch, 1):
        en_members = sorted(en.forms)[:4]
        members_str = " · ".join(en_members) if en_members else "—"
        lines.append(
            f"{i}. ID: {wnja_id} | POS: {ja.pos} | EN members: {members_str}\n"
            f"   EN: {en.definitions[0]}\n"
            f"   JA: {ja.definitions[0]}\n"
        )
    return "\n".join(lines)


def _parse_response(
    response: str,
    expected_ids: list[str],
) -> dict[str, tuple[str, str]]:
    """Parse model output into {wnja_id: (verdict, evidence)}.

    Args:
        response: Raw model output string.
        expected_ids: The wnja ids we asked about; used to warn on missing lines.

    Returns:
        Dict of parsed results. Missing or unparseable lines are omitted
        (caller should retry or log a warning).
    """
    results: dict[str, tuple[str, str]] = {}
    for line in response.splitlines():
        m = _LINE_RE.search(line)
        if m:
            wnja_id, verdict, note = m.group(1), m.group(2).upper(), m.group(3).strip()
            results[wnja_id] = (verdict, note)

    missing = [eid for eid in expected_ids if eid not in results]
    if missing:
        log.debug("Missing %d ids from response: %s", len(missing), missing[:5])

    return results


def run(
    current_synsets: dict[str, SynsetData],
    eng_synsets: dict[str, SynsetData],
    db: AuditDB,
    generator: Generator,
    batch_size: int = 10,
    retry_limit: int = 2,
) -> tuple[int, int, int, int]:
    """Run the definition accuracy check and write results to *db*.

    Args:
        current_synsets: Synset data from the current wnja build (JA definitions).
        eng_synsets: Synset data from wn-ntumc-eng.xml (EN definitions), keyed
            by ntumc-en-XXXXXXXX-p ids.
        db: Audit checkpoint database; already-checked synsets are skipped.
        generator: Configured LLM generator instance.
        batch_size: Synsets per prompt (default 10).
        retry_limit: Max retries on parse failure per batch (default 2).

    Returns:
        (n_ok, n_drift, n_wrong, n_skipped) counts.
    """
    n_ok = n_drift = n_wrong = n_skipped = 0

    # Build list of synsets to check (those not already done, with both definitions)
    todo: list[tuple[str, SynsetData, SynsetData]] = []
    for wnja_id, ja in current_synsets.items():
        if db.is_done(wnja_id, "definition"):
            n_skipped += 1
            continue
        if not ja.definitions:
            db.save_result(wnja_id, "definition", "", "SKIP",
                           evidence="no Japanese definition")
            n_skipped += 1
            continue
        en = eng_synsets.get(_ntumc_id(wnja_id))
        if not en or not en.definitions:
            db.save_result(wnja_id, "definition", "", "SKIP",
                           evidence="no English definition in ntumc-eng",
                           en_source="ntumc-eng")
            n_skipped += 1
            continue
        todo.append((wnja_id, ja, en))

    log.info("Definition check: %d synsets to process, %d already done",
             len(todo), n_skipped)

    for batch_start in range(0, len(todo), batch_size):
        batch = todo[batch_start : batch_start + batch_size]
        expected_ids = [wnja_id for wnja_id, _, _ in batch]
        prompt = _format_batch(batch)

        parsed: dict[str, tuple[str, str]] = {}
        for attempt in range(retry_limit + 1):
            try:
                response = generator.generate(prompt, max_tokens=batch_size * 25)
                parsed = _parse_response(response, expected_ids)
            except Exception as e:
                log.warning("LLM error on attempt %d: %s", attempt + 1, e)
                time.sleep(2)
                continue

            # Accept if we got at least half the expected ids
            if len(parsed) >= len(batch) // 2:
                break
            log.warning(
                "Batch %d: only %d/%d ids parsed on attempt %d, retrying",
                batch_start // batch_size, len(parsed), len(batch), attempt + 1,
            )

        for wnja_id, _, en in batch:
            verdict, note = parsed.get(wnja_id, ("WRONG", "parse failure — defaulted"))
            db.save_result(
                wnja_id, "definition", "", verdict,
                evidence=note,
                en_source="ntumc-eng",
            )
            if verdict == "OK":
                n_ok += 1
            elif verdict == "DRIFT":
                n_drift += 1
            else:
                n_wrong += 1

        if (batch_start // batch_size) % 100 == 0:
            log.info(
                "  batch %d/%d | OK=%d DRIFT=%d WRONG=%d",
                batch_start // batch_size + 1,
                (len(todo) + batch_size - 1) // batch_size,
                n_ok, n_drift, n_wrong,
            )

    log.info(
        "Definition check complete: %d OK, %d DRIFT, %d WRONG, %d skipped",
        n_ok, n_drift, n_wrong, n_skipped,
    )
    return n_ok, n_drift, n_wrong, n_skipped
