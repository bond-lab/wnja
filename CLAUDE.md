# wnja — Japanese WordNet v2.0

## Layout

```
build_wnja.py tweak_wnja.py package_wnja.py   the release pipeline (all paths defined in build_wnja.py)
scripts/          data preparation, run as `python -m scripts.NAME`; writes data/derived/
data/sources/     input data (hand-made or imported)
data/derived/     made from the sources by scripts/
corrections/      applied by the build: definitions.tsv, senses.tsv
corrections/review/  candidates waiting for review, NOT applied
audit/            LLM audit code; audit/reviews/ holds review data (issue8/, jsemcor/)
tests/            pytest
docs/             the web site (GitHub Pages)
build/            all output, ignored: wnja-VERSION.xml, *.log, reports/, release/
```

## Build sequence

```bash
uv run python -m scripts.detect_sahen       # only when jamdict data changes
uv run python -m scripts.rank_readings      # only when vars_tk17.tab changes (~4 min, jamdict)
uv run python -m scripts.count_jsemcor      # only when ../jsemcor/data changes
uv run python -m scripts.kuroda_adjectives  # only when adjective_kuroda_2011.tsv changes
uv run python -m scripts.agentive_links     # only when agents_240619.xlsx or corrections/agentive-removed.tsv changes
uv run python build_wnja.py                 # -> build/wnja-VERSION.xml (VERSION in build_wnja.py), build/wnja.log
uv run python tweak_wnja.py
uv run python -m scripts.report_wnja        # optional quality report -> build/reports/
uv run python package_wnja.py               # -> build/release/wnja-VERSION.tar.xz
uv run pytest tests/                        # ~85 tests; most need a rebuilt build/wnja-VERSION.xml
```

## Data sources

| File | Description |
|------|-------------|
| `data/sources/wn-ntumc-jpn.xml` | NTU-MC Japanese export (WN-LMF). Not in git: `build_wnja.py` downloads the pinned export (`NTUMC_XML_URL`, checked against `NTUMC_XML_XZ_SHA256`) when it is missing. Regenerate via `getwn.py` (see below). |
| `data/sources/vars_tk17.tab` | Orthographic forms per (lemma, hno) |
| `data/sources/wn+var_tk17.tab` | Authoritative (synset, lemma, hno) mappings with confidence scores |
| `data/sources/adjective_kuroda_2011.tsv` | Kuroda's hand-marked adjective endings, per (synset, lemma) (issue #9) |
| `data/sources/agents_240619.xlsx` | Bond and Wei (2019): verb–agentive noun pairs (eng, cmn, jpn), checked twice by hand; method in `agents_tagging_report_2019.docx` |
| `data/derived/sahen_verbs.txt` | Suru-verb list (scripts/detect_sahen.py + jamdict) |
| `data/derived/reading_rank.tsv` | JMdict frequency rank of each reading of a homograph (scripts/rank_readings.py) |
| `data/derived/adjective_class.tsv` | Adjective class per lemma: na, no, na-no, no-na, other (scripts/kuroda_adjectives.py) |
| `data/derived/agentive_links.tsv` | Accepted Japanese verb → agentive noun links, typed (scripts/agentive_links.py); applied by tweak_wnja.py as `other`/dc:type + `derivation` |
| `data/derived/wn-freq-jpn-jsemcor.tsv` | Sense counts from the Japanese SemCor (scripts/count_jsemcor.py) |
| `../NTUMC/build/ili-map-pwn30.tab` | ILI ↔ PWN30 synset number map (used by build_wnja.py for stubs) |

### Regenerating wn-ntumc-jpn.xml

```bash
cd /home/bond/git/NTUMC
/home/bond/git/wnja/.venv/bin/python scripts/getwn.py \
    build/wn-ntumc.db build/ \
    --ili build/ili-map-pwn30.tab \
    --version "2026.10" \
    --base "omw-en:2.0"
cp build/wn-ntumc-jpn.xml /home/bond/git/wnja/data/sources/
```

For a release, attach `xz -9` of the export to an NTU-MC GitHub release
(e.g. `wn-2026.10`, with the cmn/ind/zsm exports for COW and Wordnet
Bahasa) and update `NTUMC_XML_URL`, `NTUMC_XML_XZ_SHA256` and
`NTUMC_XML_SHA256` in `build_wnja.py`; a local file that differs from the
pinned one is used with a warning.

