# wnja

Japanese WordNet v2.1

Web page: <https://bond-lab.github.io/wnja/>

## Version 2.1 (2026-10)

| | |
|---|---|
| Synsets | 67,509 (57,885 with Japanese words) |
| Words (lexical entries) | 90,706 |
| Senses | 148,703 |
| Japanese definitions | 67,064 |
| Japanese examples | 35,359 |

wnja 2.1 is a WN-LMF 1.4 package built from the Japanese WordNet 1.1
data (`data/sources/wn+var_tk17.tab`, `data/sources/vars_tk17.tab`) and the Japanese
wordnet in the [NTU-MC](https://github.com/bond-lab/NTUMC) database
(`data/sources/wn-ntumc-jpn.xml`), which adds Japan-specific synsets,
definitions and senses from sense-tagging.  It extends `omw-en:2.0`.

```python
import wn
wn.download("omw-en:2.0")
wn.download("https://github.com/bond-lab/wnja/releases/download/v2.1/wnja-2.1.tar.xz")
wn.Wordnet("wnja:2.1").words("犬")
```

### Changes in 2.1

- 35,359 Japanese examples (from the NTU-MC database; 2.0 had none)
- 2,277 wrong senses removed and 187 given confidence 0.5 after an LLM
  audit checked twice, blind, by Claude Opus ([issue
  #8](https://github.com/bond-lab/wnja/issues/8)); 16 more removed from
  error reports (`corrections/senses.tsv`)
- 1,314 senses added from a review of the Japanese SemCor
- Homographs with different readings (物 もの/ぶつ) kept as separate entries;
  sense counts per reading from the Japanese SemCor 1.1
- Lemma forms: full-width digits and letters made half width (ＶＩＰ → VIP),
  katakana okurigana typos fixed (跡取リ → 跡取り)
- Adjectives tagged with Kuroda's classes (na, no, na-no, no-na)
- Contributors listed in `CONTRIBUTORS.md`; license updated to 2026

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
  according to JMdict (`data/derived/reading_rank.tsv`), and is not counted twice.

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

For wnja 2.0, please cite:

Francis Bond and Takayuki Kuribayashi (2023). The Japanese Wordnet 2.0.
In *12th International Global Wordnet Conference (GWC2023)*.
<https://aclanthology.org/2023.gwc-1.22/>

and for the original wordnet:

Hitoshi Isahara, Francis Bond, Kiyotaka Uchimoto, Masao Utiyama and
Kyoko Kanzaki (2008). Development of the Japanese WordNet. In *LREC
2008*, Marrakech.

[citation.bib](citation.bib) has these and the papers on parts of the
wordnet: earlier versions (2008–2010), adjectives (Kanzaki et al. 2010),
orthographic variants and adjective classes (Kuroda et al. 2011), the
Japanese SemCor (Bond et al. 2012), numeral classifiers (Mok et al.
2012), sense-tagged parallel corpora (Bond et al. 2013), pronouns (Seah
and Bond 2014), exclamatives (Morgado da Costa and Bond 2016),
derivational relations (Bond and Wei 2019) and the TUFS basic
vocabulary (Bond et al. 2020).

## Manual corrections

Definition fixes and other post-build corrections are tracked in
[`corrections/`](corrections/README.md). See that directory for the format
and instructions for adding new corrections.
