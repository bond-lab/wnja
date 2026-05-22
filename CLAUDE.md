# wnja — Japanese WordNet v2.0

## Build sequence

```bash
uv run python detect_sahen.py    # only needed when jamdict data changes
uv run python build_wnja.py
uv run python tweak_wnja.py
uv run python report_wnja.py     # optional quality report → reports/
uv run pytest tests/             # 38 tests, all should pass
```

## Data sources

| File | Description |
|------|-------------|
| `data/wn-ntumc-jpn.xml` | NTU-MC Japanese export (WN-LMF). Regenerate via `getwn.py` (see below). |
| `data/vars_tk17.tab` | Orthographic forms per (lemma, hno) |
| `data/wn+var_tk17.tab` | Authoritative (synset, lemma, hno) mappings with confidence scores |
| `data/sahen_verbs.txt` | Pre-computed suru-verb list from detect_sahen.py + jamdict |
| `../NTUMC/build/ili-map-pwn30.tab` | ILI ↔ PWN30 synset number map (used by build_wnja.py for stubs) |

### Regenerating wn-ntumc-jpn.xml

```bash
cd /home/bond/git/NTUMC
/home/bond/git/wnja/.venv/bin/python scripts/getwn.py \
    build/wn-ntumc.db build/ \
    --ili build/ili-map-pwn30.tab \
    --version "2026.02" \
    --base "omw-en:2.0"
cp build/wn-ntumc-jpn.xml /home/bond/git/wnja/data/
```

Key fix already applied: `getwn.py` line ~405 uses `"nvartux"` (not `"nvartu"`) to include x-POS entries.

## Pipeline architecture

1. `read_vars()` — `{(lemma, hno): [(writtenForm, script), ...]}`
2. `read_wnvar()` — `{(lemma, hno): [(wnja_synset_id, confidence)]}` + `covered_synsets`
3. `parse_ntumc_xml()` — NTU-MC entries list + synsets dict
4. `build_entries()` — LexicalEntry dicts from wn+var. Merges entries with same (lemma, pos) by unioning senses AND forms (handles multiple-hno homographs).
5. `build_passthrough_entries()` — NTU-MC entries for synsets NOT in `covered_synsets` (Japan-specific). A lemma can appear in both pipelines.
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

## Current validation status (wn validate, 2026-04-15)

| Code | Count | Notes |
|------|------:|-------|
| E401 | 0 | Fixed: stub synsets for all missing PWN relation targets |
| W203 | 282 | Cross-pipeline (wn+var + passthrough) overlapping entries |
| W202 | 308 | Side-effect of same issue |
| W301 | 8,451 | 5,941 PWN orphans (expected) + 2,510 stubs (empty by design) |
| W307 | 464 | Cross-synset shared definition texts — data issue |
| W404 | 2,509 | Stubs have no reverse relations (by design) |
| W501 | crash | Upstream wn validator bug — filed for reporting |

## Known gaps and planned work

- **Release**: tag and package after further testing
- **Yojijukugo**: ~364 four-character idiom synsets need Japanese entries in NTU-MC DB
- **Missing vocabulary**: よいしょ, いただきます, ごちそうさま, よろしく, もったいない,
  木漏れ日, 引きこもり, 過労死, 婚活, 一石二鳥, 一期一会, わびさび, …
- **Remaining W203/W202**: merge `main_entries + pass_entries` by (lemma, pos) in `main()`
- **W307**: per-case review of 464 cross-synset duplicate definitions
- **Sense-level antonyms**: temperature antonyms currently synset-level; best practice is sense-level
- **Adjective types**: merge data on -na/-no adjective
- **Merge TUFS data**

