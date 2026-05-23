"""Stage 1: batched LLM definition accuracy check.

For each synset, compares the Japanese definition in the current build
against the English source definition from wn-ntumc-eng.xml.

ID mapping: ``wnja-XXXXXXXX-p`` ↔ ``ntumc-en-XXXXXXXX-p`` (prefix swap).

Verdicts
--------
OK      Faithful translation; minor paraphrasing is acceptable.
DRIFT   Related but shifted: too narrow/broad, or imprecise emphasis.
WRONG   Substantially different meaning; likely a mistranslation or the
        wrong English synset was used as the source.
SKIP    No Japanese or English definition available to compare.

Prompt styles
-------------
zero-shot   Task description and format only; no examples.
few-shot    Three labelled examples (one per class) prepended to the user
            turn before the synsets to evaluate.

Results are written to AuditDB keyed on (synset_id, 'definition', '', model)
so multiple models and prompt styles can coexist in the same database.
"""
from __future__ import annotations

import logging
import re
import time

from audit.db import AuditDB
from audit.llm import Generator
from audit.loader import SynsetData

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt components
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a computational linguistics expert auditing definitions in the Japanese \
WordNet (wnja). For each synset you are given an English source definition (EN) \
and a Japanese definition (JA). Decide whether the JA accurately conveys the \
same meaning as the EN.

Verdicts:
  OK    — faithful translation, even if not word-for-word
  DRIFT — related but shifted: too narrow, too broad, or emphasises the wrong aspect
  WRONG — substantially different meaning; likely a mistranslation or wrong source

Reply with EXACTLY one line per synset. No extra text, no preamble:
  <ID> | OK | <brief note in English>
  <ID> | DRIFT | <brief note in English>
  <ID> | WRONG | <brief note in English>\
"""

# Three unambiguous few-shot examples (one per class), excluded from dev set.
# Kept outside the batched synsets so they appear once per call, not once per synset.
_FEW_SHOT_EXAMPLES = """\
Here are three labelled examples to illustrate the verdict criteria:

  wnja-07471246-n | OK | direct and complete translation ("レスラーの試合" = "a match between wrestlers")
  EN: a match between wrestlers
  JA: レスラーの試合

  wnja-00727002-n | DRIFT | JA omits the key qualifier "generally smaller than a fullback"
  EN: the position of a back on a football team, generally smaller in size than a fullback
  JA: フットボールチームの後ろのポジション

  wnja-SYNTH-WRONG | WRONG | JA describes a financial institution; EN describes a riverbank
  EN: sloping land beside a body of water
  JA: 金融機関。預金、貸出、為替などの業務を行う

