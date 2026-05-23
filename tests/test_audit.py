"""Tests for the wnja audit package (db, loader, checks, web_lookup, dev)."""
from __future__ import annotations

import csv
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from audit.db import AuditDB
from audit.loader import SynsetData, load_lmf
from audit.web_lookup import WebCache, KotobankLookup, WiktionaryLookup, _strip_html


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_synset(
    synset_id: str = "wnja-00000001-n",
    pos: str = "n",
    definitions: list[str] | None = None,
    examples: list[str] | None = None,
    forms: set[str] | None = None,
) -> SynsetData:
    return SynsetData(
        synset_id=synset_id,
        pos=pos,
        definitions=definitions or ["a test definition"],
        examples=examples or [],
        forms=forms or {"テスト"},
    )


def _temp_db() -> AuditDB:
    tmp = tempfile.mktemp(suffix=".db")
    return AuditDB(Path(tmp))


# ---------------------------------------------------------------------------
# AuditDB
# ---------------------------------------------------------------------------


class TestAuditDB:
    def test_save_and_is_done(self):
        db = _temp_db()
        assert not db.is_done("wnja-00000001-n", "definition")
        db.save_result("wnja-00000001-n", "definition", "", "OK", model="m1")
        assert db.is_done("wnja-00000001-n", "definition", model="m1")
        assert not db.is_done("wnja-00000001-n", "definition", model="m2")
        db.close()

    def test_save_many(self):
        db = _temp_db()
        rows = [
            dict(synset_id="wnja-00000001-n", check_type="definition", item="",
                 verdict="OK", model="m1"),
            dict(synset_id="wnja-00000002-n", check_type="definition", item="",
                 verdict="DRIFT", model="m1", evidence="slightly off"),
        ]
        db.save_many(rows)
        assert db.is_done("wnja-00000001-n", "definition", model="m1")
        assert db.is_done("wnja-00000002-n", "definition", model="m1")
        db.close()

    def test_register_run(self):
        db = _temp_db()
        run_id = db.register_run(model="gemma3", prompt_style="zero-shot")
        assert isinstance(run_id, int)
        db.close()

    def test_conn_property(self):
        db = _temp_db()
        assert isinstance(db.conn, sqlite3.Connection)
        db.close()

    def test_schema_version(self):
        db = _temp_db()
        row = db.conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        assert row is not None
        assert int(row[0]) >= 2
        db.close()

    def test_web_cache_table_exists(self):
        db = _temp_db()
        tables = {r[0] for r in db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "web_cache" in tables
        db.close()


# ---------------------------------------------------------------------------
# WebCache
# ---------------------------------------------------------------------------


class TestWebCache:
    def test_put_and_get(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE web_cache(url TEXT PRIMARY KEY, body TEXT, fetched_at INTEGER)"
        )
        cache = WebCache(conn)
        assert cache.get("https://example.com") is None
        cache.put("https://example.com", "<html>hello</html>")
        assert cache.get("https://example.com") == "<html>hello</html>"

    def test_put_overwrites(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE web_cache(url TEXT PRIMARY KEY, body TEXT, fetched_at INTEGER)"
        )
        cache = WebCache(conn)
        cache.put("https://example.com", "first")
        cache.put("https://example.com", "second")
        assert cache.get("https://example.com") == "second"


# ---------------------------------------------------------------------------
# _strip_html
# ---------------------------------------------------------------------------


def test_strip_html_basic():
    assert _strip_html("<p>hello <b>world</b></p>") == "hello world"


def test_strip_html_whitespace():
    assert _strip_html("a  <br/>  b") == "a b"


# ---------------------------------------------------------------------------
# KotobankLookup (mocked HTTP)
# ---------------------------------------------------------------------------


class TestKotobankLookup:
    def _make_cache(self, body: str) -> WebCache:
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE web_cache(url TEXT PRIMARY KEY, body TEXT, fetched_at INTEGER)"
        )
        cache = WebCache(conn)
        return cache

    def test_found(self):
        html = """
        <html><body>
        <section class="description">
          <p>テスト（名詞）試験のこと。品質を確かめるための行為。</p>
          <li>試験をすること</li>
        </section>
        </body></html>
        """
        cache = self._make_cache(html)
        kb = KotobankLookup(cache)
        # Inject into cache directly
        cache.put("https://kotobank.jp/word/%E3%83%86%E3%82%B9%E3%83%88", html)
        result = kb.lookup("テスト")
        assert result.found
        assert len(result.definitions) >= 1

    def test_not_found(self):
        html = "<html><body>見つかりません</body></html>"
        cache = self._make_cache(html)
        cache.put("https://kotobank.jp/word/%E3%82%B3%E3%82%A2", html)
        kb = KotobankLookup(cache)
        result = kb.lookup("コア")
        assert not result.found


# ---------------------------------------------------------------------------
# WiktionaryLookup (mocked HTTP)
# ---------------------------------------------------------------------------


class TestWiktionaryLookup:
    def _make_cache(self) -> WebCache:
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE web_cache(url TEXT PRIMARY KEY, body TEXT, fetched_at INTEGER)"
        )
        return WebCache(conn)

    def test_not_found_page_minus_one(self):
        import json
        data = {"query": {"pages": {"-1": {"missing": ""}}}}
        cache = self._make_cache()
        wk = WiktionaryLookup(cache)
        url = "https://ja.wiktionary.org/w/api.php?action=query&prop=extracts&explaintext=1&titles=%E3%83%86%E3%82%B9%E3%83%88&format=json&utf8=1"
        cache.put(url, json.dumps(data))
        result = wk.lookup("テスト")
        assert not result.found

    def test_found(self):
        import json
        extract = (
            "== 日本語 ==\n"
            "=== 名詞 ===\n"
            "1. 試験すること。品質・能力などを確かめるために行う検査。\n"
            "2. 学力試験のこと。\n"
        )
        data = {"query": {"pages": {"12345": {"title": "テスト", "extract": extract}}}}
        cache = self._make_cache()
        wk = WiktionaryLookup(cache)
        url = "https://ja.wiktionary.org/w/api.php?action=query&prop=extracts&explaintext=1&titles=%E3%83%86%E3%82%B9%E3%83%88&format=json&utf8=1"
        cache.put(url, json.dumps(data))
        result = wk.lookup("テスト")
        assert result.found
        assert len(result.definitions) >= 1


