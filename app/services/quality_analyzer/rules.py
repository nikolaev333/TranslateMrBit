"""Independent quality analysis rules."""

from __future__ import annotations

from typing import Any, Protocol

from app.services.quality_analyzer.language_analysis import analyze_language_consistency
from app.services.quality_analyzer.risk_scoring import warning_priority_from_signal
from app.services.quality_analyzer.schemas import (
    DEFAULT_RULES,
    QualityWarning,
    ReviewPriority,
    RuleConfig,
)
from app.services.quality_analyzer.text_metrics import (
    detect_immediate_word_repetitions,
    detect_phrase_repetitions,
    detect_repetition_loops,
    intentional_repetition_heuristic,
)
from app.services.quality_analyzer.timestamp_analysis import analyze_timestamps


class RuleContext(Protocol):
    segments: list[dict[str, Any]]
    full_text: str
    expected_language: str | None
    whisper_segments_meta: list[dict[str, Any]]
    audio_issues: list[dict[str, Any]]
    glossary_matches: list[dict[str, Any]]
    rules: dict[str, RuleConfig]


def _cfg(rules: dict[str, RuleConfig], rule_id: str) -> RuleConfig:
    return rules.get(rule_id) or DEFAULT_RULES.get(rule_id) or RuleConfig(rule_id)


def rule_repetitions(ctx: Any) -> list[QualityWarning]:
    out: list[QualityWarning] = []
    rules: dict[str, RuleConfig] = ctx.rules

    for seg in ctx.segments:
        text = seg.get("edited_text") or seg.get("original_text") or seg.get("text") or ""
        sid = str(seg.get("id") or "")
        start = seg.get("start_sec", seg.get("start"))
        end = seg.get("end_sec", seg.get("end"))

        cfg_w = _cfg(rules, "REP_WORD")
        if cfg_w.enabled:
            for hit in detect_immediate_word_repetitions(
                text, min_repeats=max(2, int(cfg_w.threshold))
            ):
                if intentional_repetition_heuristic(hit["phrase"], hit["count"]):
                    continue
                strength = float(hit["count"] - 1)
                out.append(
                    QualityWarning(
                        rule_id="REP_WORD",
                        description=cfg_w.description,
                        priority=warning_priority_from_signal(strength=strength),
                        explanation=(
                            f"Immediate word repetition '{hit['phrase']}' ×{hit['count']}. "
                            "Heuristic indicator — may be intentional emphasis."
                        ),
                        evidence=hit,
                        segment_ids=[sid] if sid else [],
                        start_sec=float(start) if start is not None else None,
                        end_sec=float(end) if end is not None else None,
                    )
                )

        cfg_p = _cfg(rules, "REP_PHRASE")
        if cfg_p.enabled:
            for hit in detect_phrase_repetitions(
                text, min_repeats=max(2, int(cfg_p.threshold))
            ):
                strength = float(hit["count"])
                out.append(
                    QualityWarning(
                        rule_id="REP_PHRASE",
                        description=cfg_p.description,
                        priority=warning_priority_from_signal(
                            strength=strength, medium_at=2.0, high_at=3.0
                        ),
                        explanation=(
                            f"Repeated phrase '{hit['phrase']}' ×{hit['count']}. "
                            "Not proof of a transcription error."
                        ),
                        evidence=hit,
                        segment_ids=[sid] if sid else [],
                        start_sec=float(start) if start is not None else None,
                        end_sec=float(end) if end is not None else None,
                    )
                )

        cfg_l = _cfg(rules, "REP_LOOP")
        if cfg_l.enabled:
            for hit in detect_repetition_loops(
                text, min_repeats=max(3, int(cfg_l.threshold))
            ):
                out.append(
                    QualityWarning(
                        rule_id="REP_LOOP",
                        description=cfg_l.description,
                        priority=ReviewPriority.HIGH,
                        explanation=(
                            f"Possible repetition loop '{hit['phrase']}' ×{hit['count']}. "
                            "Often associated with decoder loops; verify against audio."
                        ),
                        evidence=hit,
                        segment_ids=[sid] if sid else [],
                        start_sec=float(start) if start is not None else None,
                        end_sec=float(end) if end is not None else None,
                    )
                )
    return out


def rule_timestamps(ctx: Any) -> list[QualityWarning]:
    out: list[QualityWarning] = []
    rules = ctx.rules
    gap_th = _cfg(rules, "TS_GAP").threshold
    long_th = _cfg(rules, "TS_DURATION").threshold
    for issue in analyze_timestamps(
        ctx.segments, gap_threshold_sec=gap_th, long_segment_sec=long_th
    ):
        rule_id = issue["type"]
        cfg = _cfg(rules, rule_id)
        if not cfg.enabled:
            continue
        pri = ReviewPriority.MEDIUM
        if rule_id in {"TS_OVERLAP", "TS_ORDER"}:
            pri = ReviewPriority.HIGH
        elif rule_id == "TS_GAP":
            pri = ReviewPriority.LOW
        out.append(
            QualityWarning(
                rule_id=rule_id,
                description=cfg.description,
                priority=pri,
                explanation=issue.get("detail") or cfg.description,
                evidence=issue,
                segment_ids=[str(issue["segment_id"])] if issue.get("segment_id") else [],
                start_sec=issue.get("start_sec"),
                end_sec=issue.get("end_sec"),
            )
        )
    return out