Key fix already applied: `getwn.py` line ~405 uses `"nvartux"` (not `"nvartu"`) to include x-POS entries.

## Pipeline architecture

1. `read_vars()` — `{(lemma, hno): [(writtenForm, script), ...]}`
2. `read_wnvar()` — `{(lemma, hno): [(wnja_synset_id, confidence)]}` + `covered_synsets`
3. `parse_ntumc_xml()` — NTU-MC entries list + synsets dict
4. `build_entries()` — LexicalEntry dicts from wn+var. Merges entries with same (lemma, pos, reading): a word is a spelling plus a reading (katakana form), so homographs such as 反る ソル/カエル stay separate, while two hno with the same reading merge. Corpus counts (NTU-MC, JSemCor; one `<Count dc:source=…>` each) go to one reading per (synset, lemma), chosen by `count_owners()` from `data/derived/reading_rank.tsv`, so they are not counted twice.
5. `build_passthrough_entries()` — NTU-MC entries for synsets NOT in `covered_synsets` (Japan-specific). A lemma can appear in both pipelines; passthrough entries take the hno 0 reading and merge into that entry (or the first entry for the lemma if it has no vars forms).
6. Relation-target expansion — adds synsets referenced by relations that have NTU-MC definitions.
7. `build_synsets()` — deduplicates definition texts; normalises POS `s` → `a`.
8. `build_stub_synsets()` — stub synsets for still-missing relation targets: ILI from `ili-map-pwn30.tab` + English definition from `omw-en:2.0`. Resolves E401 errors.
9. Export via `wn.lmf.dump()` (WN-LMF 1.4 with correct DOCTYPE).

`tweak_wnja.py` uses `WordnetEditor.load_from_file()` / `editor.export()` for round-trips —
**never** raw `ET.write()`, which strips the DOCTYPE and breaks `wn validate`.

## ID conventions

- NTU-MC: `ntumc-ja-XXXXXXXX-p` → strip prefix → `XXXXXXXX-p` → `wnja-XXXXXXXX-p`
- Japan-specific synsets: `wnja-70xxxx` through `wnja-95xxxx`
- Pronouns / JP function words: `wnja-77xxxx-n`
- Classifiers (x-POS): `wnja-761xxxx-x`
- Greetings / exclamatives (x-POS): `wnja-800xxxx-x`
- Confidence: `hand`=1.0, `mlsn`=0.91, `multi`=0.72, `mono`=0.71

## Current validation status (wn validate, 2026-10-24)

| Code | Count | Notes |
|------|------:|-------|
| E401 | 0 | Fixed: stub synsets for all missing PWN relation targets |
| W203 | 1,459 | Homographs (same lemma and pos, different reading) — by design |
| W202 | 0 | Fixed: cross-pipeline entries merge by (lemma, pos, reading) |
| W301 | 9,644 | PWN orphans (expected) + 2,510 stubs (empty by design) |
| W307 | 657 | Cross-synset shared definition texts — data issue |
| W404 | 2,509 | Stubs have no reverse relations (by design) |
| W501 | crash | Upstream wn validator bug — filed for reporting |

## Audit pipeline (LLM definition check)

### Running the eval
```bash
# Run against the 150-synset dev set (one model at a time — GPU-bound)
uv run python -m audit.cli \
    --lmf build/wnja-VERSION.xml \
    --ref-lmf wn-ntumc-eng.xml \
    --check definitions \
    --db audit_dev.db \
    --model qwen3.5:latest \
    --prompt-style zero-shot \
    --synset-file audit/dev_set.tsv

# View comparison table
uv run python -m audit.dev table --gold audit/dev_set.tsv --db audit_dev.db
```

### Inference best practices (mlx_lm)
- **Backend**: `mlx_lm` via `audit/llm.py` `Generator` class. Loads model into
  Apple Silicon unified memory; uses `apply_chat_template` for instruct models.
- **Output format**: pipe-delimited text — `<ID> | OK|DRIFT|WRONG | note [| SUGGESTED: ...]`.
  Parser strips `<think>…</think>` blocks, tries pipe-delimited then verbose block fallback.
  A cleanup pass retries any parse failures one synset at a time.
- **Temperature**: defaults to 0.0 for deterministic output.
- **Parallelism**: run one model at a time — inference is GPU-bound and
  concurrent jobs share unified memory, causing thrashing.
