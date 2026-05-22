"""Stage 0: programmatic example↔lemma check (no LLM required).

For each example sentence in *example_synsets*, checks whether any
writtenForm from the corresponding synset in *current_synsets* appears in
the text.  Match strategy (in order, stopping at first success):

1. Exact substring search over all writtenForms → leftmost match.
2. MeCab tokenization: surface form or orthographic base form in the form set.
3. No match → MISMATCH.

Results are written to the AuditDB checkpoint table so the run can be
interrupted and resumed without reprocessing completed rows.
"""
from __future__ import annotations

import logging

from audit.db import AuditDB
from audit.loader import SynsetData
from audit.tokenizer import Tokenizer

log = logging.getLogger(__name__)


def _find_exact(
    example: str, forms: set[str]
) -> tuple[str, int, int] | None:
    """Return (matched_form, start, end) for the leftmost exact substring match.

    Args:
        example: The example sentence to search.
        forms: Set of writtenForms to look for.

    Returns:
        (matched_form, start, end) tuple, or None if no form is found.
    """
    best: tuple[str, int, int] | None = None
    for form in forms:
        idx = example.find(form)
        if idx != -1 and (best is None or idx < best[1]):
            best = (form, idx, idx + len(form))
    return best


def _find_via_tokens(
    example: str, forms: set[str], tokenizer: Tokenizer
) -> tuple[str, int, int] | None:
    """Return (matched_form, start, end) via tokenizer surface/base matching.

    Checks each token's surface form first, then its base form.  Returns
    the position of the token in the source string in both cases (the
    surface span, not the base form span).

    Args:
        example: The example sentence to tokenize and search.
        forms: Set of writtenForms to match against.
        tokenizer: Language-specific tokenizer instance.

    Returns:
        (matched_form, start, end) tuple, or None if no token matches.
    """
    for tok in tokenizer.tokenize(example):
        if tok.surface in forms:
            return (tok.surface, tok.start, tok.end)
        if tok.base in forms:
            return (tok.base, tok.start, tok.end)
    return None


def run(
    current_synsets: dict[str, SynsetData],
    example_synsets: dict[str, SynsetData],
    db: AuditDB,
    tokenizer: Tokenizer,
) -> tuple[int, int, int]:
    """Run the example↔lemma check and write results to *db*.

    Args:
        current_synsets: Synset data with lemma forms from the current build.
        example_synsets: Synset data that carries the example sentences
            (may be the same dict or a separate older version).
        db: Audit checkpoint database; already-checked rows are skipped.
        tokenizer: Language-specific tokenizer for base-form fallback.

    Returns:
        (n_ok, n_mismatch, n_skipped) counts.
    """
    n_ok = n_mismatch = n_skipped = 0

    for ss_id, ex_data in example_synsets.items():
        if not ex_data.examples:
            continue

        current = current_synsets.get(ss_id)
        if current is None:
            log.debug("Synset %s has examples but is absent from current build", ss_id)
            n_skipped += len(ex_data.examples)
            continue

        forms = current.forms
        if not forms:
            log.debug("Synset %s has no forms in current build", ss_id)

        for example in ex_data.examples:
            if db.is_done(ss_id, "example", example):
                n_skipped += 1
                continue

            match = _find_exact(example, forms) or _find_via_tokens(
                example, forms, tokenizer
            )

            if match:
                matched_form, start, end = match
                db.save_result(
                    ss_id,
                    "example",
                    example,
                    "OK",
                    evidence=f"'{matched_form}' at [{start}:{end}]",
                    matched_lemma=matched_form,
                    match_start=start,
                    match_end=end,
                )
                n_ok += 1
            else:
                sample = sorted(forms)[:5]
                evidence = (
                    f"none of {sample!r}{'...' if len(forms) > 5 else ''} "
                    f"found in example"
                )
                db.save_result(ss_id, "example", example, "MISMATCH", evidence=evidence)
                n_mismatch += 1

    log.info(
        "Example check: %d OK, %d MISMATCH, %d skipped",
        n_ok, n_mismatch, n_skipped,
    )
    return n_ok, n_mismatch, n_skipped
