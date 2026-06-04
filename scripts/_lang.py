"""Shared language detection for seisbib (used by both proc_jsonl_to_bib
and generate). Heuristic English vs. non-English title classifier.

* Returns False for titles in non-Latin scripts (Cyrillic, CJK, Arabic,
  Greek, Devanagari, Hebrew, ...).
* For Latin-script titles, requires ≥1 English function word in the
  title — catches Portuguese / Spanish / French / German / Italian /
  Romanian / Polish etc. that pass the script check but aren't English.
* Very short titles (< 3 words) default to True since stopword detection
  is unreliable.
"""

from __future__ import annotations

import re

_NON_LATIN_RANGES = (
    (0x0370, 0x03FF),  # Greek
    (0x0400, 0x052F),  # Cyrillic + supplement
    (0x0530, 0x058F),  # Armenian
    (0x0590, 0x05FF),  # Hebrew
    (0x0600, 0x06FF),  # Arabic
    (0x0700, 0x074F),  # Syriac
    (0x0900, 0x097F),  # Devanagari
    (0x0980, 0x09FF),  # Bengali
    (0x0E00, 0x0E7F),  # Thai
    (0x1100, 0x11FF),  # Hangul Jamo
    (0x2E80, 0x2EFF),  # CJK radicals supplement
    (0x3040, 0x309F),  # Hiragana
    (0x30A0, 0x30FF),  # Katakana
    (0x3400, 0x4DBF),  # CJK Extension A
    (0x4E00, 0x9FFF),  # CJK Unified
    (0xAC00, 0xD7AF),  # Hangul Syllables
    (0xF900, 0xFAFF),  # CJK Compatibility Ideographs
)

_EN_STOPWORDS = frozenset({
    "the", "a", "an", "of", "and", "or", "for", "in", "on", "at",
    "to", "from", "by", "with", "as", "is", "are", "be", "this",
    "that", "these", "via", "using", "use", "into", "based", "case",
    "study", "analysis", "method", "methods", "model", "models",
    "data", "wave", "waves", "field", "fields", "between", "over",
    "under", "new", "novel", "review", "first", "high", "low",
    "imaging", "inversion", "tomography",
})

_WORD_RE = re.compile(r"[A-Za-z]+")


def has_non_latin_script(text: str) -> bool:
    for ch in text:
        cp = ord(ch)
        for lo, hi in _NON_LATIN_RANGES:
            if lo <= cp <= hi:
                return True
    return False


def looks_english(title: str) -> bool:
    if not title or len(title.strip()) < 4:
        return True
    if has_non_latin_script(title):
        return False
    words = [w.lower() for w in _WORD_RE.findall(title)]
    if len(words) < 3:
        return True
    return any(w in _EN_STOPWORDS for w in words)
