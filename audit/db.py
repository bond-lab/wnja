"""SQLite checkpoint layer for the wnja audit pipeline.

Schema versions
---------------
v1  Original: PRIMARY KEY (synset_id, check_type, item) — single result per cell.
v2  Add `model` to PRIMARY KEY; add `runs` and `meta` tables.
v3  Add `prompt_style` to PRIMARY KEY; add `suggestion` column.
    Enables storing results from all (model, prompt_style) combinations in one DB.
"""
import sqlite3
import time
from pathlib import Path

_SCHEMA_VERSION = 3

_DDL_STABLE = """
CREATE TABLE IF NOT EXISTS web_cache (
    url        TEXT PRIMARY KEY,
    body       TEXT,
    fetched_at INTEGER
);

CREATE TABLE IF NOT EXISTS runs (
    run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    model           TEXT NOT NULL,
    prompt_style    TEXT NOT NULL DEFAULT '',
    short_name      TEXT,
    notes           TEXT,
    created_at      REAL,
    finished_at     REAL,
    elapsed_seconds REAL,
    n_ok            INTEGER,
    n_drift         INTEGER,
    n_wrong         INTEGER,
    n_skipped       INTEGER
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS reviews (
    synset_id         TEXT NOT NULL,
    stage1_model      TEXT NOT NULL,
    stage1_style      TEXT NOT NULL,
    stage2_model      TEXT NOT NULL,
    stage2_style      TEXT NOT NULL,
    bucket            TEXT NOT NULL,
    claude_verdict    TEXT,
    claude_reasoning  TEXT,
    claude_suggestion TEXT,
    user_decision     TEXT,
    user_suggestion   TEXT,
    created_at        REAL,
    reviewed_at       REAL,
    decided_at        REAL,
    PRIMARY KEY (synset_id, stage1_model, stage1_style, stage2_model, stage2_style)
);
"""

_DDL_RESULTS_V3 = """
CREATE TABLE IF NOT EXISTS results (
    synset_id     TEXT NOT NULL,
    check_type    TEXT NOT NULL,
    item          TEXT NOT NULL DEFAULT '',
    model         TEXT NOT NULL DEFAULT '',
    prompt_style  TEXT NOT NULL DEFAULT '',
    verdict       TEXT NOT NULL,
    evidence      TEXT,
    suggestion    TEXT,
    matched_lemma TEXT,
    match_start   INTEGER,
    match_end     INTEGER,
    source_url    TEXT,
    en_source     TEXT,
    ts            REAL,
    PRIMARY KEY (synset_id, check_type, item, model, prompt_style)
);

CREATE INDEX IF NOT EXISTS idx_results_type_verdict
    ON results (check_type, verdict, model, prompt_style);
"""


def _get_version(conn: sqlite3.Connection) -> int:
    has_meta = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
    ).fetchone()
    if not has_meta:
        return 1
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return int(row[0]) if row else 1


def _migrate_to_v3(conn: sqlite3.Connection, from_version: int) -> None:
    """Migrate any older schema to v3."""
    has_results = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='results'"
    ).fetchone()

    if has_results:
        conn.execute("ALTER TABLE results RENAME TO results_old")

    conn.executescript(_DDL_RESULTS_V3)

    if has_results:
        # Copy columns that exist in both old and new schemas
        cols_old = {
            row[1]
            for row in conn.execute("PRAGMA table_info(results_old)").fetchall()
        }
        shared = [
            c for c in
            ("synset_id", "check_type", "item", "model",
             "verdict", "evidence", "matched_lemma", "match_start",
             "match_end", "source_url", "en_source", "ts")
            if c in cols_old
        ]
        col_list = ", ".join(shared)
        conn.execute(f"""
            INSERT OR IGNORE INTO results ({col_list})
            SELECT {col_list} FROM results_old
        """)
        conn.execute("DROP TABLE results_old")

    conn.execute(
        "INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)",
        (str(_SCHEMA_VERSION),),
    )
    conn.commit()


