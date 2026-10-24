# wnja

Japanese WordNet v2.0

Web page: <https://bond-lab.github.io/wnja/>

## Version 2.0 (2026-10)

| | |
|---|---|
| Synsets | 67,509 (57,885 with Japanese words) |
| Words (lexical entries) | 90,706 |
| Senses | 148,703 |
| Japanese definitions | 67,064 |
| Japanese examples | 35,359 |

wnja 2.0 is a WN-LMF 1.4 package built from the Japanese WordNet 1.1
data (`data/wn+var_tk17.tab`, `data/vars_tk17.tab`) and the Japanese
wordnet in the [NTU-MC](https://github.com/bond-lab/NTUMC) database
(`data/wn-ntumc-jpn.xml`), which adds Japan-specific synsets,
definitions and senses from sense-tagging.  It extends `omw-en:2.0`.

```python
import wn
wn.download("omw-en:2.0")
wn.download("https://github.com/bond-lab/wnja/releases/download/v2.0/wnja-2.0.tar.xz")
wn.Wordnet("wnja:2.0").words("犬")
```

Build: see [CLAUDE.md](CLAUDE.md); package with `package_wnja.py`.

### Differences from the GWC 2023 paper

- **No Latin forms.** The paper ([Bond and Kuribayashi
  2023](https://aclanthology.org/2023.gwc-1.22.pdf), Figure 1 and Table 3)
  describes romanised forms (`script="latn"` and `latn-hepburn`, e.g.
  *zisyo*, *jisho*).  They are left out of the release because they make
  the database much bigger, and they can be generated from the katakana
  reading (e.g. with [romkan](https://pypi.org/project/romkan/)).
- **Homographs are separate entries.** A word is a spelling plus a
  reading, so 反る read ソル "warp" and カエル (= 返る) are two entries.
  `wn validate` reports these as W203 (redundant entries).
- **Frequencies are per corpus.** Each sense has one `<Count>` per
  corpus, NTU-MC and JSemCor, named in `dc:source`.  The corpora tag
  spellings, not readings, so when several readings of a spelling share
  a synset (物 ブツ/モノ) the count goes to the most frequent reading
  according to JMdict (`data/reading_rank.tsv`), and is not counted twice.

### Adjective classes

Adjectives are tagged with how they modify a noun, from Kow Kuroda's
hand annotation ([Kuroda et al.
2011](https://www.anlp.jp/proceedings/annual_meeting/2011/pdf_dir/A4-1.pdf)),
as `<Tag category="adjective-class">` on the Lemma:

| Tag | Class | Example |
|-----|-------|---------|
| `na` | ナ形容詞 (形容動詞): ends in な | 静かな |
| `no` | ノ形容詞: ends in の | 本当の |
| `na-no` | な or の, な more natural | |
| `no-na` | の or な, の more natural | |
| `other` | ends in たる or なる | |

Untagged adjectives are mostly イ形容詞 ending in (し)い, the default
class, which was not annotated.

## License

See [LICENSE](LICENSE) (a WordNet-style permissive license).

## Citation

Hitoshi Isahara, Francis Bond, Kiyotaka Uchimoto, Masao Utiyama and
Kyoko Kanzaki (2008). Development of the Japanese WordNet. In *LREC
2008*, Marrakech.  See [citation.bib](citation.bib).

## Manual corrections

Definition fixes and other post-build corrections are tracked in
[`corrections/`](corrections/README.md). See that directory for the format
and instructions for adding new corrections.
