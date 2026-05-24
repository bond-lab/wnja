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
zero-shot   Task description and format only; no examples.
one-shot    One DRIFT example prepended to the user turn.
few-shot    Three labelled examples (one per class) prepended.

Results are written to AuditDB keyed on
(synset_id, 'definition', '', model, prompt_style)
so all combinations coexist in the same database.

Output format
-------------
One pipe-delimited line per synset::

    <ID> | OK | <brief note>
    <ID> | DRIFT | <brief note> | SUGGESTED: <improved Japanese definition>
    <ID> | WRONG | <brief note> | SUGGESTED: <improved Japanese definition>

Thinking-mode models (Qwen3) prepend a ``<think>…</think>`` block which
is stripped before parsing.  A cleanup pass retries any synsets whose
output could not be parsed, running them one at a time.
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

For DRIFT and WRONG, also provide a suggested improved Japanese definition.

Reply with EXACTLY one line per synset. No extra text, no preamble:
  <ID> | OK | <brief note in English>
  <ID> | DRIFT | <brief note in English> | SUGGESTED: <improved Japanese definition>
  <ID> | WRONG | <brief note in English> | SUGGESTED: <improved Japanese definition>\
"""

_ONE_SHOT_EXAMPLES = """\
Here is one labelled example to illustrate the verdict criteria:

  wnja-00727002-n | DRIFT | JA omits the key qualifier "generally smaller than a fullback" | SUGGESTED: フットボールチームのバックのポジションで、一般的にフルバックより小柄な選手
  EN: the position of a back on a football team, generally smaller in size than a fullback
  JA: フットボールチームの後ろのポジション

Now evaluate the following synsets using the same format:
"""

_FEW_SHOT_EXAMPLES = """\
Here are three labelled examples to illustrate the verdict criteria:

  wnja-07471246-n | OK | direct and complete translation ("レスラーの試合" = "a match between wrestlers")
  EN: a match between wrestlers
  JA: レスラーの試合

  wnja-00727002-n | DRIFT | JA omits the key qualifier "generally smaller than a fullback" | SUGGESTED: フットボールチームのバックのポジションで、一般的にフルバックより小柄な選手
  EN: the position of a back on a football team, generally smaller in size than a fullback
  JA: フットボールチームの後ろのポジション

  wnja-SYNTH-WRONG | WRONG | JA describes a financial institution; EN describes a riverbank | SUGGESTED: 水辺に沿った傾斜した土地
  EN: sloping land beside a body of water
  JA: 金融機関。預金、貸出、為替などの業務を行う