# ---------------------------------------------------------------------------
# Definition check: _parse_response
# ---------------------------------------------------------------------------


def test_definition_parse_response():
    from audit.checks.definitions import _parse_response

    response = (
        "wnja-00000001-n | OK | correct\n"
        "wnja-00000002-n | DRIFT | too narrow\n"
        "wnja-00000003-n | WRONG | totally different\n"
    )
    expected = ["wnja-00000001-n", "wnja-00000002-n", "wnja-00000003-n"]
    results = _parse_response(response, expected)
    assert results["wnja-00000001-n"][:2] == ("OK", "correct")
    assert results["wnja-00000002-n"][:2] == ("DRIFT", "too narrow")
    assert results["wnja-00000003-n"][:2] == ("WRONG", "totally different")


def test_definition_parse_response_verbose_fallback():
    from audit.checks.definitions import _parse_response

    response = (
        "<|channel>thought\n"
        "*   ID: wnja-00000001-n\n"
        "    *   EN: a test\n"
        "    *   JA: テスト\n"
        "    *   Verdict: OK.\n"
        "*   ID: wnja-00000002-n\n"
        "    *   EN: another\n"
        "    *   JA: 別の\n"
        "    *   Verdict: DRIFT.\n"
    )
    expected = ["wnja-00000001-n", "wnja-00000002-n"]
    results = _parse_response(response, expected)
    assert results["wnja-00000001-n"][0] == "OK"
    assert results["wnja-00000002-n"][0] == "DRIFT"


def test_definition_parse_response_missing():
    from audit.checks.definitions import _parse_response

    response = "wnja-00000001-n | OK | fine\n"
    results = _parse_response(response, ["wnja-00000001-n", "wnja-99999999-n"])
    assert "wnja-00000001-n" in results
    assert "wnja-99999999-n" not in results


def test_definition_parse_response_case_insensitive():
    from audit.checks.definitions import _parse_response

    response = "wnja-00000001-n | ok | fine\n"
    results = _parse_response(response, ["wnja-00000001-n"])
    assert results["wnja-00000001-n"][0] == "OK"


# ---------------------------------------------------------------------------
# Lemma check: _parse_response
# ---------------------------------------------------------------------------


def test_lemma_parse_response():
    from audit.checks.lemmas import _parse_response

    response = (
        "wnja-00000001-n | テスト | OK | correct\n"
        "wnja-00000002-n | 磯巾着 | MISSING | not a real word here\n"
        "wnja-00000003-n | 跳躍する | DUBIOUS | wrong synset\n"
    )
    expected = [
        ("wnja-00000001-n", "テスト"),
        ("wnja-00000002-n", "磯巾着"),
        ("wnja-00000003-n", "跳躍する"),
    ]
    results = _parse_response(response, expected)
    assert results[("wnja-00000001-n", "テスト")] == ("OK", "correct")
    assert results[("wnja-00000002-n", "磯巾着")][0] == "MISSING"
    assert results[("wnja-00000003-n", "跳躍する")][0] == "DUBIOUS"


# ---------------------------------------------------------------------------
# dev.py: load_gold
# ---------------------------------------------------------------------------


def test_load_gold_valid():
    from audit.dev import load_gold

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".tsv", delete=False, encoding="utf-8"
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["synset_id", "pos", "en_members", "en_def", "ja_def", "gold", "notes"],
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerow({
            "synset_id": "wnja-00000001-n", "pos": "n", "en_members": "test",
            "en_def": "a test", "ja_def": "テスト", "gold": "OK", "notes": "",
        })
        writer.writerow({
            "synset_id": "wnja-00000002-n", "pos": "n", "en_members": "ex",
            "en_def": "example", "ja_def": "例", "gold": "DRIFT", "notes": "shifted",
        })
        path = Path(f.name)

    gold = load_gold(path)
    assert gold["wnja-00000001-n"] == "OK"
    assert gold["wnja-00000002-n"] == "DRIFT"
    path.unlink()


def test_load_gold_invalid_verdict():
    from audit.dev import load_gold

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".tsv", delete=False, encoding="utf-8"
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["synset_id", "pos", "en_members", "en_def", "ja_def", "gold", "notes"],
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerow({
            "synset_id": "wnja-00000001-n", "pos": "n", "en_members": "t",
            "en_def": "a", "ja_def": "あ", "gold": "INVALID", "notes": "",
        })
        path = Path(f.name)

    with pytest.raises(ValueError, match="INVALID"):
        load_gold(path)
    path.unlink()


def test_load_gold_skips_empty():
    from audit.dev import load_gold

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".tsv", delete=False, encoding="utf-8"
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["synset_id", "pos", "en_members", "en_def", "ja_def", "gold", "notes"],
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerow({
            "synset_id": "wnja-00000001-n", "pos": "n", "en_members": "t",
            "en_def": "a", "ja_def": "あ", "gold": "", "notes": "",
        })
        path = Path(f.name)

    gold = load_gold(path)
    assert "wnja-00000001-n" not in gold
    path.unlink()
