# wnja

Japanese WordNet v2.0

Web page: <https://bond-lab.github.io/wnja/>

## Version 2.0 (2026-10)

| | |
|---|---|
| Synsets | 67,387 (58,935 with Japanese words) |
| Words (lexical entries) | 89,549 |
| Senses | 146,207 |
| Japanese definitions | 66,944 |

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
