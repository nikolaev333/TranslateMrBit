"""Lightweight language/script consistency heuristics (no external APIs)."""

from __future__ import annotations

import unicodedata
from typing import Any


def _script_bucket(ch: str) -> str | None:
    if not ch.isalpha():
        return None
    name = unicodedata.name(ch, "")
    if "CYRILLIC" in name:
        return "cyrillic"
    if "LATIN" in name:
        return "latin"
    if "GREEK" in name:
        return "greek"
    if "ARABIC" in name:
        return "arabic"
    if "CJK" in name or "HIRAGANA" in name or "KATAKANA" in name or "HANGUL" in name:
        return "cjk"
    return "other"


def script_ratios(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for ch in text or "":
        bucket = _script_bucket(ch)
        if not bucket:
            continue
        counts[bucket] = counts.get(bucket, 0) + 1
    return counts


def analyze_language_consistency(
    text: str,
    *,
    expected_language: str | None = None,
    mix_ratio_threshold: float = 0.35,
) -> list[dict[str, Any]]:
    """Flag unexpected script mixes. Occasional foreign words are tolerated.

    Does not invent language probabilities.
    """
    counts = script_ratios(text)
    total = sum(counts.values())
    if total < 20:
        return []

    issues: list[dict[str, Any]] = []
    expected = (expected_language or "").strip().lower()
    primary = "cyrillic" if expected.startswith("bulgar") or expected in {"bg", "ru", "uk"} else None
    if expected.startswith("en") or expected in {"english", "de", "fr", "es", "it"}:
        primary = "latin"

    if primary and primary in counts:
        other = total - counts.get(primary, 0)
        ratio = other / total
        if ratio >= mix_ratio_threshold and other >= 8:
            issues.append(
                {
                    "type": "LANG_MIX",
                    "detail": (
                        f"Unexpected script mix: {ratio:.0%} non-{primary} letters "
                        f"(threshold {mix_ratio_threshold:.0%}). "
                        "Occasional foreign words may be intentional."
                    ),
                    "ratios": counts,
                    "ratio": ratio,
                }
            )
    elif len(counts) >= 2:
        ranked = sorted(counts.items(), key=lambda x: -x[1])
        secondary_ratio = ranked[1][1] / total
        if secondary_ratio >= mix_ratio_threshold:
            issues.append(
                {
                    "type": "LANG_MIX",
                    "detail": (
                        f"Multiple scripts detected ({ranked[0][0]} / {ranked[1][0]}). "
                        "Review if unexpected for this recording."
                    ),
                    "counts": counts,
                    "ratio": secondary_ratio,
                }
            )
    return issues
