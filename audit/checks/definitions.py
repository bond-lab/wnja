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

For DRIFT and WRONG the model also provides a suggested improved
Japanese definition, stored in the ``suggestion`` column.

Prompt styles
-------------
zero-shot   Task description only; no examples.
one-shot    One DRIFT example prepended to the user turn.
few-shot    Three labelled examples (one per class) prepended.

Results are written to AuditDB keyed on
(synset_id, 'definition', '', model, prompt_style)
so all combinations coexist in the same database.

Output format
-------------
Structured output via Pydantic schema — ``DefinitionCheck`` with a
``verdicts`` list. The Ollama schema enforcement means no format
instructions are needed in the prompt.
"""
from __future__ import annotations

import logging
import time
from typing import Literal

from pydantic import BaseModel

from audit.db import AuditDB
from audit.llm import Generator
from audit.loader import SynsetData

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic schema for structured output
# ---------------------------------------------------------------------------


class Verdict(BaseModel):
    id: str
    verdict: Literal["OK", "DRIFT", "WRONG"]
    note: str
    suggestion: str  # empty string for OK


class DefinitionCheck(BaseModel):
    verdicts: list[Verdict]


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

For DRIFT and WRONG, provide a suggested improved Japanese definition in the \
suggestion field. For OK, leave suggestion empty.\
"""

_ONE_SHOT_EXAMPLES = """\
Here is one labelled example to illustrate the verdict criteria:

  ID: wnja-00727002-n | POS: n | EN members: halfback
  EN: the position of a back on a football team, generally smaller in size than a fullback
  JA: フットボールチームの後ろのポジション
  → DRIFT: JA omits the key qualifier "generally smaller than a fullback"
  → Suggested: フットボールチームのバックのポジションで、一般的にフルバックより小柄な選手

Now evaluate the following synsets:
"""

_FEW_SHOT_EXAMPLES = """\
Here are three labelled examples to illustrate the verdict criteria:

  ID: wnja-07471246-n | POS: n | EN members: wrestling match
  EN: a match between wrestlers
  JA: レスラーの試合
  → OK: direct and complete translation

  ID: wnja-00727002-n | POS: n | EN members: halfback
  EN: the position of a back on a football team, generally smaller in size than a fullback
  JA: フットボールチームの後ろのポジション
  → DRIFT: JA omits the key qualifier "generally smaller than a fullback"
  → Suggested: フットボールチームのバックのポジションで、一般的にフルバックより小柄な選手

  ID: wnja-SYNTH-WRONG | POS: n | EN members: bank · riverbank
  EN: sloping land beside a body of water
  JA: 金融機関。預金、貸出、為替などの業務を行う
  → WRONG: JA describes a financial institution; EN describes a riverbank
  → Suggested: 水辺に沿った傾斜した土地

Now evaluate the following synsets:
"""

_FEW_SHOT_IDS = {"wnja-07471246-n", "wnja-00727002-n"}  # exclude from dev set


# ---------------------------------------------------------------------------
# Formatting and parsing
# ---------------------------------------------------------------------------

def _ntumc_id(wnja_id: str) -> str:
    return wnja_id.replace("wnja-", "ntumc-en-", 1)


def _format_user_turn(
    batch: list[tuple[str, SynsetData, SynsetData]],
    prompt_style: str,
) -> str:
    """Build the user-turn text for one batch."""
    parts: list[str] = []
    if prompt_style == "few-shot":
        parts.append(_FEW_SHOT_EXAMPLES)
    elif prompt_style == "one-shot":
        parts.append(_ONE_SHOT_EXAMPLES)
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
) -> dict[str, tuple[str, str, str | None]]:
    """Parse JSON model output into {wnja_id: (verdict, note, suggestion)}.

    Uses Pydantic validation on the JSON response from the model.
    """
    try:
        check = DefinitionCheck.model_validate_json(response)
    except Exception as exc:
        log.debug("Pydantic parse failed (%s); response snippet: %s", exc, response[:200])
        return {}

    results: dict[str, tuple[str, str, str | None]] = {}
    for v in check.verdicts:
        suggestion: str | None = v.suggestion if v.suggestion else None
        results[v.id] = (v.verdict, v.note, suggestion)

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

    Results are keyed by (synset_id, 'definition', '', model, prompt_style)
    so multiple models and prompt styles coexist in one database.

    Returns:
        (n_ok, n_drift, n_wrong, n_skipped) counts.
    """
    model = generator.model_id
    n_ok = n_drift = n_wrong = n_skipped = 0

    todo: list[tuple[str, SynsetData, SynsetData]] = []
    for wnja_id, ja in current_synsets.items():
        if db.is_done(wnja_id, "definition", model=model, prompt_style=prompt_style):
            n_skipped += 1
            continue
        if not ja.definitions:
            db.save_result(wnja_id, "definition", "", "SKIP",
                           model=model, prompt_style=prompt_style,
                           evidence="no Japanese definition")
            n_skipped += 1
            continue
        en = eng_synsets.get(_ntumc_id(wnja_id))
        if not en or not en.definitions:
            db.save_result(wnja_id, "definition", "", "SKIP",
                           model=model, prompt_style=prompt_style,
                           evidence="no English definition in ntumc-eng",
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

        parsed: dict[str, tuple[str, str, str | None]] = {}
        for attempt in range(retry_limit + 1):
            try:
                response = generator.chat(
                    system=_SYSTEM_PROMPT,
                    user=user_turn,
                    max_tokens=batch_size * 400,
                    schema=DefinitionCheck,
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
            log.warning("Raw response (first 500 chars):\n%s", response[:500])

        for wnja_id, _, _ in batch:
            if wnja_id in parsed:
                verdict, note, suggestion = parsed[wnja_id]
            else:
                verdict, note, suggestion = "SKIP", "parse failure — not in model output", None
            db.save_result(
                wnja_id, "definition", "", verdict,
                model=model, prompt_style=prompt_style,
                evidence=note, suggestion=suggestion, en_source="ntumc-eng",
            )
            if verdict == "OK":
                n_ok += 1
            elif verdict == "DRIFT":
                n_drift += 1
            elif verdict == "WRONG":
                n_wrong += 1
            else:
                n_skipped += 1

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
