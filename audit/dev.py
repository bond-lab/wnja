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
import re
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

def _fmt_time(seconds: float) -> str:
    mins, secs = divmod(int(seconds), 60)
    return f"{mins}m{secs:02d}s"


def _macro_f1(gold: list[str], pred: list[str], labels: list[str]) -> float:
    f1s = [_precision_recall_f1(gold, pred, lbl)[2] for lbl in labels]
    return sum(f1s) / len(f1s) if f1s else 0.0


def table(gold: dict[str, str], db_path: Path) -> None:
    """Print a compact cross-run comparison table.

    Args:
        gold: {synset_id: verdict} from load_gold() (SKIP entries excluded).
        db_path: Path to audit.db.
    """
    conn = sqlite3.connect(str(db_path))
    labels = ["OK", "DRIFT", "WRONG"]

    runs = conn.execute(
        "SELECT model, prompt_style, elapsed_seconds "
        "FROM runs ORDER BY created_at"
    ).fetchall()

    hdr = f"{'Model':<26} {'Style':<10} {'Time':>7}  {'OK':>4} {'Drift':>5} {'Wrong':>5}  {'F1':>6}  {'WRONG-F1':>8}"
    print(hdr)
    print("-" * len(hdr))

    for model, prompt_style, elapsed in runs:
        short_model = model.replace("mlx-community/", "")
        time_str = _fmt_time(elapsed) if elapsed else "—"

        rows = conn.execute(
            "SELECT synset_id, verdict FROM results "
            "WHERE check_type='definition' AND item='' AND model=? AND prompt_style=?",
            (model, prompt_style),
        ).fetchall()
        preds = {ss: v for ss, v in rows}

        # Compute verdict totals from results table (runs table only has incremental counts)
        r_ok = sum(1 for v in preds.values() if v == "OK")
        r_drift = sum(1 for v in preds.values() if v == "DRIFT")
        r_wrong = sum(1 for v in preds.values() if v == "WRONG")

        common = [ss for ss in gold if ss in preds]
        if common:
            g = [gold[ss] for ss in common]
            p = [preds[ss] for ss in common]
            mf1 = _macro_f1(g, p, labels)
            _, _, wf1 = _precision_recall_f1(g, p, "WRONG")
        else:
            mf1 = wf1 = 0.0

        print(
            f"{short_model:<26} {prompt_style:<10} {time_str:>7}  "
            f"{r_ok:>4} {r_drift:>5} {r_wrong:>5}  "
            f"{mf1:>6.2f}  {wf1:>8.2f}"
        )

    conn.close()


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
# Two-stage review workflow subcommands
# ---------------------------------------------------------------------------

_REVIEW_SYSTEM = """\
You are reviewing Japanese WordNet definitions flagged as potentially incorrect by
two automated models. For each case you have the synset ID, English source
definition, current Japanese definition, and explanatory notes from the two model runs.

Decide:
  change — the issue is real; the Japanese definition should be corrected
  keep   — the definition is fine; this is a false positive
  check  — you are uncertain; human review is needed

For 'change', also provide an improved Japanese definition.

Reply with EXACTLY one line per case, no preamble:
  <ID> | change | <brief reasoning in English> | SUGGESTED: <improved Japanese definition>
  <ID> | keep   | <brief reasoning in English>
  <ID> | check  | <brief reasoning in English>\
"""

_REVIEW_LINE_RE = re.compile(
    r"(wnja-[\w-]+)\s*\|\s*(change|keep|check)\s*\|\s*(.*?)(?:\|\s*SUGGESTED[：:]?\s*(.+))?$",
    re.IGNORECASE,
)


def _parse_meta_from_toml(text: str) -> dict[str, str]:
    """Extract string values from the [meta] block of a TOML file."""
    meta_m = re.search(r'\[meta\](.*?)(?=\[\[|\Z)', text, re.DOTALL)
    if not meta_m:
        return {}
    block = meta_m.group(1)
    result: dict[str, str] = {}
    for m in re.finditer(r'^(\w+)\s*=\s*"([^"]*)"', block, re.MULTILINE):
        result[m.group(1)] = m.group(2)
    return result


