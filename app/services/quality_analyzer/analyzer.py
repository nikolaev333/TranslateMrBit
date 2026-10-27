"""Orchestrates MrBit Quality Analyzer pipeline (no torch)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services.quality_analyzer.audio_metrics import analyze_audio_file
from app.services.quality_analyzer.risk_scoring import score_warnings
from app.services.quality_analyzer.rules import ALL_RULES
from app.services.quality_analyzer.schemas import (
    ANALYSIS_VERSION,
    DEFAULT_RULES,
    AnalysisResult,
    RuleConfig,
)


@dataclass
class AnalyzerContext:
    segments: list[dict[str, Any]]
    full_text: str
    expected_language: str | None = None
    whisper_segments_meta: list[dict[str, Any]] = field(default_factory=list)
    audio_issues: list[dict[str, Any]] = field(default_factory=list)
    glossary_matches: list[dict[str, Any]] = field(default_factory=list)
    rules: dict[str, RuleConfig] = field(default_factory=lambda: dict(DEFAULT_RULES))


class QualityAnalyzer:
    def __init__(self, rules: dict[str, RuleConfig] | None = None) -> None:
        self.rules = dict(DEFAULT_RULES)
        if rules:
            for k, v in rules.items():
                self.rules[k] = v

    def analyze(self, ctx: AnalyzerContext) -> AnalysisResult:
        ctx.rules = self.rules
        warnings = []
        executed: list[str] = []
        for rule_fn in ALL_RULES:
            executed.append(rule_fn.__name__)
            warnings.extend(rule_fn(ctx))

        overall, breakdown = score_warnings(warnings, self.rules)
        return AnalysisResult(
            warnings=warnings,
            overall_priority=overall,
            score_breakdown=breakdown,
            rules_executed=executed,
            segments_analyzed=len(ctx.segments),
            analysis_version=ANALYSIS_VERSION,
            config_snapshot={
                rid: {
                    "enabled": r.enabled,
                    "threshold": r.threshold,
                    "weight": r.weight,
                    "description": r.description,
                }
                for rid, r in self.rules.items()
            },
        )


def load_whisper_meta_from_json(json_path: str | None) -> list[dict[str, Any]]:
    if not json_path:
        return []
    path = Path(json_path)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    segs = data.get("segments") if isinstance(data, dict) else None
    return segs if isinstance(segs, list) else []


def run_quality_analysis(
    *,
    segments: list[dict[str, Any]],
    full_text: str,
    expected_language: str | None = None,
    output_json_path: str | None = None,
    upload_path: str | None = None,
    include_audio: bool = True,
    glossary_matches: list[dict[str, Any]] | None = None,
    rules: dict[str, RuleConfig] | None = None,
) -> AnalysisResult:
    """Full pipeline entrypoint used by background workers."""
    meta = load_whisper_meta_from_json(output_json_path)
    audio_issues: list[dict[str, Any]] = []
    if include_audio and upload_path and Path(upload_path).is_file():
        transcript_end = None
        ends = []
        for s in segments:
            e = s.get("end_sec", s.get("end"))
            if e is not None:
                try:
                    ends.append(float(e))
                except (TypeError, ValueError):
                    pass
        if ends:
            transcript_end = max(ends)
        try:
            audio_issues = analyze_audio_file(
                upload_path, transcript_end_sec=transcript_end
            )
        except Exception:
            # Audio diagnostics are optional — never fail the whole analysis.
            audio_issues = []

    ctx = AnalyzerContext(
        segments=segments,
        full_text=full_text or "",
        expected_language=expected_language,
        whisper_segments_meta=meta,
        audio_issues=audio_issues,
        glossary_matches=glossary_matches or [],
        rules=dict(DEFAULT_RULES),
    )
    return QualityAnalyzer(rules=rules).analyze(ctx)