- **Model sizes**: larger models give substantially better F1.
  Qwen3-32B-4bit (mlx): F1=0.72 zero-shot on full 147-synset gold set.
  Use the largest model that fits in VRAM for production audits.
- **Prompt style**: zero-shot is best for Qwen3-32B (F1=0.72); few-shot improves
  WRONG-recall but hurts DRIFT recall. Smaller models benefit less from examples.
- **Running the eval**:
```bash
uv run python -m audit.cli \
    --lmf build/wnja-VERSION.xml \
    --ref-lmf wn-ntumc-eng.xml \
    --check definitions \
    --db audit_dev.db \
    --model mlx-community/Qwen3-32B-4bit \
    --prompt-style zero-shot \
    --synset-file audit/dev_set.tsv
```

### Dev set eval results (2026-05-24, gold=147 synsets OK=109 DRIFT=27 WRONG=11)

All results on the full 150-synset dev set (147 gold-labelled).
Best WRONG-F1: Qwen3-32B few-shot (P=0.88 R=0.64 F1=0.74).
**Production pipeline**: Stage 1 = Qwen3.6-35B-A3B-4bit few-shot (R=0.64, architectural diversity);
Stage 2 = Qwen3-32B-4bit few-shot (P=0.88). MoE is same speed as 32B on Apple Silicon
(memory-bandwidth bound); chosen for independent model family, not speed.

| Model | Style | F1 | WRONG-F1 | WRONG-P | WRONG-R |
|-------|-------|----|----------|---------|---------|
| Qwen3-32B-4bit (mlx) | zero-shot | 0.72 | 0.67 | 0.80 | 0.55 |
| Qwen3-32B-4bit (mlx) | one-shot | 0.61 | 0.40 | 0.57 | 0.36 |
| Qwen3-32B-4bit (mlx) | few-shot | 0.68 | **0.74** | **0.88** | 0.64 |
| Qwen3.6-35B-A3B-4bit (mlx) | few-shot | 0.63 | 0.56 | 0.50 | **0.64** |
| qwen3.6:35b (Ollama GGUF) | zero-shot | 0.57 | 0.47 | 0.50 | 0.45 |
| qwen3.6:35b (Ollama GGUF) | few-shot | 0.68 | 0.62 | 0.48 | 0.73 |
| qwen3.5:latest (Ollama, 9.7B) | zero-shot | 0.53 | 0.33 | — | — |
| qwen3.5:latest (Ollama, 9.7B) | few-shot | 0.47 | 0.17 | — | — |
| gemma-4-31b-it-4bit (mlx) | zero-shot | 0.48 | 0.29 | — | — |
| gemma-4-31b-it-4bit (mlx) | few-shot | 0.49 | 0.29 | — | — |

## Known gaps and planned work

- **Release**: tag and package after further testing
- **Yojijukugo**: ~364 four-character idiom synsets need Japanese entries in NTU-MC DB
- **Missing vocabulary**: よいしょ, いただきます, ごちそうさま, よろしく, もったいない,
  木漏れ日, 引きこもり, 過労死, 婚活, 一石二鳥, 一期一会, わびさび, …
- **W307**: per-case review of 657 cross-synset duplicate definitions
- **Sense-level antonyms**: temperature antonyms currently synset-level; best practice is sense-level
- **Adjective classes**: Kuroda's classes are merged as Lemma tags (`adjective-class`); 1,116 hand-marked or unclassifiable lines await review in `corrections/review/kuroda-adjective-review.tsv`
- **JSemCor suggested senses** (issue #11): `scripts/jsemcor_suggestions.py` wrote 2,785 candidate (synset, lemma) senses to `corrections/review/jsemcor-suggestions.tsv` from JSemCor 1.1 in `../jsemcor` (the aligned `data/` and `traduku/traduku.sqlite.xz`); reviewed blind twice plus adjudication in `audit/reviews/jsemcor/` (`uv run python -m audit.jsemcor_review batches|combine|apply`). 1,347 accepted senses are `add` rows in `corrections/senses.tsv`; **196 still need a human**: `corrections/review/jsemcor-manual-review.tsv` (179 POS without a single existing corrected form, 17 MWE)
- **Main NTU-MC database**: the wnja fixes in NTU-MC `scripts/fixwn.py` were applied to the local `build/wn-ntumc.db` only. Run its DEF, EX, SENSE, EXCL and JSEM sections on the main database (they are idempotent; run them with the logging triggers kept)
- **Merge TUFS data**