Now evaluate the following synsets using the same format:
"""

_FEW_SHOT_IDS = {"wnja-07471246-n", "wnja-00727002-n"}  # exclude from dev set


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

# Primary: pipe-delimited with optional suggestion.
# Handles optional leading number/bullet and markdown bold markers.
# wnja-XXXXXXXX-n | OK | note
# wnja-XXXXXXXX-n | DRIFT | note | SUGGESTED: 改善された定義
_LINE_RE = re.compile(
    r"(wnja-[\w-]+)\s*\|\s*(OK|DRIFT|WRONG)\s*\|\s*(.*?)(?:\|\s*SUGGESTED[：:]?\s*(.+))?$",
    re.IGNORECASE,
)

# Fallback for verbose block format (thinking-mode models that ignore the format):
#   ID: wnja-XXXXXXXX-n  ...  Verdict: OK.  [Suggested: ...]
_BLOCK_ID_RE = re.compile(r"\bID:\s*(wnja-[\w-]+)", re.IGNORECASE)
_BLOCK_VERDICT_RE = re.compile(r"\bVerdict:\s*(OK|DRIFT|WRONG)", re.IGNORECASE)
_BLOCK_SUGGEST_RE = re.compile(r"\bSuggested?[：:]?\s*(.+)", re.IGNORECASE)

# Thinking block patterns
_QWEN_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_GEMMA_THINK_RE = re.compile(r"<\|channel\s*>thought.*?(?=<\|channel\s*>|\Z)", re.DOTALL)


def _ntumc_id(wnja_id: str) -> str:
    return wnja_id.replace("wnja-", "ntumc-en-", 1)


def _strip_thinking(text: str) -> str:
    """Remove thinking blocks, returning the final response text."""
    stripped = _QWEN_THINK_RE.sub("", text)
    stripped = _GEMMA_THINK_RE.sub("", stripped).strip()
    return stripped or text


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
    """Parse model output into {wnja_id: (verdict, note, suggestion)}.

    Tries pipe-delimited format first, then falls back to verbose block format
    for any IDs still missing.  Thinking blocks are stripped before parsing.
    """
    results: dict[str, tuple[str, str, str | None]] = {}
    final_text = _strip_thinking(response)

    # Pass 1: pipe-delimited (primary format)
    for line in final_text.splitlines():
        m = _LINE_RE.search(line)
        if m:
            syn_id = m.group(1).rstrip("*:.,")  # strip stray markdown/punctuation
            note = m.group(3).strip()
            suggestion: str | None = m.group(4).strip() if m.group(4) else None
            results[syn_id] = (m.group(2).upper(), note, suggestion)

    # Pass 2: block format for any IDs still missing
    missing_set = {eid for eid in expected_ids if eid not in results}
    if missing_set:
        current_id: str | None = None
        current_verdict: str | None = None
        current_suggestion: str | None = None
        for line in response.splitlines():
            id_m = _BLOCK_ID_RE.search(line)
            if id_m:
                if current_id and current_verdict and current_id in missing_set:
                    results[current_id] = (current_verdict, "from block format", current_suggestion)
                    missing_set.discard(current_id)
                current_id = id_m.group(1).rstrip("*:.,")
                current_verdict = None
                current_suggestion = None
            if current_id and current_id in missing_set:
                sug_m = _BLOCK_SUGGEST_RE.search(line)
                if sug_m:
                    current_suggestion = sug_m.group(1).strip()
                v_m = _BLOCK_VERDICT_RE.search(line)
                if v_m:
                    current_verdict = v_m.group(1).upper()
        if current_id and current_verdict and current_id in missing_set:
            results[current_id] = (current_verdict, "from block format", current_suggestion)

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

    After the main batch loop, a cleanup pass retries any synsets whose
    output could not be parsed, submitting them one at a time.

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

    # Track parse failures for the cleanup pass
    parse_failed: list[tuple[str, SynsetData, SynsetData]] = []

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

        batch_by_id = {wnja_id: (ja, en) for wnja_id, ja, en in batch}
        for wnja_id, _, _ in batch:
            if wnja_id in parsed:
                verdict, note, suggestion = parsed[wnja_id]
            else:
                verdict, note, suggestion = "SKIP", "parse failure — not in model output", None
                parse_failed.append((wnja_id, *batch_by_id[wnja_id]))
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

    # Cleanup pass: retry parse failures one at a time
    if parse_failed:
        log.info(
            "Cleanup pass: retrying %d parse-failed synsets individually",
            len(parse_failed),
        )
        recovered = 0
        for wnja_id, ja, en in parse_failed:
            user_turn = _format_user_turn([(wnja_id, ja, en)], prompt_style)
            parsed1: dict[str, tuple[str, str, str | None]] = {}
            for attempt in range(retry_limit + 1):
                try:
                    response = generator.chat(
                        system=_SYSTEM_PROMPT,
                        user=user_turn,
                        max_tokens=2000,
                    )
                    parsed1 = _parse_response(response, [wnja_id])
                except Exception as exc:
                    log.warning("Cleanup LLM error (attempt %d): %s", attempt + 1, exc)
                    time.sleep(2)
                    continue
                if wnja_id in parsed1:
                    break
                log.warning("Cleanup attempt %d: still no parse for %s", attempt + 1, wnja_id)
                log.warning("Raw response (first 500 chars):\n%s", response[:500])

            if wnja_id in parsed1:
                verdict, note, suggestion = parsed1[wnja_id]
                db.save_result(
                    wnja_id, "definition", "", verdict,
                    model=model, prompt_style=prompt_style,
                    evidence=note, suggestion=suggestion, en_source="ntumc-eng",
                )
                n_skipped -= 1
                if verdict == "OK":
                    n_ok += 1
                elif verdict == "DRIFT":
                    n_drift += 1
                elif verdict == "WRONG":
                    n_wrong += 1
                else:
                    n_skipped += 1
                recovered += 1
            else:
                log.warning("Cleanup: giving up on %s", wnja_id)

        log.info("Cleanup pass: recovered %d / %d", recovered, len(parse_failed))

    log.info(
        "Definition check complete: %d OK, %d DRIFT, %d WRONG, %d skipped",
        n_ok, n_drift, n_wrong, n_skipped,
    )
    return n_ok, n_drift, n_wrong, n_skipped
