"""Dev set sampling and evaluation for the definition accuracy check.

Workflow
--------
1. Sample::

    uv run python -m audit.dev sample \\
        --lmf wnja-2.0.xml \\
        --ref-lmf /home/bond/git/NTUMC/build/wn-ntumc-eng.xml \\
        --out audit/dev_set.tsv

   Opens ``dev_set.tsv`` in a spreadsheet.  Fill in the ``gold`` column
   with OK / DRIFT / WRONG / SKIP for each row.  Save as TSV.

2. Evaluate::

    uv run python -m audit.dev evaluate \\
        --gold audit/dev_set.tsv \\
        --db audit.db \\
        --model "mlx-community/gemma-3-27b-it-4bit" \\
        --model "mlx-community/Qwen2.5-32B-Instruct-4bit"

   Prints per-model accuracy, per-class F1, and a confusion matrix.
   When two models are given, also prints their agreement rate.

Dev set composition (100 synsets, stratified by POS)
----------------------------------------------------
n  40 | v  20 | a  20 | r  10 | x/other  10

Synsets used as few-shot examples in checks/definitions.py are excluded.
"""
from __future__ import annotations

import argparse
import csv
import random
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

from audit.checks.definitions import _FEW_SHOT_IDS
from audit.loader import load_lmf, SynsetData

# Target counts per POS for the 100-synset dev set
_DEV_TARGET: dict[str, int] = {"n": 40, "v": 20, "a": 20, "r": 10}
_DEV_OTHER_TARGET = 10  # x, s, and any remaining POS

_DEV_COLUMNS = [
    "synset_id", "pos", "en_members", "en_def", "ja_def", "gold", "notes",
]

# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------

def sample_dev_set(
    current_synsets: dict[str, SynsetData],
    eng_synsets: dict[str, SynsetData],
    seed: int = 42,
) -> list[dict]:
    """Return a stratified sample of 100 synsets for manual annotation.

    Excludes synsets used as few-shot examples and synsets without both
    Japanese and English definitions.

    Args:
        current_synsets: Synset data from the current wnja build.
        eng_synsets: Synset data from wn-ntumc-eng.xml.
        seed: Random seed for reproducibility.

    Returns:
        List of dicts with keys matching ``_DEV_COLUMNS`` (gold and notes empty).
    """
    rng = random.Random(seed)

    # Build eligible pool: both defs present, not few-shot examples
    by_pos: dict[str, list[dict]] = defaultdict(list)
    for wnja_id, ja in current_synsets.items():
        if wnja_id in _FEW_SHOT_IDS:
            continue
        if not ja.definitions:
            continue
        ntumc_id = wnja_id.replace("wnja-", "ntumc-en-")
        en = eng_synsets.get(ntumc_id)
        if not en or not en.definitions:
            continue
        en_members = " · ".join(sorted(en.forms)[:4]) if en.forms else "—"
        row = {
            "synset_id": wnja_id,
            "pos": ja.pos,
            "en_members": en_members,
            "en_def": en.definitions[0],
            "ja_def": ja.definitions[0],
            "gold": "",
            "notes": "",
        }
        by_pos[ja.pos].append(row)

    # Sample per POS up to target counts
    selected: list[dict] = []
    for pos, target in _DEV_TARGET.items():
        pool = by_pos.get(pos, [])
        selected.extend(rng.sample(pool, min(target, len(pool))))

    # Fill remaining slots from other POS (x, s, …)
    other_pos = [p for p in by_pos if p not in _DEV_TARGET]
    other_pool: list[dict] = []
    for pos in other_pos:
        other_pool.extend(by_pos[pos])
    rng.shuffle(other_pool)
    selected.extend(other_pool[: _DEV_OTHER_TARGET])

    rng.shuffle(selected)
    return selected


def write_dev_tsv(rows: list[dict], path: Path) -> None:
    """Write dev set rows to a TSV file for manual annotation.

    Args:
        rows: List of dicts as returned by ``sample_dev_set``.
        path: Output path.
    """
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_DEV_COLUMNS, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {path}")
    print("Fill in the 'gold' column (OK / DRIFT / WRONG / SKIP) and save as TSV.")


# ---------------------------------------------------------------------------
# Loading annotated gold data
# ---------------------------------------------------------------------------

