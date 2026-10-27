"""Explainable review-priority scoring (not transcription accuracy)."""

from __future__ import annotations

from typing import Any

from app.services.quality_analyzer.schemas import (
    DEFAULT_RULES,
    QualityWarning,
    ReviewPriority,
    RuleConfig,
)


PRIORITY_RANK = {
    ReviewPriority.INSUFFICIENT_EVIDENCE: 0,
    ReviewPriority.LOW: 1,
    ReviewPriority.MEDIUM: 2,
    ReviewPriority.HIGH: 3,
}


def priority_for_weight_sum(weighted: float) -> ReviewPriority:
    if weighted <= 0:
        return ReviewPriority.INSUFFICIENT_EVIDENCE
    if weighted < 2.0:
        return ReviewPriority.LOW
    if weighted < 4.5:
        return ReviewPriority.MEDIUM
    return ReviewPriority.HIGH


def score_warnings(
    warnings: list[QualityWarning],
    rules: dict[str, RuleConfig] | None = None,
) -> tuple[ReviewPriority, dict[str, Any]]:
    """Combine warning weights into an explainable review priority."""
    rules = rules or DEFAULT_RULES
    by_rule: dict[str, float] = {}
    reasons: list[str] = []

    for w in warnings:
        cfg = rules.get(w.rule_id) or RuleConfig(w.rule_id, weight=1.0)
        if not cfg.enabled:
            continue
        # Priority bump for HIGH individual warnings
        bump = 1.0
        if w.priority == ReviewPriority.HIGH:
            bump = 1.5
        elif w.priority == ReviewPriority.LOW:
            bump = 0.6
        elif w.priority == ReviewPriority.INSUFFICIENT_EVIDENCE:
            bump = 0.2
        contrib = cfg.weight * bump
        by_rule[w.rule_id] = by_rule.get(w.rule_id, 0.0) + contrib
        reasons.append(f"{w.rule_id}: {w.description} (weight≈{contrib:.2f})")

    total = sum(by_rule.values())
    overall = priority_for_weight_sum(total)

    # Escalate if any single HIGH warning of critical rules
    critical = {"REP_LOOP", "TS_OVERLAP", "META_COMPRESS", "AUD_CLIP"}
    if any(w.priority == ReviewPriority.HIGH and w.rule_id in critical for w in warnings):
        if PRIORITY_RANK[overall] < PRIORITY_RANK[ReviewPriority.HIGH]:
            overall = ReviewPriority.HIGH
            reasons.append("Escalated: critical HIGH warning present")

    if not warnings:
        overall = ReviewPriority.INSUFFICIENT_EVIDENCE
        reasons.append("No heuristic warnings fired — insufficient evidence for elevated review")

    return overall, {
        "weighted_sum": total,
        "by_rule": by_rule,
        "reasons": reasons[:50],
        "note": (
            "Review priority is a heuristic for human attention. "
            "It is not a measured transcription accuracy percentage."
        ),
    }


def warning_priority_from_signal(
    *,
    strength: float,
    medium_at: float = 1.0,
    high_at: float = 2.0,
) -> ReviewPriority:
    if strength <= 0:
        return ReviewPriority.INSUFFICIENT_EVIDENCE
    if strength >= high_at:
        return ReviewPriority.HIGH
    if strength >= medium_at:
        return ReviewPriority.MEDIUM
    return ReviewPriority.LOW
