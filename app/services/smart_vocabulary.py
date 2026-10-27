"""MrBit Smart Vocabulary — application-level terminology matching.

Original TranslateMrBit code. Does not modify the Whisper neural network.
Uses post-transcription analysis and optional Whisper ``initial_prompt`` context only.
"""

from __future__ import annotations

import json
import re
import unicodedata
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Iterable


def normalize_text(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


def _escape_phrase(phrase: str) -> str:
    return re.escape(normalize_text(phrase).strip())


def _boundary_pattern(phrase: str) -> re.Pattern[str]:
    """Word-boundary-aware match for Latin and Cyrillic tokens."""
    escaped = _escape_phrase(phrase)
    # Soft boundaries: start/end or non-letter/digit around the phrase.
    return re.compile(
        rf"(?<![\w]){escaped}(?![\w])",
        re.IGNORECASE | re.UNICODE,
    )


@dataclass(frozen=True)
class VocabularyTerm:
    id: str
    correct: str
    alternatives: tuple[str, ...]
    language: str
    category: str
    description: str
    enabled: bool


@dataclass
class MatchHit:
    entry_id: str
    correct: str
    matched_text: str
    start: int
    end: int
    category: str
    language: str


@dataclass
class AnalysisResult:
    original_text: str
    suggested_text: str
    matches: list[MatchHit]


def parse_alternatives(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                data = json.loads(text)
                if isinstance(data, list):
                    return [str(x).strip() for x in data if str(x).strip()]
            except json.JSONDecodeError:
                pass
        return [p.strip() for p in re.split(r"[,;\n]+", text) if p.strip()]
    return []


def dump_alternatives(items: Iterable[str]) -> str:
    cleaned = [str(x).strip() for x in items if str(x).strip()]
    return json.dumps(cleaned, ensure_ascii=False)


def term_from_row(row: dict[str, Any]) -> VocabularyTerm:
    return VocabularyTerm(
        id=str(row["id"]),
        correct=str(row["correct_term"]).strip(),
        alternatives=tuple(parse_alternatives(row.get("alternatives"))),
        language=str(row.get("language") or "").strip() or "any",
        category=str(row.get("category") or "").strip() or "General",
        description=str(row.get("description") or "").strip(),
        enabled=bool(row.get("enabled", 1)),
    )


def _candidate_phrases(term: VocabularyTerm) -> list[str]:
    phrases: list[str] = []
    for p in (term.correct, *term.alternatives):
        p = normalize_text(p).strip()
        if not p:
            continue
        # Skip ultra-short alternatives that cause false positives (e.g. "R").
        if len(p) < 2:
            continue
        if p not in phrases:
            phrases.append(p)
    # Longest first so multi-word phrases win before fragments.
    phrases.sort(key=lambda s: (-len(s), s.lower()))
    return phrases


def _language_compatible(term_language: str, job_language: str | None) -> bool:
    """Soft language filter for initial_prompt; matching uses all enabled terms."""
    lang = (job_language or "").strip().lower()
    if not lang:
        return True
    term_lang = (term_language or "").strip().lower()
    if term_lang in {"", "any", "*"}:
        return True
    if term_lang == lang:
        return True
    if term_lang.startswith(lang[:2]) or lang.startswith(term_lang[:2]):
        return True
    return False


def find_matches(
    text: str,
    terms: Iterable[VocabularyTerm],
    *,
    language: str | None = None,
) -> list[MatchHit]:
    """Find non-overlapping terminology matches (longest-first).

    Enabled terms from any language are considered so Latin brand names
    (RTP, Jackpot, …) still match inside mixed/English transcripts.
    ``language`` is accepted for API compatibility; prompt building filters
    more strictly via ``build_initial_prompt``.
    """
    del language  # matching is language-agnostic; see docstring
    original = normalize_text(text)
    if not original:
        return []

    enabled = [t for t in terms if t.enabled and t.correct.strip()]

    # Rank all candidate hits, then greedily keep non-overlapping longest spans.
    raw_hits: list[MatchHit] = []
    for term in enabled:
        for phrase in _candidate_phrases(term):
            pattern = _boundary_pattern(phrase)
            for m in pattern.finditer(original):
                raw_hits.append(
                    MatchHit(
                        entry_id=term.id,
                        correct=term.correct,
                        matched_text=original[m.start() : m.end()],
                        start=m.start(),
                        end=m.end(),
                        category=term.category,
                        language=term.language,
                    )
                )

    raw_hits.sort(key=lambda h: (-(h.end - h.start), h.start, h.correct.lower()))
    selected: list[MatchHit] = []
    occupied: list[tuple[int, int]] = []

    def overlaps(a: int, b: int) -> bool:
        for s, e in occupied:
            if a < e and b > s:
                return True
        return False

    for hit in raw_hits:
        if overlaps(hit.start, hit.end):
            continue
        # Occupy the span even for identity matches so shorter alternatives
        # (e.g. "bet" inside "bet limits") cannot steal the phrase.
        occupied.append((hit.start, hit.end))
        if normalize_text(hit.matched_text).casefold() == normalize_text(hit.correct).casefold():
            continue
        selected.append(hit)

    selected.sort(key=lambda h: h.start)
    return selected


def apply_suggestions(text: str, matches: list[MatchHit]) -> str:
    """Build suggested text by replacing matched spans with correct terms."""
    original = normalize_text(text)
    if not matches:
        return original
    parts: list[str] = []
    cursor = 0
    for hit in sorted(matches, key=lambda h: h.start):
        if hit.start < cursor:
            continue
        parts.append(original[cursor : hit.start])
        parts.append(hit.correct)
        cursor = hit.end
    parts.append(original[cursor:])
    return "".join(parts)


def analyze_transcription(
    text: str,
    terms: Iterable[VocabularyTerm],
    *,
    language: str | None = None,
) -> AnalysisResult:
    original = normalize_text(text)
    matches = find_matches(original, terms, language=language)
    suggested = apply_suggestions(original, matches)
    return AnalysisResult(original_text=original, suggested_text=suggested, matches=matches)


def analysis_to_dict(result: AnalysisResult) -> dict[str, Any]:
    return {
        "original_text": result.original_text,
        "suggested_text": result.suggested_text,
        "matches": [asdict(m) for m in result.matches],
    }


def build_reviewed_text(
    original_text: str,
    matches: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
) -> str:
    """Apply per-match accept/reject/edit decisions. Original remains unchanged in DB."""
    text = normalize_text(original_text)
    # Map decision by match index
    by_index: dict[int, dict[str, Any]] = {}
    for d in decisions:
        try:
            idx = int(d.get("match_index"))
        except (TypeError, ValueError):
            continue
        by_index[idx] = d

    # Rebuild from original using only accepted/edited matches
    applied: list[MatchHit] = []
    for idx, m in enumerate(matches):
        decision = by_index.get(idx) or {"action": "reject"}
        action = str(decision.get("action") or "reject").lower()
        if action == "reject":
            continue
        replacement = m.get("correct") or ""
        if action == "edit":
            replacement = str(decision.get("replacement") or replacement).strip()
            if not replacement:
                continue
        applied.append(
            MatchHit(
                entry_id=str(m.get("entry_id") or ""),
                correct=replacement,
                matched_text=str(m.get("matched_text") or ""),
                start=int(m["start"]),
                end=int(m["end"]),
                category=str(m.get("category") or ""),
                language=str(m.get("language") or ""),
            )
        )
    return apply_suggestions(text, applied)


def build_initial_prompt(
    terms: Iterable[VocabularyTerm],
    *,
    language: str | None = None,
    max_chars: int = 800,
) -> str | None:
    """Short terminology context for Whisper initial_prompt (best-effort only)."""
    corrects: list[str] = []
    for t in terms:
        if not t.enabled:
            continue
        if not _language_compatible(t.language, language):
            continue
        term = normalize_text(t.correct).strip()
        if term and term not in corrects:
            corrects.append(term)

    if not corrects:
        return None

    header = "Domain terminology:"
    body_parts: list[str] = []
    used = len(header) + 1
    for term in corrects:
        piece = f" {term},"
        if used + len(piece) > max_chars:
            break
        body_parts.append(term)
        used += len(piece)
    if not body_parts:
        return None
    return f"{header} " + ", ".join(body_parts) + "."


def new_entry_id() -> str:
    return str(uuid.uuid4())
