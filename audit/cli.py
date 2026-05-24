"""Command-line entry point for the wnja audit pipeline.

Usage examples
--------------
# Stage 0: example↔lemma check (Japanese)
uv run python -m audit.cli \\
    --lmf wnja-2.0.xml \\
    --example-lmf tmp/wnja.1.9.0.xml \\
    --lang jpn \\
    --check examples \\
    --db audit.db

# Stage 0 for Indonesian (space tokenizer, no fugashi needed)
uv run python -m audit.cli \\
    --lmf wn-ntumc-ind.xml \\
    --lang ind \\
    --check examples \\
    --db audit_ind.db
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from audit.db import AuditDB
from audit.loader import load_lmf
from audit.tokenizer import get_tokenizer

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
log = logging.getLogger("audit")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Quality audit for wnja and related wordnets.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--lmf",
        type=Path,
        default=Path("wnja-2.0.xml"),
        help="Current build LMF XML (source of lemma forms and definitions).",
    )
    p.add_argument(
        "--example-lmf",
        type=Path,
        default=None,
        help=(
            "LMF XML that carries example sentences. "
            "Defaults to --lmf when omitted (examples in the same file)."
        ),
    )
    p.add_argument(
        "--ref-lmf",
        type=Path,
        default=None,
        help="NTU-MC English LMF for Stage 1 definition check (e.g. wn-ntumc-eng.xml).",
    )
    p.add_argument(
        "--lang",
        default="jpn",
        help="ISO 639-3 language code of the target wordnet.",
    )
    p.add_argument(
        "--check",
        choices=["examples", "definitions", "lemmas", "all"],
        default="examples",
        help="Which check(s) to run.",
    )
    p.add_argument(
        "--db",
        type=Path,
        default=Path("audit.db"),
        help="SQLite checkpoint database path.",
    )
    p.add_argument(
        "--model",
        default="mlx-community/Qwen3-32B-4bit",
        help="MLX model repo id, e.g. 'mlx-community/Qwen3-32B-4bit'.",
    )
    p.add_argument(
        "--prompt-style",
        choices=["zero-shot", "one-shot", "few-shot"],
        default="zero-shot",
        help="Prompt style for Stage 1 definition check.",
    )
    p.add_argument(
        "--run-name",
        default=None,
        help="Short display name for this run (stored in the runs table).",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Synsets per LLM prompt for Stage 1.",
    )
    p.add_argument(
        "--report",
        action="store_true",
        help="Print a summary table after running checks.",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write results to this TSV path (used with --report).",
    )
    p.add_argument(
        "--check-type",
        default=None,
        help="Filter TSV output to this check type (used with --out).",
    )
    p.add_argument(
        "--synset-file",
        type=Path,
        default=None,
        help=(
            "TSV file with a 'synset_id' column; restrict all checks to those "
            "synset IDs only. Useful for running just the dev set, e.g. "
            "--synset-file audit/dev_set.tsv"
        ),
    )
    p.add_argument(
        "--pos",
        default=None,
        help="Restrict definitions check to this part-of-speech (e.g. 'n', 'v', 'a', 'r').",
    )
    p.add_argument(
        "--prior-wrong-model",
        default=None,
        help=(
            "Stage-2 filter: only process synsets that this model flagged WRONG. "
            "Must be combined with --prior-wrong-style."
        ),
    )
    p.add_argument(
        "--prior-wrong-style",
        default=None,
        help="Prompt style of the --prior-wrong-model run to use as stage-2 filter.",
    )
    return p.parse_args(argv)


def _load_synset_filter(path: Path) -> set[str] | None:
    """Read synset IDs from a TSV with a 'synset_id' column.

    Returns a set of IDs, or None if path is None.
    """
    import csv
    ids: set[str] = set()
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            sid = row.get("synset_id", "").strip()
            if sid:
                ids.add(sid)
    log.info("Synset filter: %d IDs from %s", len(ids), path)
    return ids


def main(argv: list[str] | None = None) -> None:
    """Run the requested audit stage(s)."""
    args = _parse_args(argv)

    if not args.lmf.exists():
        log.error("LMF file not found: %s", args.lmf)
        sys.exit(1)

    synset_filter: set[str] | None = None
    if args.synset_file:
        if not args.synset_file.exists():
            log.error("Synset file not found: %s", args.synset_file)
            sys.exit(1)
        synset_filter = _load_synset_filter(args.synset_file)

    db = AuditDB(args.db)
    log.info("Checkpoint DB: %s", args.db)

    run_examples = args.check in ("examples", "all")
    run_defs = args.check in ("definitions", "all")
    run_lemmas = args.check in ("lemmas", "all")

    if run_examples:
        from audit.checks.examples import run as run_example_check

        log.info("Loading current build from %s …", args.lmf)
        current = load_lmf(args.lmf)
        log.info("  %d synsets loaded", len(current))

        example_src = args.example_lmf or args.lmf
        if example_src == args.lmf:
            examples = current
        else:
            log.info("Loading example source from %s …", example_src)
            examples = load_lmf(example_src)
            log.info("  %d synsets loaded", len(examples))

        tokenizer = get_tokenizer(args.lang)
        if synset_filter:
            current = {k: v for k, v in current.items() if k in synset_filter}
            examples = {k: v for k, v in examples.items() if k in synset_filter}
            log.info("  filtered to %d synsets", len(current))
        run_example_check(current, examples, db, tokenizer)

    if run_defs:
        from audit.checks.definitions import run as run_def_check
        from audit.llm import Generator

        if not args.ref_lmf or not args.ref_lmf.exists():
            log.error(
                "Stage 1 requires --ref-lmf pointing to wn-ntumc-eng.xml. "
                "Example: --ref-lmf /home/bond/git/NTUMC/build/wn-ntumc-eng.xml"
            )
            sys.exit(1)

        log.info("Loading current build from %s …", args.lmf)
        current = load_lmf(args.lmf)
        log.info("  %d synsets loaded", len(current))

        log.info("Loading English reference from %s …", args.ref_lmf)
        eng = load_lmf(args.ref_lmf)
        log.info("  %d synsets loaded", len(eng))

        if synset_filter:
            current = {k: v for k, v in current.items() if k in synset_filter}
            log.info("  filtered to %d synsets", len(current))

        if args.pos:
            current = {k: v for k, v in current.items() if v.pos == args.pos}
            log.info("  filtered to %d synsets (pos=%s)", len(current), args.pos)

        prior_wrong: tuple[str, str] | None = None
        if args.prior_wrong_model and args.prior_wrong_style:
            prior_wrong = (args.prior_wrong_model, args.prior_wrong_style)
            log.info(
                "Stage-2 filter: only synsets flagged WRONG by %s / %s",
                args.prior_wrong_model, args.prior_wrong_style,
            )

        generator = Generator(model=args.model)
        db.register_run(
            model=args.model,
            prompt_style=args.prompt_style,
            short_name=args.run_name,
        )
        t0 = time.time()
        counts = run_def_check(
            current, eng, db, generator,
            prompt_style=args.prompt_style,
            batch_size=args.batch_size,
            prior_wrong=prior_wrong,
        )
        db.finish_run(
            model=args.model,
            prompt_style=args.prompt_style,
            elapsed_seconds=time.time() - t0,
            n_ok=counts[0], n_drift=counts[1],
            n_wrong=counts[2], n_skipped=counts[3],
        )

    if run_lemmas:
        from audit.checks.lemmas import run as run_lemma_check
        from audit.llm import Generator
        from audit.web_lookup import WebCache

        if not args.ref_lmf or not args.ref_lmf.exists():
            log.error(
                "Stage 2 requires --ref-lmf pointing to wn-ntumc-eng.xml. "
                "Example: --ref-lmf /home/bond/git/NTUMC/build/wn-ntumc-eng.xml"
            )
            sys.exit(1)

        log.info("Loading current build from %s …", args.lmf)
        current = load_lmf(args.lmf)
        log.info("  %d synsets loaded", len(current))

        log.info("Loading English reference from %s …", args.ref_lmf)
        eng = load_lmf(args.ref_lmf)
        log.info("  %d synsets loaded", len(eng))

        if synset_filter:
            current = {k: v for k, v in current.items() if k in synset_filter}
            log.info("  filtered to %d synsets", len(current))

        generator = Generator(model=args.model)
        db.register_run(
            model=args.model,
            prompt_style=args.prompt_style,
            short_name=args.run_name,
        )
        web_cache = WebCache(db.conn)
        run_lemma_check(
            current, eng, db, generator, web_cache,
            prompt_style=args.prompt_style,
            batch_size=args.batch_size,
        )

    if args.report:
        from audit.report import summary, write_tsv
        summary(db.conn)
        if args.out:
            write_tsv(db.conn, args.out, args.check_type if args.check_type else None)

    db.close()


if __name__ == "__main__":
    main()