class AuditDB:
    """Checkpoint database shared by all audit stages."""

    def __init__(self, path: str | Path) -> None:
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_DDL_STABLE)
        version = _get_version(self._conn)
        if version < _SCHEMA_VERSION:
            _migrate_to_v3(self._conn, from_version=version)
        else:
            self._conn.executescript(_DDL_RESULTS_V3)
        self._conn.execute(
            "INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)",
            (str(_SCHEMA_VERSION),),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Run registration
    # ------------------------------------------------------------------

    def register_run(
        self,
        model: str,
        prompt_style: str = "",
        short_name: str | None = None,
        notes: str | None = None,
    ) -> int:
        """Register a (model, prompt_style) combination; return run_id.

        Idempotent: returns the existing run_id if already registered.
        """
        row = self._conn.execute(
            "SELECT run_id FROM runs WHERE model=? AND prompt_style=?",
            (model, prompt_style),
        ).fetchone()
        if row:
            return row[0]
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO runs (model, prompt_style, short_name, notes, created_at) "
                "VALUES (?,?,?,?,?)",
                (model, prompt_style, short_name, notes, time.time()),
            )
        return cur.lastrowid

    def finish_run(
        self,
        model: str,
        prompt_style: str = "",
        *,
        elapsed_seconds: float | None = None,
        n_ok: int | None = None,
        n_drift: int | None = None,
        n_wrong: int | None = None,
        n_skipped: int | None = None,
    ) -> None:
        """Record completion time and verdict counts for a run."""
        with self._conn:
            self._conn.execute(
                """UPDATE runs
                   SET finished_at=?, elapsed_seconds=?,
                       n_ok=?, n_drift=?, n_wrong=?, n_skipped=?
                   WHERE model=? AND prompt_style=?""",
                (time.time(), elapsed_seconds,
                 n_ok, n_drift, n_wrong, n_skipped,
                 model, prompt_style),
            )

    def list_runs(self) -> list[tuple]:
        """Return all runs ordered by creation time."""
        return self._conn.execute(
            """SELECT run_id, model, prompt_style, short_name,
                      created_at, finished_at, elapsed_seconds,
                      n_ok, n_drift, n_wrong, n_skipped
               FROM runs ORDER BY created_at"""
        ).fetchall()

    # ------------------------------------------------------------------
    # Checkpoint queries
    # ------------------------------------------------------------------

    def is_done(
        self,
        synset_id: str,
        check_type: str,
        item: str = "",
        model: str = "",
        prompt_style: str = "",
    ) -> bool:
        """Return True if a result row already exists for this combination."""
        row = self._conn.execute(
            "SELECT 1 FROM results "
            "WHERE synset_id=? AND check_type=? AND item=? AND model=? AND prompt_style=?",
            (synset_id, check_type, item, model, prompt_style),
        ).fetchone()
        return row is not None

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def save_result(
        self,
        synset_id: str,
        check_type: str,
        item: str,
        verdict: str,
        *,
        model: str = "",
        prompt_style: str = "",
        evidence: str | None = None,
        suggestion: str | None = None,
        matched_lemma: str | None = None,
        match_start: int | None = None,
        match_end: int | None = None,
        source_url: str | None = None,
        en_source: str | None = None,
    ) -> None:
        """Insert or replace one result row."""
        with self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO results
                   (synset_id, check_type, item, model, prompt_style,
                    verdict, evidence, suggestion,
                    matched_lemma, match_start, match_end,
                    source_url, en_source, ts)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    synset_id, check_type, item, model, prompt_style,
                    verdict, evidence, suggestion,
                    matched_lemma, match_start, match_end,
                    source_url, en_source, time.time(),
                ),
            )

    def save_many(self, rows: list[dict]) -> None:
        """Bulk-insert result rows.

        Each dict must have synset_id, check_type, item, verdict;
        all other keys are optional.
        """
        defaults: dict = dict(
            model="", prompt_style="", evidence=None, suggestion=None,
            matched_lemma=None, match_start=None, match_end=None,
            source_url=None, en_source=None,
        )
        with self._conn:
            self._conn.executemany(
                """INSERT OR REPLACE INTO results
                   (synset_id, check_type, item, model, prompt_style,
                    verdict, evidence, suggestion,
                    matched_lemma, match_start, match_end,
                    source_url, en_source, ts)
                   VALUES (:synset_id,:check_type,:item,:model,:prompt_style,
                           :verdict,:evidence,:suggestion,
                           :matched_lemma,:match_start,:match_end,
                           :source_url,:en_source,:ts)""",
                [{**defaults, **r, "ts": time.time()} for r in rows],
            )

    # ------------------------------------------------------------------
    # Access
    # ------------------------------------------------------------------

    @property
    def conn(self) -> sqlite3.Connection:
        """The underlying SQLite connection (read access for reports)."""
        return self._conn

    # ------------------------------------------------------------------
    # Two-stage review workflow
    # ------------------------------------------------------------------

    def assign_buckets(
        self,
        s1_model: str,
        s1_style: str,
        s2_model: str,
        s2_style: str,
    ) -> dict[str, int]:
        """Compute change/keep/uncertain buckets and populate the reviews table.

        Reads the results table for both stage runs, applies bucket logic:
          stage1=WRONG + stage2=WRONG  → 'change'
          stage1=WRONG + stage2=OK     → 'keep'
          stage1=WRONG + stage2=other  → 'uncertain'

        Idempotent: uses INSERT OR IGNORE so existing rows are not overwritten.

        Returns:
            Dict with keys 'change', 'keep', 'uncertain' and their counts.
        """
        s1 = {row[0]: row[1] for row in self._conn.execute(
            "SELECT synset_id, verdict FROM results "
            "WHERE check_type='definition' AND item='' AND model=? AND prompt_style=? AND verdict='WRONG'",
            (s1_model, s1_style),
        )}
        s2 = {row[0]: row[1] for row in self._conn.execute(
            "SELECT synset_id, verdict FROM results "
            "WHERE check_type='definition' AND item='' AND model=? AND prompt_style=?",
            (s2_model, s2_style),
        )}

        counts: dict[str, int] = {"change": 0, "keep": 0, "uncertain": 0}
        rows = []
        for sid in s1:
            v2 = s2.get(sid, "")
            if v2 == "WRONG":
                bucket = "change"
            elif v2 == "OK":
                bucket = "keep"
            else:
                bucket = "uncertain"
            counts[bucket] += 1
            rows.append((sid, s1_model, s1_style, s2_model, s2_style, bucket, time.time()))

        with self._conn:
            self._conn.executemany(
                "INSERT OR IGNORE INTO reviews "
                "(synset_id, stage1_model, stage1_style, stage2_model, stage2_style, "
                " bucket, created_at) VALUES (?,?,?,?,?,?,?)",
                rows,
            )
        return counts

    def get_unreviewed_batch(
        self,
        s1_model: str,
        s1_style: str,
        s2_model: str,
        s2_style: str,
        n: int = 50,
        bucket: str | None = None,
    ) -> list[dict]:
        """Return up to n unreviewed review rows with full result context.

        Orders 'change' before 'uncertain'. Each returned dict has:
          synset_id, bucket,
          s1_verdict, s1_note, s1_suggestion,
          s2_verdict, s2_note, s2_suggestion.
        """
        bucket_filter = "AND r.bucket = ?" if bucket else ""
        params: list = [s1_model, s1_style, s2_model, s2_style]
        if bucket:
            params.append(bucket)
        params.append(n)

        rows = self._conn.execute(f"""
            SELECT r.synset_id, r.bucket,
                   r1.verdict, r1.evidence, r1.suggestion,
                   r2.verdict, r2.evidence, r2.suggestion
            FROM reviews r
            LEFT JOIN results r1
                ON r1.synset_id = r.synset_id AND r1.check_type = 'definition'
                AND r1.item = '' AND r1.model = r.stage1_model AND r1.prompt_style = r.stage1_style
            LEFT JOIN results r2
                ON r2.synset_id = r.synset_id AND r2.check_type = 'definition'
                AND r2.item = '' AND r2.model = r.stage2_model AND r2.prompt_style = r.stage2_style
            WHERE r.stage1_model = ? AND r.stage1_style = ?
              AND r.stage2_model = ? AND r.stage2_style = ?
              AND r.claude_verdict IS NULL
              {bucket_filter}
            ORDER BY CASE r.bucket WHEN 'change' THEN 0 WHEN 'uncertain' THEN 1 ELSE 2 END,
                     r.synset_id
            LIMIT ?
        """, params).fetchall()

        return [
            {
                "synset_id": row[0],
                "bucket": row[1],
                "s1_verdict": row[2],
                "s1_note": row[3],
                "s1_suggestion": row[4],
                "s2_verdict": row[5],
                "s2_note": row[6],
                "s2_suggestion": row[7],
            }
            for row in rows
        ]

    def save_claude_review(
        self,
        synset_id: str,
        s1_model: str,
        s1_style: str,
        s2_model: str,
        s2_style: str,
        *,
        verdict: str,
        reasoning: str,
        suggestion: str | None,
    ) -> None:
        """Record Claude's review verdict for one synset."""
        with self._conn:
            self._conn.execute(
                """UPDATE reviews SET claude_verdict=?, claude_reasoning=?,
                   claude_suggestion=?, reviewed_at=?
                   WHERE synset_id=? AND stage1_model=? AND stage1_style=?
                     AND stage2_model=? AND stage2_style=?""",
                (verdict, reasoning, suggestion, time.time(),
                 synset_id, s1_model, s1_style, s2_model, s2_style),
            )

    def save_user_decision(
        self,
        synset_id: str,
        s1_model: str,
        s1_style: str,
        s2_model: str,
        s2_style: str,
        *,
        decision: str,
        suggestion: str | None = None,
    ) -> None:
        """Record the user's final decision for one synset."""
        with self._conn:
            self._conn.execute(
                """UPDATE reviews SET user_decision=?, user_suggestion=?, decided_at=?
                   WHERE synset_id=? AND stage1_model=? AND stage1_style=?
                     AND stage2_model=? AND stage2_style=?""",
                (decision, suggestion, time.time(),
                 synset_id, s1_model, s1_style, s2_model, s2_style),
            )

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
