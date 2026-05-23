"""Export audit results to TSV and print a summary."""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path


_COLUMNS = [
    "synset_id", "check_type", "item", "verdict",
    "evidence", "matched_lemma", "match_start", "match_end",
    "source_url", "en_source", "model", "ts",
]


def summary(conn: sqlite3.Connection) -> None:
    """Print a grouped verdict count table to stdout."""
    print("check_type       verdict     count")
    print("-" * 40)
    rows = conn.execute("""
        SELECT check_type, verdict, count(*) AS n
        FROM results
        GROUP BY check_type, verdict
        ORDER BY check_type, verdict
    """).fetchall()
    for ct, v, n in rows:
        print(f"{ct:<16} {v:<12} {n:>6}")
    print()

    # For example checks, break out stub (no forms) vs real mismatch
    stub = conn.execute("""
        SELECT count(*) FROM results
        WHERE check_type='example' AND verdict='MISMATCH'
        AND evidence LIKE 'none of []%'
    """).fetchone()[0]
    real = conn.execute("""
        SELECT count(*) FROM results
        WHERE check_type='example' AND verdict='MISMATCH'
        AND evidence NOT LIKE 'none of []%'
    """).fetchone()[0]
    if stub + real > 0:
        print(f"  (example MISMATCH breakdown: {stub} stub/no-forms, {real} form-present mismatches)")
        print()


def sample(
    conn: sqlite3.Connection,
    check_type: str,
    verdict: str,
    n: int = 100,
    stub_filter: str | None = None,
) -> list[tuple]:
    """Return up to *n* result rows for a given check_type and verdict.

    Args:
        conn: Open SQLite connection.
        check_type: e.g. 'example', 'definition', 'lemma'.
        verdict: e.g. 'OK', 'MISMATCH', 'DRIFT', 'WRONG'.
        n: Sample size.
        stub_filter: If 'stub', restrict to empty-forms MISMATCHes.
                     If 'real', exclude them.

    Returns:
        List of (synset_id, item, evidence, matched_lemma, match_start, match_end) tuples.
    """
    extra = ""
    if stub_filter == "stub":
        extra = "AND evidence LIKE 'none of []%'"
    elif stub_filter == "real":
        extra = "AND evidence NOT LIKE 'none of []%'"

    return conn.execute(f"""
        SELECT synset_id, item, evidence, matched_lemma, match_start, match_end
        FROM results
        WHERE check_type=? AND verdict=? {extra}
        ORDER BY RANDOM()
        LIMIT ?
    """, (check_type, verdict, n)).fetchall()


def write_tsv(conn: sqlite3.Connection, path: Path, check_type: str | None = None) -> None:
    """Write results to a TSV file.

    Args:
        conn: Open SQLite connection.
        path: Output file path.
        check_type: If set, restrict to this check type; otherwise write all.
    """
    where = f"WHERE check_type='{check_type}'" if check_type else ""
    rows = conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM results {where} ORDER BY synset_id, check_type, item"
    ).fetchall()
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(_COLUMNS)
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {path}")


def main(argv: list[str] | None = None) -> None:
    """Entry point for the report command."""
    p = argparse.ArgumentParser(description="Report audit results from audit.db.")
    p.add_argument("--db", type=Path, default=Path("audit.db"))
    p.add_argument("--out", type=Path, default=None, help="TSV output path.")
    p.add_argument("--check-type", default=None, help="Filter TSV to this check type.")
    p.add_argument(
        "--sample",
        type=int,
        default=0,
        help="Print N random rows per verdict to stdout.",
    )
    args = p.parse_args(argv)

    conn = sqlite3.connect(str(args.db))
    summary(conn)

    if args.out:
        write_tsv(conn, args.out, args.check_type)

    if args.sample:
        for ct, v, _ in conn.execute(
            "SELECT DISTINCT check_type, verdict, count(*) FROM results GROUP BY check_type, verdict"
        ).fetchall():
            rows = sample(conn, ct, v, args.sample)
            if not rows:
                continue
            print(f"\n=== {ct} / {v} (sample of {len(rows)}) ===")
            for ss, item, ev, ml, ms, me in rows:
                print(f"  {ss}")
                print(f"    item:  {item[:120]}")
                if ml:
                    print(f"    match: '{ml}' [{ms}:{me}]")
                else:
                    print(f"    ev:    {(ev or '')[:120]}")

    conn.close()


if __name__ == "__main__":
    main()
