"""Typed schemas for MrBit Quality Analyzer (application-level)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class ReviewPriority(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


@dataclass
class RuleConfig:
    rule_id: str
    enabled: bool = True
    threshold: float = 0.0
    weight: float = 1.0
    description: str = ""


@dataclass
class QualityWarning:
    rule_id: str
    description: str
    priority: ReviewPriority
    explanation: str
    evidence: dict[str, Any] = field(default_factory=dict)
    segment_ids: list[str] = field(default_factory=list)
    start_sec: float | None = None
    end_sec: float | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["priority"] = self.priority.value
        return data


@dataclass
class AnalysisResult:
    warnings: list[QualityWarning]
    overall_priority: ReviewPriority
    score_breakdown: dict[str, Any]
    rules_executed: list[str]
    segments_analyzed: int
    analysis_version: str
    config_snapshot: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "warnings": [w.to_dict() for w in self.warnings],
            "overall_priority": self.overall_priority.value,
            "score_breakdown": self.score_breakdown,
            "rules_executed": self.rules_executed,
            "segments_analyzed": self.segments_analyzed,
            "analysis_version": self.analysis_version,
            "config_snapshot": self.config_snapshot,
        }


ANALYSIS_VERSION = "1.0.0"

DEFAULT_RULES: dict[str, RuleConfig] = {
    "REP_WORD": RuleConfig(
        "REP_WORD",
        threshold=2.0,
        weight=1.5,
        description="Immediate repeated words",
    ),
    "REP_PHRASE": RuleConfig(
        "REP_PHRASE",
        threshold=2.0,
        weight=2.0,
        description="Repeated multi-word phrases",
    ),
    "REP_LOOP": RuleConfig(
        "REP_LOOP",
        threshold=3.0,
        weight=2.5,
        description="Excessive consecutive repetition loops",
    ),
    "TS_OVERLAP": RuleConfig(
        "TS_OVERLAP",
        threshold=0.0,
        weight=2.0,
        description="Overlapping segment timestamps",
    ),
    "TS_GAP": RuleConfig(
        "TS_GAP",
        threshold=5.0,
        weight=1.0,
        description="Large unexplained gaps between segments",
    ),
    "TS_DURATION": RuleConfig(
        "TS_DURATION",
        threshold=45.0,
        weight=1.2,
        description="Unusually long transcript segments",
    ),
    "TS_ORDER": RuleConfig(
        "TS_ORDER",
        threshold=0.0,
        weight=2.0,
        description="Non-monotonic timestamps",
    ),
    "META_COMPRESS": RuleConfig(
        "META_COMPRESS",
        threshold=2.4,
        weight=1.8,
        description="High Whisper compression_ratio (heuristic)",
    ),
    "META_NOSPEECH": RuleConfig(
        "META_NOSPEECH",
        threshold=0.6,
        weight=1.5,
        description="High no_speech_probability with text present",
    ),
    "META_LOGPROB": RuleConfig(
        "META_LOGPROB",
        threshold=-1.2,
        weight=1.2,
        description="Low avg_logprob (heuristic review signal only)",
    ),
    "AUD_SILENCE": RuleConfig(
        "AUD_SILENCE",
        threshold=2.0,
        weight=0.8,
        description="Long silence intervals (informational)",
    ),
    "AUD_LOWVOL": RuleConfig(
        "AUD_LOWVOL",
        threshold=0.02,
        weight=1.0,
        description="Low-volume regions",
    ),
    "AUD_CLIP": RuleConfig(
        "AUD_CLIP",
        threshold=0.98,
        weight=1.5,
        description="Potential clipping peaks",
    ),
    "AUD_DURATION_MISMATCH": RuleConfig(
        "AUD_DURATION_MISMATCH",
        threshold=3.0,
        weight=1.5,
        description="Audio vs transcript duration mismatch",
    ),
    "LANG_MIX": RuleConfig(
        "LANG_MIX",
        threshold=0.35,
        weight=0.7,
        description="Possible unexpected script/language mix",
    ),
    "TERM_GLOSSARY": RuleConfig(
        "TERM_GLOSSARY",
        threshold=1.0,
        weight=0.6,
        description="Optional glossary terminology suggestions",
    ),
}