def load_gold(path: Path) -> dict[str, str]:
    """Load a manually annotated dev TSV.

    Args:
        path: Path to annotated TSV (must have 'synset_id' and 'gold' columns).

    Returns:
        Dict mapping synset_id → gold verdict (uppercased).

    Raises:
        ValueError: If any annotated verdict is not one of OK/DRIFT/WRONG/SKIP.
    """
    gold: dict[str, str] = {}
    valid = {"OK", "DRIFT", "WRONG", "SKIP", ""}
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            verdict = row.get("gold", "").strip().upper()
            if verdict not in valid:
                raise ValueError(
                    f"Invalid gold verdict {verdict!r} for {row['synset_id']}"
                )
            if verdict:
                gold[row["synset_id"]] = verdict
    return gold


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def _precision_recall_f1(
    gold: list[str], pred: list[str], label: str
) -> tuple[float, float, float]:
    tp = sum(g == label and p == label for g, p in zip(gold, pred))
    fp = sum(g != label and p == label for g, p in zip(gold, pred))
    fn = sum(g == label and p != label for g, p in zip(gold, pred))
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return p, r, f1


def _parse_run(run_str: str) -> tuple[str, str]:
    """Parse 'model/prompt_style' string into (model, prompt_style).

    The last path component after '/' is taken as prompt_style if it is one
    of the known styles; otherwise prompt_style defaults to 'zero-shot'.
    Known styles: zero-shot, one-shot, few-shot.
    """
    known = {"zero-shot", "one-shot", "few-shot"}
    if "/" in run_str:
        # Split on last '/' that matches a known style
        for style in known:
            if run_str.endswith("/" + style):
                return run_str[: -(len(style) + 1)], style
    return run_str, "zero-shot"


