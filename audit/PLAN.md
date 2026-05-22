# wnja Quality Audit — Detailed Plan

## Overview

Three independent checks over the wnja LMF output, run as separate passes sharing a SQLite
checkpoint database. Designed to be reusable for OMW-cmn and OMW-ind with flag changes only.

---

## Data sources and joins

| Source | Contents | Used for |
|--------|----------|----------|
| `wnja.xml` (build output) | Japanese definitions, examples, lemmas | All checks |
| `/home/bond/git/NTUMC/build/wn-ntumc-eng.xml` | English definitions for ~120K synsets | Definition check (primary English) |
| `omw-en:2.0` (via `wn`) | Standard PWN English definitions | Definition check (fallback) |
| Kotobank, ja.wiktionary | Dictionary evidence for lemmas | Lemma check, cached |

**ID join:** `wnja-XXXXXXXX-p` ↔ `ntumc-en-XXXXXXXX-p` — just replace the prefix. Japan-specific
synsets (`wnja-70xxxx` through `wnja-95xxxx`) have no PWN counterpart but may still appear in
ntumc-eng.

**English source priority:** ntumc-eng.xml first (closer to what the translators worked from),
omw-en:2.0 as fallback. Flag `en_source` in output so discrepancies are traceable.

**Important:** `omw-en` is currently loaded three times in the wn DB. Before querying, check
lexicon versions with `{(lx.id, lx.version) for lx in wn.lexicons()}` and use
`lexicon="omw-en:2.0"` explicitly to avoid ambiguity errors.

---

## Repository layout

```
audit/
  db.py              — SQLite layer: write results, skip already-done rows, cache web fetches
  loader.py          — parse wnja.xml + ntumc-eng.xml; build synset cross-reference dict
  tokenizer.py       — pluggable segmenters: jpn=fugashi, cmn=jieba, ind=str.split
  web_lookup.py      — kotobank + ja.wiktionary fetch with 1 req/s rate limit
  llm.py             — MLX interface; prompt templates; response parser
  checks/
    examples.py      — Stage 0: programmatic example↔lemma check (no LLM)
    definitions.py   — Stage 1: batched LLM definition accuracy
    lemmas.py        — Stage 2: web-grounded LLM lemma appropriateness
  report.py          — export TSV and HTML summary from audit.db
  cli.py             — argparse entry point
```

---

## Database schema (`audit.db`)

```sql
CREATE TABLE results (
    synset_id   TEXT NOT NULL,
    check_type  TEXT NOT NULL,   -- 'example', 'definition', 'lemma'
    item        TEXT,            -- example text, or lemma under scrutiny
    verdict     TEXT NOT NULL,   -- 'OK', 'DRIFT', 'WRONG', 'MISMATCH', 'DOUBTFUL', 'NO'
    evidence    TEXT,
    source_url  TEXT,
    model       TEXT,
    ts          REAL,
    PRIMARY KEY (synset_id, check_type, item)
);

CREATE TABLE web_cache (
    url         TEXT PRIMARY KEY,
    content     TEXT,
    fetched_at  REAL
);
```

Every script skips rows already in `results` on restart, so a crash loses at most one batch.

---

## Stage 0: Example↔lemma check (programmatic, no LLM)

**Goal:** flag examples that contain no form of any synset lemma — likely copy-pasted from a
different synset, or using an orthographic variant not in `vars_tk17`.

**Algorithm:**
1. For each synset with at least one example:
   - Collect all `writtenForm` values for every lemma in the synset (from `vars_tk17` data,
     not just the canonical form)
   - Tokenise the example with `fugashi` (MeCab), collect surface forms and base forms
   - If the intersection is empty → flag `MISMATCH`
2. For `MISMATCH` cases, also run a quick LLM check: "Does this example sentence illustrate
   the concept [definition]?" — catches cases where the example is thematically correct but
   uses a synonym or pronoun instead of the lemma

**Output:** rows in `results` with `check_type='example'`, verdict `OK` or `MISMATCH`.

**Estimated time:** minutes (MeCab is fast; LLM only on flagged subset).

**Reuse for Chinese/Indonesian:** swap `fugashi` for `jieba` (cmn) or `str.split` (ind).
The rest is identical.

---

## Stage 1: Definition accuracy (batched LLM)

**Goal:** flag Japanese definitions that mistranslate, drift from, or contradict their English
source.

**Algorithm:**
1. For each synset, retrieve:
   - English definition from ntumc-eng.xml (preferred) or omw-en:2.0 (fallback)
   - Japanese definition from wnja.xml
   - POS + a few English member lemmas (for context)
2. Batch 10 synsets per prompt
3. LLM verdict per synset: `OK` / `DRIFT` / `WRONG`

**Prompt template:**

```
For each synset below, decide whether the Japanese definition accurately conveys the same
meaning as the English.

Reply with exactly one line per synset in the format:
  <id> | OK | [brief note]        — faithful translation
  <id> | DRIFT | [brief note]     — related but imprecise or shifted
  <id> | WRONG | [brief note]     — substantially different meaning

Synsets:
1. ID: wnja-00001740-a
   EN (adj): (of a person) having a strong desire or impulse [English members: able]
   JA: 強い欲求または衝動を持つ（人について）

2. ID: wnja-00002312-a
   EN (adj): facing away from the axis of an organ or organism
   JA: 器官または生物の軸から離れた方向を向いている
...
```

