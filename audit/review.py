"""TOML digest generation and decision loading for the two-stage review workflow.

Workflow
--------
1. ``generate_digest(rows, meta, outpath)`` — write a TOML file with one
   ``[[cases]]`` entry per reviewed synset.  The user fills in ``decision``
   and optionally ``new_suggestion`` for each case.

2. ``load_decisions(db, toml_path, s1_model, s1_style, s2_model, s2_style)``
   — read the edited TOML back and persist user decisions to the DB.

TOML structure
--------------
::

    [meta]
    date = "2026-05-25"
    batch = 1
    stage1_model = "mlx-community/Qwen3.6-35B-A3B-4bit"
    ...

    [[cases]]
    synset_id = "wnja-06023476-n"
    bucket = "change"
    claude_verdict = "change"
    en_def = "..."
    ja_def = "..."
    issue = "..."
    suggestion = "..."
    stage1_note = "..."
    stage2_note = "..."
    decision = ""          # user fills: approved | rejected | modified
    new_suggestion = ""    # only when decision = modified
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from audit.db import AuditDB

# ---------------------------------------------------------------------------
# Digest generation
# ---------------------------------------------------------------------------

def _toml_str(value: str | None) -> str:
    """Escape a string for TOML inline value (double-quoted)."""
    if not value:
        return '""'
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def generate_digest(
    rows: list[dict],
    meta: dict,
    outpath: Path,
    ja_defs: dict[str, str] | None = None,
    en_defs: dict[str, str] | None = None,
) -> None:
    """Write a TOML review digest.

    Args:
        rows: Dicts from ``AuditDB.get_unreviewed_batch()`` augmented with
              ``claude_verdict``, ``claude_reasoning``, ``claude_suggestion``.
        meta: Dict with keys: date, batch, stage1_model, stage1_style,
              stage2_model, stage2_style.
        outpath: Destination path for the TOML file.
        ja_defs: Optional {synset_id: ja_definition} for display.
        en_defs: Optional {synset_id: en_definition} for display.
    """
    lines: list[str] = []

    n_change   = sum(1 for r in rows if r.get("claude_verdict") == "change")
    n_uncertain = sum(1 for r in rows if r.get("claude_verdict") == "check")
    n_keep     = sum(1 for r in rows if r.get("claude_verdict") == "keep")

    lines.append("[meta]")
    lines.append(f'date = {_toml_str(meta.get("date", ""))}')
    lines.append(f'batch = {meta.get("batch", 1)}')
    lines.append(f'stage1_model = {_toml_str(meta.get("stage1_model", ""))}')
    lines.append(f'stage1_style = {_toml_str(meta.get("stage1_style", ""))}')
    lines.append(f'stage2_model = {_toml_str(meta.get("stage2_model", ""))}')
    lines.append(f'stage2_style = {_toml_str(meta.get("stage2_style", ""))}')
    lines.append(f"n_change = {n_change}")
    lines.append(f"n_check = {n_uncertain}")
    lines.append(f"n_keep = {n_keep}")
    lines.append("")

    for row in rows:
        sid = row["synset_id"]
        cv  = row.get("claude_verdict", "")
        # Skip 'keep' cases — no action needed from user
        if cv == "keep":
            continue

        ja  = (ja_defs or {}).get(sid, "")
        en  = (en_defs or {}).get(sid, "")
        s1_note = row.get("s1_note") or ""
        s2_note = row.get("s2_note") or ""
        s1_sug  = row.get("s1_suggestion") or ""
        s2_sug  = row.get("s2_suggestion") or ""
        suggestion = row.get("claude_suggestion") or s1_sug or s2_sug

        lines.append("[[cases]]")
        lines.append(f"synset_id = {_toml_str(sid)}")
        lines.append(f"bucket = {_toml_str(row.get('bucket', ''))}")
        lines.append(f"claude_verdict = {_toml_str(cv)}")
        lines.append(f"en_def = {_toml_str(en)}")
        lines.append(f"ja_def = {_toml_str(ja)}")
        lines.append(f"issue = {_toml_str(row.get('claude_reasoning', ''))}")
        lines.append(f"suggestion = {_toml_str(suggestion)}")
        lines.append(f"stage1_note = {_toml_str(s1_note)}")
        lines.append(f"stage2_note = {_toml_str(s2_note)}")
        lines.append('decision = ""      # approved | rejected | modified')
        lines.append('new_suggestion = ""  # only when decision = modified')
        lines.append("")

    outpath.parent.mkdir(parents=True, exist_ok=True)
    outpath.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Decision loading
# ---------------------------------------------------------------------------

_CASE_BLOCK_RE = re.compile(
    r'\[\[cases\]\](.*?)(?=\[\[cases\]\]|\Z)', re.DOTALL
)
_KV_RE = re.compile(r'^(\w+)\s*=\s*"((?:[^"\\]|\\.)*)"', re.MULTILINE)


def _parse_toml_cases(text: str) -> list[dict[str, str]]:
    """Extract [[cases]] blocks and their key-value pairs."""
    cases = []
    for block_m in _CASE_BLOCK_RE.finditer(text):
        block = block_m.group(1)
        kv = {m.group(1): m.group(2) for m in _KV_RE.finditer(block)}
        if kv:
            cases.append(kv)
    return cases


def load_decisions(
    db: "AuditDB",
    toml_path: Path,
    s1_model: str,
    s1_style: str,
    s2_model: str,
    s2_style: str,
) -> dict[str, int]:
    """Read user decisions from an edited TOML digest and persist to DB.

    Returns:
        Dict with keys 'approved', 'rejected', 'modified', 'skipped'.
    """
    text = toml_path.read_text(encoding="utf-8")
    cases = _parse_toml_cases(text)

    counts: dict[str, int] = {"approved": 0, "rejected": 0, "modified": 0, "skipped": 0}
    for case in cases:
        sid = case.get("synset_id", "").strip()
        decision = case.get("decision", "").strip().lower()
        if not sid or decision not in ("approved", "rejected", "modified"):
            counts["skipped"] += 1
            continue
        new_sug = case.get("new_suggestion", "").strip() or None
        if decision == "modified" and not new_sug:
            counts["skipped"] += 1
            continue
        db.save_user_decision(
            sid, s1_model, s1_style, s2_model, s2_style,
            decision=decision,
            suggestion=new_sug,
        )
        counts[decision] += 1

    return counts
