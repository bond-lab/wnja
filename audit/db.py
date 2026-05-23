"""SQLite checkpoint layer for the wnja audit pipeline.

Schema versions
---------------
v1  Original: PRIMARY KEY (synset_id, check_type, item) — single result per cell.
v2  Add `model` to PRIMARY KEY; add `runs` and `meta` tables.
    Old v1 databases are migrated automatically on open (model set to '').
"""
import sqlite3
import time
from pathlib import Path

_SCHEMA_VERSION = 2

# Tables that are safe to create with IF NOT EXISTS (no PK changes between versions)
_DDL_STABLE = """
CREATE TABLE IF NOT EXISTS web_cache (
    url        TEXT PRIMARY KEY,  -- full URL
    content    TEXT,              -- raw HTML/text of the response
    fetched_at REAL               -- Unix timestamp
);

CREATE TABLE IF NOT EXISTS runs (
    -- One row per distinct (model, prompt_style) combination used.
    run_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    model        TEXT NOT NULL,         -- model identifier stored in results.model
    prompt_style TEXT NOT NULL DEFAULT 'zero-shot',  -- zero-shot, few-shot, etc.
    short_name   TEXT,                  -- display label for reports
    notes        TEXT,
    started_at   REAL                   -- Unix timestamp of first use
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,  -- e.g. 'schema_version'
    value TEXT
);
"""

# v2 results schema (model is part of the PK)
_DDL_RESULTS_V2 = """
CREATE TABLE IF NOT EXISTS results (
    synset_id     TEXT NOT NULL,
    check_type    TEXT NOT NULL,
    -- example text, lemma under review, or '' for whole-synset checks
    item          TEXT NOT NULL DEFAULT '',
    -- '' for programmatic checks; model identifier string for LLM checks
    model         TEXT NOT NULL DEFAULT '',
    verdict       TEXT NOT NULL,  -- OK, DRIFT, WRONG, MISMATCH, DOUBTFUL, NO, SKIP
    evidence      TEXT,
    -- example check only: writtenForm that matched (NULL on MISMATCH)
    matched_lemma TEXT,
    -- Unicode char offsets in item string (NULL on MISMATCH)
    match_start   INTEGER,
    match_end     INTEGER,
    source_url    TEXT,
    -- definition check only: English source used ('ntumc-eng' or 'omw-en:2.0')
    en_source     TEXT,
    ts            REAL,
    PRIMARY KEY (synset_id, check_type, item, model)
);

CREATE INDEX IF NOT EXISTS idx_results_type_verdict
    ON results (check_type, verdict, model);
"""


def _get_version(conn: sqlite3.Connection) -> int:
    has_meta = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
    ).fetchone()
    if not has_meta:
        return 1
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return int(row[0]) if row else 1


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """Rename old results table, create v2, copy rows with model=''."""
    has_results = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='results'"
    ).fetchone()
    if has_results:
        conn.executescript("""
            ALTER TABLE results RENAME TO results_v1;
        """)
    conn.executescript(_DDL_RESULTS_V2)
    if has_results:
        conn.execute("""
            INSERT OR IGNORE INTO results
                (synset_id, check_type, item, model, verdict, evidence,
                 matched_lemma, match_start, match_end, source_url, en_source, ts)
            SELECT synset_id, check_type, item, COALESCE(model, '') AS model,
                   verdict, evidence, matched_lemma, match_start, match_end,
                   source_url, en_source, ts
            FROM results_v1
        """)
        conn.execute("DROP TABLE results_v1")
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
            _migrate_v1_to_v2(self._conn)
        else:
            self._conn.executescript(_DDL_RESULTS_V2)
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
        prompt_style: str = "zero-shot",
        short_name: str | None = None,
        notes: str | None = None,
    ) -> int:
        """Register a model/prompt-style combination and return its run_id.

        Idempotent: returns the existing run_id if already registered.

        Args:
            model: Model identifier string (same value stored in results.model).
            prompt_style: e.g. 'zero-shot', 'few-shot'.
            short_name: Optional display label.
            notes: Free-text notes about this run.

        Returns:
            Integer run_id.
        """
        row = self._conn.execute(
            "SELECT run_id FROM runs WHERE model=? AND prompt_style=?",
            (model, prompt_style),
        ).fetchone()
        if row:
            return row[0]
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO runs (model, prompt_style, short_name, notes, started_at) "
                "VALUES (?,?,?,?,?)",
                (model, prompt_style, short_name, notes, time.time()),
            )
        return cur.lastrowid

    # ------------------------------------------------------------------
    # Checkpoint queries
    # ------------------------------------------------------------------

    def is_done(
        self, synset_id: str, check_type: str, item: str = "", model: str = ""
    ) -> bool:
        """Return True if this (synset_id, check_type, item, model) row exists."""
        row = self._conn.execute(
            "SELECT 1 FROM results "
            "WHERE synset_id=? AND check_type=? AND item=? AND model=?",
            (synset_id, check_type, item, model),
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
        evidence: str | None = None,
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
                   (synset_id, check_type, item, model, verdict, evidence,
                    matched_lemma, match_start, match_end,
                    source_url, en_source, ts)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    synset_id, check_type, item, model, verdict, evidence,
                    matched_lemma, match_start, match_end,
                    source_url, en_source, time.time(),
                ),
            )

    def save_many(self, rows: list[dict]) -> None:
        """Bulk-insert result rows (for batched LLM checks).

        Each dict must have keys matching result columns; missing keys default
        to None / ''.
        """
        defaults: dict = dict(
            model="", evidence=None, matched_lemma=None,
            match_start=None, match_end=None,
            source_url=None, en_source=None,
        )
        with self._conn:
            self._conn.executemany(
                """INSERT OR REPLACE INTO results
                   (synset_id, check_type, item, model, verdict, evidence,
                    matched_lemma, match_start, match_end,
                    source_url, en_source, ts)
                   VALUES (:synset_id,:check_type,:item,:model,:verdict,:evidence,
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

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
