"""Pluggable word tokenizers, one per language."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Token:
    """One token from a tokenized string."""

    surface: str   # as it appears in the source text
    base: str      # dictionary base / lemma form
    start: int     # Unicode char offset in source string (inclusive)
    end: int       # Unicode char offset in source string (exclusive)


class Tokenizer:
    """Abstract base; subclasses implement tokenize()."""

    def tokenize(self, text: str) -> list[Token]:
        """Return tokens with character positions for *text*."""
        raise NotImplementedError


class JapaneseTokenizer(Tokenizer):
    """MeCab tokenizer via fugashi + unidic-lite.

    Uses orthBase (orthographic base form) from UniDic features as the
    canonical base form when available, falling back to the surface form.
    """

    def __init__(self) -> None:
        import fugashi
        import unidic_lite
        self._tagger = fugashi.Tagger(f"-d {unidic_lite.DICDIR}")

    def tokenize(self, text: str) -> list[Token]:
        """Tokenize Japanese text; positions are Unicode character offsets."""
        tokens: list[Token] = []
        pos = 0
        for word in self._tagger(text):
            surface = word.surface
            try:
                # UniDic: orthBase is the standard written base form
                base = word.feature.orthBase or surface
            except AttributeError:
                base = surface
            start = text.find(surface, pos)
            if start == -1:
                start = pos
            end = start + len(surface)
            tokens.append(Token(surface=surface, base=base, start=start, end=end))
            pos = end
        return tokens


class ChineseTokenizer(Tokenizer):
    """jieba-based tokenizer for Mandarin Chinese."""

    def __init__(self) -> None:
        import jieba
        self._jieba = jieba

    def tokenize(self, text: str) -> list[Token]:
        tokens: list[Token] = []
        pos = 0
        for word in self._jieba.cut(text):
            start = text.find(word, pos)
            if start == -1:
                start = pos
            end = start + len(word)
            tokens.append(Token(surface=word, base=word, start=start, end=end))
            pos = end
        return tokens


class SpaceTokenizer(Tokenizer):
    """Whitespace tokenizer for space-delimited languages (Indonesian, etc.)."""

    def tokenize(self, text: str) -> list[Token]:
        tokens: list[Token] = []
        pos = 0
        for word in text.split():
            start = text.find(word, pos)
            end = start + len(word)
            tokens.append(Token(surface=word, base=word, start=start, end=end))
            pos = end
        return tokens


def get_tokenizer(lang: str) -> Tokenizer:
    """Return the appropriate tokenizer for an ISO 639-3 language code.

    Args:
        lang: ISO 639-3 code, e.g. 'jpn', 'cmn', 'ind'.

    Returns:
        Tokenizer instance for that language.
    """
    if lang == "jpn":
        return JapaneseTokenizer()
    if lang == "cmn":
        return ChineseTokenizer()
    return SpaceTokenizer()
