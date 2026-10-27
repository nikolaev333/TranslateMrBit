"""Unicode-aware text normalization and custom repetition detection."""

from __future__ import annotations

import re
import unicodedata
from typing import Any


_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_WS_RE = re.compile(r"\s+", re.UNICODE)
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def normalize_nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


def normalize_for_match(text: str) -> str:
    """Lowercase NFC text with punctuation stripped for matching only."""
    t = normalize_nfc(text).casefold()
    t = _PUNCT_RE.sub(" ", t)
    return _WS_RE.sub(" ", t).strip()


def tokenize_words(text: str) -> list[str]:
    return _TOKEN_RE.findall(normalize_for_match(text))


def detect_immediate_word_repetitions(
    text: str,
    *,
    min_repeats: int = 2,
) -> list[dict[str, Any]]:
    """Detect consecutive identical words (e.g. 'is is', 'hello hello hello')."""
    tokens = tokenize_words(text)
    findings: list[dict[str, Any]] = []
    i = 0
    while i < len(tokens):
        j = i + 1
        while j < len(tokens) and tokens[j] == tokens[i]:
            j += 1
        count = j - i
        if count >= min_repeats:
            findings.append(
                {
                    "type": "word",
                    "phrase": tokens[i],
                    "count": count,
                    "token_index": i,
                }
            )
        i = j
    return findings


def detect_phrase_repetitions(
    text: str,
    *,
    min_phrase_words: int = 2,
    max_phrase_words: int = 8,
    min_repeats: int = 2,
) -> list[dict[str, Any]]:
    """Detect repeated multi-word phrases via n-gram run-length analysis.

    Avoids naive substring matching by operating on token sequences only.
    """
    tokens = tokenize_words(text)
    if len(tokens) < min_phrase_words * min_repeats:
        return []

    findings: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int]] = set()

    for n in range(max_phrase_words, min_phrase_words - 1, -1):
        i = 0
        while i + n <= len(tokens):
            phrase = tuple(tokens[i : i + n])
            # Count consecutive phrase repetitions starting at i
            repeats = 1
            k = i + n
            while k + n <= len(tokens) and tuple(tokens[k : k + n]) == phrase:
                repeats += 1
                k += n
            if repeats >= min_repeats:
                key = (" ".join(phrase), i, repeats)
                if key not in seen:
                    # Prefer longer phrases: skip if this is fully covered by a longer find
                    covered = False
                    for f in findings:
                        if (
                            f["token_index"] <= i
                            and f["token_index"] + f["count"] * f["phrase_words"]
                            >= i + repeats * n
                            and f["phrase_words"] > n
                        ):
                            covered = True
                            break
                    if not covered:
                        seen.add(key)
                        findings.append(
                            {
                                "type": "phrase",
                                "phrase": " ".join(phrase),
                                "count": repeats,
                                "phrase_words": n,
                                "token_index": i,
                            }
                        )
                i = k
            else:
                i += 1
    return findings


def detect_repetition_loops(
    text: str,
    *,
    min_repeats: int = 3,
) -> list[dict[str, Any]]:
    """Flag excessive consecutive loops (word or short phrase)."""
    out: list[dict[str, Any]] = []
    for item in detect_immediate_word_repetitions(text, min_repeats=min_repeats):
        out.append({**item, "type": "loop"})
    for item in detect_phrase_repetitions(
        text, min_phrase_words=2, max_phrase_words=6, min_repeats=min_repeats
    ):
        out.append({**item, "type": "loop"})
    return out


def intentional_repetition_heuristic(phrase: str, count: int) -> bool:
    """Conservative: short emphasis words repeated twice are often intentional."""
    short = {"да", "не", "yes", "no", "ok", "okay", "ха", "хаха", "hm", "мм"}
    return count == 2 and phrase.casefold() in short
