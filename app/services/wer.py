"""Word Error Rate helpers for optional quality evaluation."""

from __future__ import annotations

import re
import unicodedata
from typing import Any


_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFC", text or "")
    return [t.lower() for t in _TOKEN_RE.findall(normalized)]


def word_error_rate(reference: str, hypothesis: str) -> dict[str, Any]:
    """Compute classic Levenshtein WER between reference and hypothesis.

    Returns measured values only — never invents accuracy scores.
    """
    ref = tokenize(reference)
    hyp = tokenize(hypothesis)
    if not ref:
        return {
            "wer": 0.0 if not hyp else 1.0,
            "reference_words": 0,
            "hypothesis_words": len(hyp),
            "substitutions": 0,
            "deletions": 0,
            "insertions": len(hyp),
            "edits": len(hyp),
        }

    # DP: rows = ref, cols = hyp
    n, m = len(ref), len(hyp)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    back = [[None] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0] = i
        back[i][0] = "del"
    for j in range(1, m + 1):
        dp[0][j] = j
        back[0][j] = "ins"

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if ref[i - 1] == hyp[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
                back[i][j] = "eq"
            else:
                sub = dp[i - 1][j - 1] + 1
                delete = dp[i - 1][j] + 1
                insert = dp[i][j - 1] + 1
                best = min(sub, delete, insert)
                dp[i][j] = best
                if best == sub:
                    back[i][j] = "sub"
                elif best == delete:
                    back[i][j] = "del"
                else:
                    back[i][j] = "ins"

    i, j = n, m
    substitutions = deletions = insertions = 0
    while i > 0 or j > 0:
        op = back[i][j]
        if op == "eq":
            i -= 1
            j -= 1
        elif op == "sub":
            substitutions += 1
            i -= 1
            j -= 1
        elif op == "del":
            deletions += 1
            i -= 1
        elif op == "ins":
            insertions += 1
            j -= 1
        else:
            break

    edits = substitutions + deletions + insertions
    return {
        "wer": edits / n,
        "reference_words": n,
        "hypothesis_words": m,
        "substitutions": substitutions,
        "deletions": deletions,
        "insertions": insertions,
        "edits": edits,
    }


def _char_tokens(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFC", text or "").casefold()
    # Ignore whitespace-only differences for CER of prose transcripts.
    return [ch for ch in normalized if not ch.isspace()]


def character_error_rate(reference: str, hypothesis: str) -> dict[str, Any]:
    """Character-level Levenshtein CER. Measured values only."""
    ref = _char_tokens(reference)
    hyp = _char_tokens(hypothesis)
    if not ref:
        return {
            "cer": 0.0 if not hyp else 1.0,
            "reference_chars": 0,
            "hypothesis_chars": len(hyp),
            "edits": len(hyp),
        }
    n, m = len(ref), len(hyp)
    prev = list(range(m + 1))
    for i in range(1, n + 1):
        cur = [i] + [0] * m
        for j in range(1, m + 1):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    edits = prev[m]
    return {
        "cer": edits / n,
        "reference_chars": n,
        "hypothesis_chars": m,
        "edits": edits,
    }