def evaluate(
    gold: dict[str, str],
    db_path: Path,
    runs: list[str],
) -> None:
    """Print evaluation metrics for each (model, prompt_style) run.

    Args:
        gold: {synset_id: verdict} from load_gold().
        db_path: Path to audit.db.
        runs: List of 'model/prompt_style' strings (prompt_style defaults to
            'zero-shot' if omitted). E.g.:
            'mlx-community/gemma-4-31b-it-4bit/zero-shot'
            'mlx-community/Qwen3-32B-4bit/few-shot'
    """
    conn = sqlite3.connect(str(db_path))
    labels = ["OK", "DRIFT", "WRONG"]

    # Show available runs if none specified
    if not runs:
        rows = conn.execute(
            "SELECT DISTINCT model, prompt_style FROM results "
            "WHERE check_type='definition' AND item='' ORDER BY model, prompt_style"
        ).fetchall()
        print("Available runs in DB:")
        for model, ps in rows:
            print(f"  {model}/{ps}")
        conn.close()
        return

    run_preds: dict[str, dict[str, str]] = {}
    for run_str in runs:
        model, prompt_style = _parse_run(run_str)
        rows = conn.execute(
            "SELECT synset_id, verdict FROM results "
            "WHERE check_type='definition' AND item='' AND model=? AND prompt_style=?",
            (model, prompt_style),
        ).fetchall()
        run_preds[run_str] = {ss: v for ss, v in rows}

        # Also show timing if available
        timing = conn.execute(
            "SELECT elapsed_seconds, n_ok, n_drift, n_wrong FROM runs "
            "WHERE model=? AND prompt_style=?",
            (model, prompt_style),
        ).fetchone()
        if timing and timing[0]:
            elapsed = timing[0]
            mins, secs = divmod(int(elapsed), 60)
            print(f"Run {run_str}: {mins}m{secs:02d}s  "
                  f"OK={timing[1]} DRIFT={timing[2]} WRONG={timing[3]}")

    # Evaluate each run
    for run_str in runs:
        preds = run_preds[run_str]
        common = [ss for ss in gold if ss in preds]
        if not common:
            print(f"\nRun: {run_str}\n  No overlapping synsets with gold data.")
            continue

        g = [gold[ss] for ss in common]
        p = [preds[ss] for ss in common]

        correct = sum(gi == pi for gi, pi in zip(g, p))
        print(f"\n{'='*60}")
        print(f"Run: {run_str}")
        print(f"  Evaluated: {len(common)} synsets")
        print(f"  Accuracy:  {correct}/{len(common)} = {correct/len(common):.1%}")

        print(f"\n  {'Label':<8} {'Prec':>6} {'Rec':>6} {'F1':>6}  (support)")
        for label in labels:
            prec, rec, f1 = _precision_recall_f1(g, p, label)
            support = g.count(label)
            print(f"  {label:<8} {prec:>6.1%} {rec:>6.1%} {f1:>6.1%}  ({support})")

        print(f"\n  Confusion matrix (rows=gold, cols=predicted):")
        print(f"  {'':8}", end="")
        for label in labels:
            print(f"  {label:>6}", end="")
        print()
        for gl in labels:
            print(f"  {gl:<8}", end="")
            for pl in labels:
                count = sum(gi == gl and pi == pl for gi, pi in zip(g, p))
                print(f"  {count:>6}", end="")
            print()

    # Pairwise agreement for all run pairs
    run_list = runs
    for i in range(len(run_list)):
        for j in range(i + 1, len(run_list)):
            r1, r2 = run_list[i], run_list[j]
            p1, p2 = run_preds[r1], run_preds[r2]
            common_both = [ss for ss in gold if ss in p1 and ss in p2]
            if common_both:
                agree = sum(p1[ss] == p2[ss] for ss in common_both)
                print(f"\n{'='*60}")
                print(f"Agreement: {r1}  vs  {r2}")
                print(f"  Common: {len(common_both)}  "
                      f"Agreement: {agree}/{len(common_both)} = {agree/len(common_both):.1%}")
                disagree = [(ss, p1[ss], p2[ss]) for ss in common_both if p1[ss] != p2[ss]]
                if disagree:
                    gold_dist = Counter(gold[ss] for ss, _, _ in disagree)
                    print(f"  Disagreements: {len(disagree)} "
                          f"(gold: {dict(gold_dist)})")

    conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    """Entry point for dev set management."""
    p = argparse.ArgumentParser(
        description="Sample dev set or evaluate model predictions against gold.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    # --- sample ---
    sp = sub.add_parser("sample", help="Sample 100 synsets for annotation.")
    sp.add_argument("--lmf", type=Path, default=Path("wnja-2.0.xml"))
    sp.add_argument(
        "--ref-lmf",
        type=Path,
        default=Path("/home/bond/git/NTUMC/build/wn-ntumc-eng.xml"),
    )
    sp.add_argument("--out", type=Path, default=Path("audit/dev_set.tsv"))
    sp.add_argument("--seed", type=int, default=42)

    # --- evaluate ---
    ep = sub.add_parser("evaluate", help="Evaluate model predictions against gold.")
    ep.add_argument(
        "--gold", type=Path, required=True,
        help="Annotated dev TSV with 'gold' column filled in.",
    )
    ep.add_argument("--db", type=Path, default=Path("audit.db"))
    ep.add_argument(
        "--run", dest="runs", action="append", default=[],
        metavar="MODEL/PROMPT_STYLE",
        help=(
            "Run to evaluate as 'model/prompt_style', e.g. "
            "'mlx-community/gemma-4-31b-it-4bit/zero-shot'. "
            "Repeat for multiple runs. Omit to list available runs."
        ),
    )

    args = p.parse_args(argv)

    if args.cmd == "sample":
        if not args.lmf.exists():
            print(f"ERROR: {args.lmf} not found", file=sys.stderr)
            sys.exit(1)
        print(f"Loading {args.lmf} …")
        current = load_lmf(args.lmf)
        print(f"Loading {args.ref_lmf} …")
        eng = load_lmf(args.ref_lmf)
        rows = sample_dev_set(current, eng, seed=args.seed)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        write_dev_tsv(rows, args.out)

    elif args.cmd == "evaluate":
        if not args.gold.exists():
            print(f"ERROR: gold file {args.gold} not found", file=sys.stderr)
            sys.exit(1)
        gold = load_gold(args.gold)
        annotated = {ss: v for ss, v in gold.items() if v}
        print(f"Gold annotations: {len(annotated)} synsets")
        evaluate(annotated, args.db, args.runs)


if __name__ == "__main__":
    main()