**Parsing:** extract `<id> | VERDICT | evidence` with a simple regex; retry the batch if
parsing fails.

**Estimated time (M4 Max, MLX Gemma 3 27B Q4, ~50 tok/s generation):**
- 119,000 synsets ÷ 10/batch = 11,900 prompts
- ~800 tokens input + ~200 tokens output per batch
- Generation dominates: 200 tok ÷ 50 tok/s = 4 s per batch
- Total: ~13 hours — run overnight

**Reuse:** fully language-agnostic; change `--ref-lmf` and `--lmf` flags only.

---

## Stage 2: Lemma appropriateness (web-grounded LLM)

**Goal:** flag lemmas that do not belong in their assigned synset (e.g., 磯巾着 in a
"follower/planet" synset).

**This stage runs one lemma at a time, not batched, because external evidence varies per lemma.**

### Step 2a — Pre-filter (programmatic, no LLM)

Flag lemmas that are *absent from JMdict* (using `jamdict`) as high-priority candidates.
Rare/unknown lemmas are not necessarily wrong, but they're worth checking. Also flag any lemma
where the synset has a very low confidence score (e.g., `mono`=0.71) — these came from
monosemous automatic alignment and are more likely to be errors.

### Step 2b — Web fetch

For each flagged lemma, fetch (with 1 req/s rate limit, results cached in `web_cache`):
- `https://kotobank.jp/search?q={lemma}&t=ja` → extract first definition/category
- `https://ja.wiktionary.org/wiki/{lemma}` → extract first definition line

### Step 2c — LLM check

```
Synset concept: facing away from the axis of an organ or organism (adj)
  / 器官または生物の軸から離れた方向を向いている
English members: abaxial, dorsal

Lemma under review: 磯巾着

Kotobank says: 磯巾着（いそぎんちゃく）— 刺胞動物門花虫綱に属する海産動物。
Wiktionary says: 磯巾着 — イソギンチャクの別名。海産無脊椎動物。

Is 磯巾着 a valid Japanese word for the concept described?
Reply: YES | DOUBTFUL | NO — one sentence of evidence.
```

`DOUBTFUL` cases go into a separate table for human review or interactive Claude second-pass
with web access.

**Estimated time:** if ~3,000 lemmas are flagged, web fetch takes ~1 hour (rate-limited);
LLM check at ~10 s/lemma = ~8 hours.

**Note on confidence scores:** if `build_wnja.py` / `tweak_wnja.py` preserve confidence
metadata in the LMF, read it directly. Otherwise join back to `wn+var_tk17.tab` which has
the scores.

---

## Command-line interface

```bash
# Stage 0 only
python -m audit.cli --lmf build/wnja.xml --lang jpn --check examples

# Stage 1 (definition accuracy), full run
python -m audit.cli --lmf build/wnja.xml --lang jpn \
    --ref-lmf /home/bond/git/NTUMC/build/wn-ntumc-eng.xml \
    --check definitions --model mlx-community/gemma-3-27b-it-4bit \
    --batch-size 10 --db audit.db

# Stage 2 (lemma check), flagged candidates only
python -m audit.cli --lmf build/wnja.xml --lang jpn \
    --ref-lmf /home/bond/git/NTUMC/build/wn-ntumc-eng.xml \
    --check lemmas --db audit.db

# Export results
python -m audit.cli --report --db audit.db --out reports/audit_wnja.tsv
```

For Chinese (later):
```bash
python -m audit.cli --lmf /path/to/wn-ntumc-cmn.xml --lang cmn \
    --ref-lmf /home/bond/git/NTUMC/build/wn-ntumc-eng.xml \
    --check definitions --db audit_cmn.db
```

---

## Output format

TSV columns for human review and for feeding back into `tweak_wnja.py`:

```
synset_id        check_type   item      verdict   evidence                         source_url
wnja-00001740-a  definition   (null)    OK        faithful translation              (null)
wnja-03000001-n  lemma        磯巾着    NO        kotobank: sea anemone, not follower  https://kotobank.jp/...
wnja-00100200-v  example      文…       MISMATCH  example uses 泳ぐ, not in lemma set  (null)
```

HTML report groups by verdict and severity, sorted by `NO` > `WRONG` > `MISMATCH` > `DRIFT`
> `DOUBTFUL`.

---

## Open questions for review

1. **Which English source to prefer?** ntumc-eng.xml is recommended (closer to translators'
   source), but some synsets have slightly different definitions there vs. omw-en. The plan
   is to use ntumc-eng first and flag which source was used — but this could be changed.

2. **Confidence threshold for lemma pre-filter (Stage 2a):** `mono`=0.71 and below? Or
   include `multi`=0.72? Worth deciding before running so the pre-filter size is manageable.

3. **Second-pass Claude review:** the plan is that Claude handles `DOUBTFUL` cases
   interactively, fetching web evidence where needed. Any objection to this?

4. **Scope of definition check:** should it run over all 119K synsets including those without
   Japanese lemmas (definition-only synsets)? The script handles this, but clarifying the
   scope affects Stage 1 runtime.

5. **Kotobank rate limits:** kotobank may throttle aggressive fetching. The 1 req/s ceiling
   is conservative — adjustable without changing the rest of the pipeline.
