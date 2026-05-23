"""Web lookup helpers for Stage 2: lemma appropriateness check.

Fetches Japanese dictionary entries from kotobank and ja.wiktionary,
with a SQLite-backed cache so each URL is fetched at most once.
Rate-limited to 1 request/second per host to be polite.

Usage::

    cache = WebCache(db_conn)
    kb = KotobankLookup(cache)
    wk = WiktionaryLookup(cache)

    result = kb.lookup("磯巾着")
    # result.found: bool
    # result.definitions: list[str]
    # result.pos_hints: list[str]   (e.g. ["名詞"], ["動詞"])
    # result.url: str
"""
from __future__ import annotations

import logging
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

_MIN_INTERVAL = 1.0  # seconds between requests to same host

# ---------------------------------------------------------------------------
# Cache layer (reuses AuditDB's web_cache table via raw sqlite3.Connection)
# ---------------------------------------------------------------------------


class WebCache:
    """Read-through cache backed by the `web_cache` table in audit.db.

    The table is created by AuditDB; this class just uses it via a
    shared sqlite3.Connection.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, url: str) -> str | None:
        row = self._conn.execute(
            "SELECT body FROM web_cache WHERE url=?", (url,)
        ).fetchone()
        return row[0] if row else None

    def put(self, url: str, body: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO web_cache(url, body, fetched_at) VALUES(?,?,?)",
            (url, body, int(time.time())),
        )
        self._conn.commit()


# ---------------------------------------------------------------------------
# HTTP helper with per-host rate limiting
# ---------------------------------------------------------------------------

_last_request: dict[str, float] = {}


def _fetch(url: str, *, timeout: int = 15) -> str:
    """Fetch *url*, rate-limited to 1 req/s per host, returning decoded HTML."""
    host = urllib.parse.urlparse(url).netloc
    now = time.time()
    wait = _MIN_INTERVAL - (now - _last_request.get(host, 0.0))
    if wait > 0:
        time.sleep(wait)
    _last_request[host] = time.time()

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "wnja-audit/2.0 (Japanese WordNet quality audit; "
                "contact: bond@ieee.org)"
            ),
            "Accept-Language": "ja,en;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            charset = "utf-8"
            ct = resp.headers.get_content_charset()
            if ct:
                charset = ct
            body = resp.read().decode(charset, errors="replace")
        log.debug("Fetched %s (%d chars)", url, len(body))
        return body
    except Exception as exc:
        log.warning("Fetch failed for %s: %s", url, exc)
        return ""


def _cached_fetch(url: str, cache: WebCache) -> str:
    body = cache.get(url)
    if body is not None:
        return body
    body = _fetch(url)
    if body:
        cache.put(url, body)
    return body


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class LookupResult:
    url: str
    found: bool = False
    definitions: list[str] = field(default_factory=list)
    pos_hints: list[str] = field(default_factory=list)
    raw_excerpt: str = ""


# ---------------------------------------------------------------------------
# Kotobank
# ---------------------------------------------------------------------------

_KOTOBANK_BASE = "https://kotobank.jp/word/{}"

# Strip HTML tags, collapse whitespace
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")


def _strip_html(text: str) -> str:
    return _SPACE_RE.sub(" ", _TAG_RE.sub(" ", text)).strip()


# Kotobank wraps dictionary entries in <section class="description"> or
# <article class="..."> — we look for common Japanese POS indicators.
_POS_WORDS = ["名詞", "動詞", "形容詞", "形容動詞", "副詞", "接続詞", "感動詞",
              "助動詞", "助詞", "接頭語", "接尾語", "連体詞"]
_DEF_BLOCK_RE = re.compile(
    r'(?:class="description"|<article[^>]*>)(.*?)(?:</article>|</section>)',
    re.DOTALL | re.IGNORECASE,
)
_LI_RE = re.compile(r"<li[^>]*>(.*?)</li>", re.DOTALL | re.IGNORECASE)
_P_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.DOTALL | re.IGNORECASE)


class KotobankLookup:
    """Look up a Japanese word on kotobank.jp."""

    def __init__(self, cache: WebCache) -> None:
        self._cache = cache

    def lookup(self, lemma: str) -> LookupResult:
        encoded = urllib.parse.quote(lemma)
        url = _KOTOBANK_BASE.format(encoded)
        result = LookupResult(url=url)
        body = _cached_fetch(url, self._cache)
        if not body:
            return result

        # Check for "見つかりません" (not found) pages
        if "見つかりません" in body or "検索結果がありません" in body:
            return result

        # Heuristic: if the lemma appears in the page body, consider it found
        result.found = lemma in body

        # Extract text from description blocks
        excerpts: list[str] = []
        for block in _DEF_BLOCK_RE.finditer(body):
            content = block.group(1)
            for m in _LI_RE.finditer(content):
                excerpts.append(_strip_html(m.group(1)))
            for m in _P_RE.finditer(content):
                text = _strip_html(m.group(1))
                if text:
                    excerpts.append(text)

        # Keep non-trivial excerpts (≥10 chars)
        excerpts = [e for e in excerpts if len(e) >= 10][:10]
        result.definitions = excerpts

        # POS hints: look for 品詞 markers in the raw HTML
        result.pos_hints = [pw for pw in _POS_WORDS if pw in body]
        result.raw_excerpt = " | ".join(excerpts[:3])

        return result


# ---------------------------------------------------------------------------
# Japanese Wiktionary (ja.wiktionary.org) — simple API approach
# ---------------------------------------------------------------------------

_WIKT_API = (
    "https://ja.wiktionary.org/w/api.php"
    "?action=query&prop=extracts&explaintext=1&titles={}&format=json&utf8=1"
)


class WiktionaryLookup:
    """Look up a Japanese word on ja.wiktionary.org using the MediaWiki API."""

    def __init__(self, cache: WebCache) -> None:
        self._cache = cache

    def lookup(self, lemma: str) -> LookupResult:
        encoded = urllib.parse.quote(lemma)
        url = _WIKT_API.format(encoded)
        result = LookupResult(url=url)
        body = _cached_fetch(url, self._cache)
        if not body:
            return result

        import json
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return result

        pages = data.get("query", {}).get("pages", {})
        for page_id, page in pages.items():
            if page_id == "-1":
                return result  # not found
            extract = page.get("extract", "")
            if not extract:
                return result

            result.found = True
            result.raw_excerpt = extract[:500]

            # Extract definitions: lines that look like numbered entries or
            # follow 意味, 名詞, 動詞 headers
            lines = extract.splitlines()
            defs: list[str] = []
            in_ja_section = False
            for line in lines:
                line = line.strip()
                if "日本語" in line:
                    in_ja_section = True
                if not in_ja_section:
                    continue
                # Definition lines typically start with a number or bullet
                if re.match(r"^\d+[\.\)）]|^[#＃]\s*\d", line) and len(line) > 5:
                    defs.append(re.sub(r"^\d+[\.\)）\s]+|^[#＃\s]+", "", line).strip())
                elif line and not line.startswith("==") and len(line) >= 10:
                    defs.append(line)
                if len(defs) >= 5:
                    break

            result.definitions = defs[:5]

            # POS hints from section headers
            result.pos_hints = [pw for pw in _POS_WORDS if pw in extract]
            break

        return result


# ---------------------------------------------------------------------------
# Combined lookup (kotobank + wiktionary)
# ---------------------------------------------------------------------------


def lookup_lemma(
    lemma: str,
    cache: WebCache,
    *,
    use_kotobank: bool = True,
    use_wiktionary: bool = True,
) -> dict[str, LookupResult]:
    """Look up *lemma* on all configured sources.

    Returns a dict ``{"kotobank": result, "wiktionary": result}``
    (only sources that were enabled).
    """
    results: dict[str, LookupResult] = {}
    if use_kotobank:
        results["kotobank"] = KotobankLookup(cache).lookup(lemma)
    if use_wiktionary:
        results["wiktionary"] = WiktionaryLookup(cache).lookup(lemma)
    return results