Now evaluate the following synsets using the same format:
"""

_FEW_SHOT_IDS = {"wnja-07471246-n", "wnja-00727002-n"}  # exclude from dev set

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_LINE_RE = re.compile(
    r"(wnja-\S+)\s*\|\s*(OK|DRIFT|WRONG)\s*\|?\s*(.*)",
    re.IGNORECASE,
)


def _ntumc_id(wnja_id: str) -> str:
    return wnja_id.replace("wnja-", "ntumc-en-", 1)


def _format_user_turn(
    batch: list[tuple[str, SynsetData, SynsetData]],
    prompt_style: str,
) -> str:
    """Build the user-turn text for one batch.

    Args:
        batch: List of (wnja_id, ja_data, en_data) triples.
        prompt_style: 'zero-shot' or 'few-shot'.

    Returns:
        User-turn string to pass to Generator.chat().
    """
    parts: list[str] = []
    if prompt_style == "few-shot":
        parts.append(_FEW_SHOT_EXAMPLES)
    for i, (wnja_id, ja, en) in enumerate(batch, 1):
        en_members = sorted(en.forms)[:4]
        members_str = " · ".join(en_members) if en_members else "—"
        parts.append(
            f"{i}. ID: {wnja_id} | POS: {ja.pos} | EN members: {members_str}\n"
            f"   EN: {en.definitions[0]}\n"
            f"   JA: {ja.definitions[0]}"
        )
    return "\n\n".join(parts)


def _parse_response(
    response: str,
    expected_ids: list[str],
) -> dict[str, tuple[str, str]]:
    """Parse model output into {wnja_id: (verdict, evidence)}.

    Args:
        response: Raw model output string.
        expected_ids: IDs we asked about; used for debug logging on misses.

    Returns:
        Dict of parsed results; missing/unparseable lines are omitted.
    """
    results: dict[str, tuple[str, str]] = {}
    for line in response.splitlines():
        m = _LINE_RE.search(line)
        if m:
            wnja_id = m.group(1)
            verdict = m.group(2).upper()
            note = m.group(3).strip()
            results[wnja_id] = (verdict, note)
    missing = [eid for eid in expected_ids if eid not in results]
    if missing:
        log.debug("Missing %d ids in response: %s", len(missing), missing[:5])
    return results


# ---------------------------------------------------------------------------
# Main check
# ---------------------------------------------------------------------------

def run(
    current_synsets: dict[str, SynsetData],
    eng_synsets: dict[str, SynsetData],
    db: AuditDB,
    generator: Generator,
    *,
    prompt_style: str = "zero-shot",
    batch_size: int = 10,
    retry_limit: int = 2,
) -> tuple[int, int, int, int]:
    """Run the definition accuracy check and write results to *db*.

    Args:
        current_synsets: Synset data from the current wnja build (JA defs).
        eng_synsets: Synset data from wn-ntumc-eng.xml (EN defs), keyed by
            ntumc-en-XXXXXXXX-p ids.
        db: Audit checkpoint database.
        generator: Configured Generator instance.
        prompt_style: 'zero-shot' or 'few-shot'.
        batch_size: Synsets per LLM call.
        retry_limit: Max retries per batch on parse failure.

    Returns:
        (n_ok, n_drift, n_wrong, n_skipped) counts.
    """
    model = generator.model_id
    n_ok = n_drift = n_wrong = n_skipped = 0

    todo: list[tuple[str, SynsetData, SynsetData]] = []
    for wnja_id, ja in current_synsets.items():
        if db.is_done(wnja_id, "definition", model=model):
            n_skipped += 1
            continue
        if not ja.definitions:
            db.save_result(wnja_id, "definition", "", "SKIP",
                           model=model, evidence="no Japanese definition")
            n_skipped += 1
            continue
        en = eng_synsets.get(_ntumc_id(wnja_id))
        if not en or not en.definitions:
            db.save_result(wnja_id, "definition", "", "SKIP",
                           model=model, evidence="no English definition in ntumc-eng",
                           en_source="ntumc-eng")
            n_skipped += 1
            continue
        todo.append((wnja_id, ja, en))

    log.info(
        "Definition check [%s, %s]: %d synsets to process, %d already done",
        model, prompt_style, len(todo), n_skipped,
    )

    for batch_start in range(0, len(todo), batch_size):
        batch = todo[batch_start : batch_start + batch_size]
        expected_ids = [wnja_id for wnja_id, _, _ in batch]
        user_turn = _format_user_turn(batch, prompt_style)

        parsed: dict[str, tuple[str, str]] = {}
        for attempt in range(retry_limit + 1):
            try:
                response = generator.chat(
                    system=_SYSTEM_PROMPT,
                    user=user_turn,
                    max_tokens=batch_size * 30,
                )
                parsed = _parse_response(response, expected_ids)
            except Exception as exc:
                log.warning("LLM error (attempt %d): %s", attempt + 1, exc)
                time.sleep(2)
                continue
            if len(parsed) >= max(1, len(batch) // 2):
                break
            log.warning(
                "Batch %d: only %d/%d ids parsed (attempt %d), retrying",
                batch_start // batch_size, len(parsed), len(batch), attempt + 1,
            )

        for wnja_id, _, _ in batch:
            if wnja_id in parsed:
                verdict, note = parsed[wnja_id]
            else:
                verdict, note = "WRONG", "parse failure — not in model output"
            db.save_result(
                wnja_id, "definition", "", verdict,
                model=model, evidence=note, en_source="ntumc-eng",
            )
            if verdict == "OK":
                n_ok += 1
            elif verdict == "DRIFT":
                n_drift += 1
            else:
                n_wrong += 1

        batch_num = batch_start // batch_size + 1
        total_batches = (len(todo) + batch_size - 1) // batch_size
        if batch_num % 100 == 0 or batch_num == total_batches:
            log.info(
                "  batch %d/%d | OK=%d DRIFT=%d WRONG=%d",
                batch_num, total_batches, n_ok, n_drift, n_wrong,
            )

    log.info(
        "Definition check complete: %d OK, %d DRIFT, %d WRONG, %d skipped",
        n_ok, n_drift, n_wrong, n_skipped,
    )
    return n_ok, n_drift, n_wrong, n_skipped