def _next_batch_num(out_dir: Path, date_str: str) -> int:
    """Return the next sequential batch number for today's reviews."""
    existing = list(out_dir.glob(f"review_{date_str}_batch*.toml"))
    nums = []
    for p in existing:
        m = re.search(r"batch(\d+)\.toml$", p.name)
        if m:
            nums.append(int(m.group(1)))
    return max(nums) + 1 if nums else 1


def _format_review_batch(cases: list[dict]) -> str:
    """Format a list of unreviewed cases for the review LLM."""
    parts: list[str] = []
    for i, case in enumerate(cases, 1):
        sid = case["synset_id"]
        s1_note = case.get("s1_note") or ""
        s2_note = case.get("s2_note") or ""
        suggestion = case.get("s1_suggestion") or case.get("s2_suggestion") or ""
        ja = case.get("ja_def", "")
        en = case.get("en_def", "")
        bucket = case.get("bucket", "")

        lines = [f"{i}. ID: {sid}  [bucket: {bucket}]"]
        lines.append(f"   EN: {en}")
        lines.append(f"   JA: {ja}")
        if s1_note:
            lines.append(f"   Stage1: {s1_note}")
        if s2_note:
            lines.append(f"   Stage2: {s2_note}")
        if suggestion:
            lines.append(f"   Suggested: {suggestion}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _parse_review_response(
    response: str,
) -> dict[str, tuple[str, str, str | None]]:
    """Parse review LLM output into {synset_id: (verdict, reasoning, suggestion)}."""
    from audit.checks.definitions import _strip_thinking
    results: dict[str, tuple[str, str, str | None]] = {}
    text = _strip_thinking(response)
    for line in text.splitlines():
        m = _REVIEW_LINE_RE.search(line)
        if m:
            sid = m.group(1).rstrip("*:.,")
            verdict = m.group(2).lower()
            reasoning = m.group(3).strip()
            suggestion = m.group(4).strip() if m.group(4) else None
            results[sid] = (verdict, reasoning, suggestion)
    return results


def _cmd_assign_buckets(args) -> None:
    from audit.db import AuditDB
    db = AuditDB(args.db)
    counts = db.assign_buckets(
        args.s1_model, args.s1_style, args.s2_model, args.s2_style
    )
    total = sum(counts.values())
    print(f"Bucket assignment complete ({total} synsets):")
    print(f"  change:    {counts['change']}")
    print(f"  uncertain: {counts['uncertain']}")
    print(f"  keep:      {counts['keep']}")
    db.close()


