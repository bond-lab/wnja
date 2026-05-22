"""SQLite checkpoint layer for the wnja audit pipeline."""
import sqlite3
import time
from pathlib import Path

_DDL = """
CREATE TABLE IF NOT EXISTS results (
    synset_id     TEXT NOT NULL,
    check_type    TEXT NOT NULL,
    -- example text, lemma under review, or '' for whole-synset checks
    item          TEXT NOT NULL DEFAULT '',
    verdict       TEXT NOT NULL,  -- OK, DRIFT, WRONG, MISMATCH, DOUBTFUL, NO
    evidence      TEXT,
    -- example check only: the writtenForm that matched (NULL on MISMATCH)
    matched_lemma TEXT,
    -- Unicode char offset of match in item string (NULL on MISMATCH)
    match_start   INTEGER,
    match_end     INTEGER,
    source_url    TEXT,
    -- definition check only: which English source was used
    en_source     TEXT,
    model         TEXT,
    ts            REAL,
    PRIMARY KEY (synset_id, check_type, item)
);

CREATE TABLE IF NOT EXISTS web_cache (
    url        TEXT PRIMARY KEY,
    content    TEXT,
    fetched_at REAL
);

CREATE INDEX IF NOT EXISTS idx_results_type_verdict
    ON results (check_type, verdict);
"""


class AuditDB:
    """Checkpoint database shared by all audit stages."""

    def __init__(self, path: str | Path) -> None:
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.executescript(_DDL)
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA foreign_keys = ON")

    def is_done(self, synset_id: str, check_type: str, item: str = "") -> bool:
        """Return True if this (synset_id, check_type, item) row already exists."""
        row = self._conn.execute(
            "SELECT 1 FROM results WHERE synset_id=? AND check_type=? AND item=?",
            (synset_id, check_type, item),
        ).fetchone()
        return row is not None

    def save_result(
        self,
        synset_id: str,
        check_type: str,
        item: str,
        verdict: str,
        *,
        evidence: str | None = None,
        matched_lemma: str | None = None,
        match_start: int | None = None,
        match_end: int | None = None,
        source_url: str | None = None,
        en_source: str | None = None,
        model: str | None = None,
    ) -> None:
        """Insert or replace one result row."""
        with self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO results
                   (synset_id, check_type, item, verdict, evidence,
                    matched_lemma, match_start, match_end,
                    source_url, en_source, model, ts)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    synset_id, check_type, item, verdict, evidence,
                    matched_lemma, match_start, match_end,
                    source_url, en_source, model, time.time(),
                ),
            )

    def save_many(self, rows: list[dict]) -> None:
        """Bulk-insert result rows (for batched LLM checks).

        Each dict must have keys matching the result columns; missing keys
        default to None.
        """
        defaults = dict(
            evidence=None, matched_lemma=None, match_start=None, match_end=None,
            source_url=None, en_source=None, model=None,
        )
        with self._conn:
            self._conn.executemany(
                """INSERT OR REPLACE INTO results
                   (synset_id, check_type, item, verdict, evidence,
                    matched_lemma, match_start, match_end,
                    source_url, en_source, model, ts)
                   VALUES (:synset_id,:check_type,:item,:verdict,:evidence,
                           :matched_lemma,:match_start,:match_end,
                           :source_url,:en_source,:model,:ts)""",
                [{**defaults, **r, "ts": time.time()} for r in rows],
            )

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