def rule_whisper_metadata(ctx: Any) -> list[QualityWarning]:
    """Use available Whisper segment metadata. Missing fields are skipped."""
    out: list[QualityWarning] = []
    rules = ctx.rules
    meta_list = ctx.whisper_segments_meta or []
    segments = ctx.segments

    for idx, meta in enumerate(meta_list):
        if not isinstance(meta, dict):
            continue
        sid = ""
        start = meta.get("start")
        end = meta.get("end")
        if idx < len(segments):
            sid = str(segments[idx].get("id") or "")
            start = start if start is not None else segments[idx].get("start_sec")
            end = end if end is not None else segments[idx].get("end_sec")

        text = str(meta.get("text") or "").strip()

        cr = meta.get("compression_ratio")
        cfg_c = _cfg(rules, "META_COMPRESS")
        if cfg_c.enabled and cr is not None:
            try:
                cr_f = float(cr)
            except (TypeError, ValueError):
                cr_f = None
            if cr_f is not None and cr_f >= cfg_c.threshold:
                out.append(
                    QualityWarning(
                        rule_id="META_COMPRESS",
                        description=cfg_c.description,
                        priority=warning_priority_from_signal(
                            strength=cr_f - cfg_c.threshold + 1
                        ),
                        explanation=(
                            f"compression_ratio={cr_f:.3f} ≥ {cfg_c.threshold}. "
                            "Heuristic only — not an accuracy percentage."
                        ),
                        evidence={"compression_ratio": cr_f},
                        segment_ids=[sid] if sid else [],
                        start_sec=float(start) if start is not None else None,
                        end_sec=float(end) if end is not None else None,
                    )
                )

        nsp = meta.get("no_speech_prob")
        cfg_n = _cfg(rules, "META_NOSPEECH")
        if cfg_n.enabled and nsp is not None and text:
            try:
                nsp_f = float(nsp)
            except (TypeError, ValueError):
                nsp_f = None
            if nsp_f is not None and nsp_f >= cfg_n.threshold:
                out.append(
                    QualityWarning(
                        rule_id="META_NOSPEECH",
                        description=cfg_n.description,
                        priority=ReviewPriority.MEDIUM,
                        explanation=(
                            f"no_speech_prob={nsp_f:.3f} with non-empty text. "
                            "Possible hallucination indicator — verify against audio."
                        ),
                        evidence={"no_speech_prob": nsp_f, "has_text": True},
                        segment_ids=[sid] if sid else [],
                        start_sec=float(start) if start is not None else None,
                        end_sec=float(end) if end is not None else None,
                    )
                )

        alp = meta.get("avg_logprob")
        cfg_l = _cfg(rules, "META_LOGPROB")
        if cfg_l.enabled and alp is not None:
            try:
                alp_f = float(alp)
            except (TypeError, ValueError):
                alp_f = None
            if alp_f is not None and alp_f <= cfg_l.threshold:
                out.append(
                    QualityWarning(
                        rule_id="META_LOGPROB",
                        description=cfg_l.description,
                        priority=ReviewPriority.LOW,
                        explanation=(
                            f"avg_logprob={alp_f:.3f} ≤ {cfg_l.threshold}. "
                            "Review signal only — never converted to accuracy %."
                        ),
                        evidence={"avg_logprob": alp_f},
                        segment_ids=[sid] if sid else [],
                        start_sec=float(start) if start is not None else None,
                        end_sec=float(end) if end is not None else None,
                    )
                )
    return out


def rule_audio(ctx: Any) -> list[QualityWarning]:
    out: list[QualityWarning] = []
    for issue in ctx.audio_issues or []:
        rule_id = issue.get("type") or "AUD_SILENCE"
        cfg = _cfg(ctx.rules, rule_id)
        if not cfg.enabled:
            continue
        pri = ReviewPriority.LOW
        if rule_id == "AUD_CLIP":
            pri = ReviewPriority.MEDIUM
        elif rule_id == "AUD_DURATION_MISMATCH":
            pri = ReviewPriority.MEDIUM
        elif rule_id == "AUD_SILENCE":
            pri = ReviewPriority.LOW
        out.append(
            QualityWarning(
                rule_id=rule_id,
                description=cfg.description,
                priority=pri,
                explanation=issue.get("detail") or cfg.description,
                evidence=issue,
                segment_ids=[],
                start_sec=issue.get("start_sec"),
                end_sec=issue.get("end_sec"),
            )
        )
    return out


def rule_language(ctx: Any) -> list[QualityWarning]:
    cfg = _cfg(ctx.rules, "LANG_MIX")
    if not cfg.enabled:
        return []
    out: list[QualityWarning] = []
    for issue in analyze_language_consistency(
        ctx.full_text,
        expected_language=ctx.expected_language,
        mix_ratio_threshold=cfg.threshold if cfg.threshold > 0 else 0.35,
    ):
        out.append(
            QualityWarning(
                rule_id="LANG_MIX",
                description=cfg.description,
                priority=ReviewPriority.LOW,
                explanation=issue.get("detail") or cfg.description,
                evidence=issue,
                segment_ids=[],
                start_sec=None,
                end_sec=None,
            )
        )
    return out


def rule_glossary(ctx: Any) -> list[QualityWarning]:
    cfg = _cfg(ctx.rules, "TERM_GLOSSARY")
    if not cfg.enabled:
        return []
    out: list[QualityWarning] = []
    for m in ctx.glossary_matches or []:
        out.append(
            QualityWarning(
                rule_id="TERM_GLOSSARY",
                description=cfg.description,
                priority=ReviewPriority.LOW,
                explanation=(
                    f"Possible terminology mismatch '{m.get('matched_text')}' → "
                    f"'{m.get('correct')}'. Optional glossary signal."
                ),
                evidence=m,
                segment_ids=[],
                start_sec=None,
                end_sec=None,
            )
        )
    return out


ALL_RULES = (
    rule_repetitions,
    rule_timestamps,
    rule_whisper_metadata,
    rule_audio,
    rule_language,
    rule_glossary,
)