def _cmd_review_batch(args) -> None:
    import datetime
    import time
    from audit.db import AuditDB
    from audit.llm import Generator
    from audit.review import generate_digest

    db = AuditDB(args.db)

    print(f"Loading {args.lmf} …", file=sys.stderr)
    current = load_lmf(args.lmf)
    print(f"Loading {args.ref_lmf} …", file=sys.stderr)
    eng = load_lmf(args.ref_lmf)

    ja_defs = {sid: sd.definitions[0] for sid, sd in current.items() if sd.definitions}
    en_defs: dict[str, str] = {}
    for wnja_id in current:
        ntumc_id = wnja_id.replace("wnja-", "ntumc-en-")
        en = eng.get(ntumc_id)
        if en and en.definitions:
            en_defs[wnja_id] = en.definitions[0]

    bucket_filter = None if args.bucket == "both" else args.bucket
    rows = db.get_unreviewed_batch(
        args.s1_model, args.s1_style,
        args.s2_model, args.s2_style,
        n=args.n, bucket=bucket_filter,
    )

    if not rows:
        print("No unreviewed cases found.")
        db.close()
        return

    for row in rows:
        sid = row["synset_id"]
        row["ja_def"] = ja_defs.get(sid, "")
        row["en_def"] = en_defs.get(sid, "")

    print(f"Reviewing {len(rows)} cases with {args.model} …", file=sys.stderr)
    generator = Generator(model=args.model)

    for batch_start in range(0, len(rows), args.batch_size):
        batch = rows[batch_start : batch_start + args.batch_size]
        user_turn = _format_review_batch(batch)

        parsed: dict[str, tuple[str, str, str | None]] = {}
        for attempt in range(3):
            try:
                response = generator.chat(
                    system=_REVIEW_SYSTEM,
                    user=user_turn,
                    max_tokens=args.batch_size * 300,
                )
                parsed = _parse_review_response(response)
            except Exception as exc:
                print(f"LLM error (attempt {attempt + 1}): {exc}", file=sys.stderr)
                time.sleep(2)
                continue
            if len(parsed) >= max(1, len(batch) // 2):
                break
            print(
                f"  Only {len(parsed)}/{len(batch)} parsed, retrying",
                file=sys.stderr,
            )

        for row in batch:
            sid = row["synset_id"]
            if sid in parsed:
                verdict, reasoning, suggestion = parsed[sid]
            else:
                verdict, reasoning, suggestion = "check", "parse failure", None
                print(f"  Warning: no review parsed for {sid}", file=sys.stderr)
            db.save_claude_review(
                sid,
                args.s1_model, args.s1_style,
                args.s2_model, args.s2_style,
                verdict=verdict,
                reasoning=reasoning,
                suggestion=suggestion,
            )
            row["claude_verdict"] = verdict
            row["claude_reasoning"] = reasoning
            row["claude_suggestion"] = suggestion

        done = min(batch_start + args.batch_size, len(rows))
        print(f"  {done}/{len(rows)} reviewed", file=sys.stderr)

    date_str = datetime.date.today().isoformat()
    args.out.mkdir(parents=True, exist_ok=True)
    batch_num = _next_batch_num(args.out, date_str)
    meta = {
        "date": date_str,
        "batch": batch_num,
        "stage1_model": args.s1_model,
        "stage1_style": args.s1_style,
        "stage2_model": args.s2_model,
        "stage2_style": args.s2_style,
    }
    toml_path = args.out / f"review_{date_str}_batch{batch_num:02d}.toml"
    generate_digest(rows, meta, toml_path, ja_defs=ja_defs, en_defs=en_defs)

    verdicts = [r.get("claude_verdict", "") for r in rows]
    n_change = verdicts.count("change")
    n_keep = verdicts.count("keep")
    n_check = verdicts.count("check")
    print(f"\nReview complete: {n_change} change, {n_check} check, {n_keep} keep")
    print(f"TOML digest written to: {toml_path}")
    db.close()


def _cmd_load_decisions(args) -> None:
    from audit.db import AuditDB
    from audit.review import load_decisions

    if not args.toml.exists():
        print(f"ERROR: {args.toml} not found", file=sys.stderr)
        sys.exit(1)

    text = args.toml.read_text(encoding="utf-8")
    meta = _parse_meta_from_toml(text)
    s1_model = args.s1_model or meta.get("stage1_model", "")
    s1_style = args.s1_style or meta.get("stage1_style", "")
    s2_model = args.s2_model or meta.get("stage2_model", "")
    s2_style = args.s2_style or meta.get("stage2_style", "")

    if not all([s1_model, s1_style, s2_model, s2_style]):
        print(
            "ERROR: could not determine model/style from TOML meta. "
            "Pass --s1-model/--s1-style/--s2-model/--s2-style explicitly.",
            file=sys.stderr,
        )
        sys.exit(1)

    db = AuditDB(args.db)
    counts = load_decisions(db, args.toml, s1_model, s1_style, s2_model, s2_style)
    print(f"Decisions loaded from {args.toml.name}:")
    print(f"  approved:  {counts['approved']}")
    print(f"  rejected:  {counts['rejected']}")
    print(f"  modified:  {counts['modified']}")
    print(f"  skipped:   {counts['skipped']}")
    db.close()


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

    # --- table ---
    tp = sub.add_parser("table", help="Print compact cross-run comparison table.")
    tp.add_argument(
        "--gold", type=Path, default=Path("audit/dev_set.tsv"),
        help="Annotated dev TSV with 'gold' column filled in.",
    )
    tp.add_argument("--db", type=Path, default=Path("audit.db"))

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

    # --- assign-buckets ---
    abp = sub.add_parser(
        "assign-buckets",
        help="Compute change/keep/uncertain buckets and populate the reviews table.",
    )
    abp.add_argument("--db", type=Path, default=Path("audit.db"))
    abp.add_argument("--s1-model", required=True, help="Stage-1 model ID.")
    abp.add_argument("--s1-style", required=True, help="Stage-1 prompt style.")
    abp.add_argument("--s2-model", required=True, help="Stage-2 model ID.")
    abp.add_argument("--s2-style", required=True, help="Stage-2 prompt style.")

    # --- review-batch ---
    rbp = sub.add_parser(
        "review-batch",
        help="Review unreviewed synsets with an LLM and write a TOML digest.",
    )
    rbp.add_argument("--db", type=Path, default=Path("audit.db"))
    rbp.add_argument("--lmf", type=Path, default=Path("wnja-2.0.xml"))
    rbp.add_argument(
        "--ref-lmf", type=Path, required=True,
        help="NTU-MC English LMF (e.g. wn-ntumc-eng.xml).",
    )
    rbp.add_argument(
        "--out", type=Path, default=Path("reviews"),
        help="Output directory for TOML digests.",
    )
    rbp.add_argument("--s1-model", required=True, help="Stage-1 model ID.")
    rbp.add_argument("--s1-style", required=True, help="Stage-1 prompt style.")
    rbp.add_argument("--s2-model", required=True, help="Stage-2 model ID.")
    rbp.add_argument("--s2-style", required=True, help="Stage-2 prompt style.")
    rbp.add_argument(
        "--model",
        default="mlx-community/Qwen3-32B-4bit",
        help="LLM to use for the review pass.",
    )
    rbp.add_argument("--n", type=int, default=50, help="Number of cases to review.")
    rbp.add_argument("--batch-size", type=int, default=5, help="Cases per LLM prompt.")
    rbp.add_argument(
        "--bucket",
        choices=["change", "uncertain", "both"],
        default="both",
        help="Which bucket(s) to pull from.",
    )

    # --- load-decisions ---
    ldp = sub.add_parser(
        "load-decisions",
        help="Load user decisions from an edited TOML digest into the DB.",
    )
    ldp.add_argument("--db", type=Path, default=Path("audit.db"))
    ldp.add_argument("--toml", type=Path, required=True, help="Edited TOML digest path.")
    ldp.add_argument(
        "--s1-model", default=None,
        help="Override stage-1 model (read from TOML [meta] if omitted).",
    )
    ldp.add_argument("--s1-style", default=None)
    ldp.add_argument("--s2-model", default=None)
    ldp.add_argument("--s2-style", default=None)

    args = p.parse_args(argv)

    if args.cmd == "table":
        if not args.gold.exists():
            print(f"ERROR: gold file {args.gold} not found", file=sys.stderr)
            sys.exit(1)
        gold = load_gold(args.gold)
        annotated = {ss: v for ss, v in gold.items() if v and v != "SKIP"}
        print(f"Gold: {len(annotated)} synsets  "
              f"(OK={sum(v=='OK' for v in annotated.values())} "
              f"DRIFT={sum(v=='DRIFT' for v in annotated.values())} "
              f"WRONG={sum(v=='WRONG' for v in annotated.values())})\n")
        table(annotated, args.db)

    elif args.cmd == "sample":
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

    elif args.cmd == "assign-buckets":
        _cmd_assign_buckets(args)

    elif args.cmd == "review-batch":
        if not args.lmf.exists():
            print(f"ERROR: {args.lmf} not found", file=sys.stderr)
            sys.exit(1)
        if not args.ref_lmf.exists():
            print(f"ERROR: {args.ref_lmf} not found", file=sys.stderr)
            sys.exit(1)
        _cmd_review_batch(args)

    elif args.cmd == "load-decisions":
        _cmd_load_decisions(args)


if __name__ == "__main__":
    main()
