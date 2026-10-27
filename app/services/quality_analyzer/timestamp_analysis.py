"""Timestamp anomaly detection using real segment timings only."""

from __future__ import annotations

from typing import Any


def analyze_timestamps(
    segments: list[dict[str, Any]],
    *,
    gap_threshold_sec: float = 5.0,
    long_segment_sec: float = 45.0,
) -> list[dict[str, Any]]:
    """Return structured timestamp issues. Never invents missing timings."""
    issues: list[dict[str, Any]] = []
    usable: list[dict[str, Any]] = []
    for seg in segments:
        start = seg.get("start_sec", seg.get("start"))
        end = seg.get("end_sec", seg.get("end"))
        if start is None or end is None:
            continue
        try:
            s = float(start)
            e = float(end)
        except (TypeError, ValueError):
            continue
        usable.append({**seg, "start_sec": s, "end_sec": e})

    if not usable:
        return issues

    usable.sort(key=lambda x: (x["start_sec"], x["end_sec"]))

    for seg in usable:
        dur = seg["end_sec"] - seg["start_sec"]
        if dur < 0:
            issues.append(
                {
                    "type": "TS_ORDER",
                    "segment_id": seg.get("id"),
                    "start_sec": seg["start_sec"],
                    "end_sec": seg["end_sec"],
                    "detail": "Segment end is before start",
                }
            )
        elif dur >= long_segment_sec:
            issues.append(
                {
                    "type": "TS_DURATION",
                    "segment_id": seg.get("id"),
                    "start_sec": seg["start_sec"],
                    "end_sec": seg["end_sec"],
                    "detail": f"Segment duration {dur:.1f}s exceeds threshold {long_segment_sec}s",
                    "duration": dur,
                }
            )

    for i in range(1, len(usable)):
        prev = usable[i - 1]
        cur = usable[i]
        if cur["start_sec"] < prev["start_sec"] - 1e-6:
            issues.append(
                {
                    "type": "TS_ORDER",
                    "segment_id": cur.get("id"),
                    "start_sec": cur["start_sec"],
                    "end_sec": cur["end_sec"],
                    "detail": "Non-monotonic segment start times",
                }
            )
        if cur["start_sec"] < prev["end_sec"] - 0.05:
            issues.append(
                {
                    "type": "TS_OVERLAP",
                    "segment_id": cur.get("id"),
                    "start_sec": cur["start_sec"],
                    "end_sec": min(cur["end_sec"], prev["end_sec"]),
                    "detail": f"Overlaps previous segment ending at {prev['end_sec']:.2f}s",
                    "prev_segment_id": prev.get("id"),
                }
            )
        gap = cur["start_sec"] - prev["end_sec"]
        if gap >= gap_threshold_sec:
            issues.append(
                {
                    "type": "TS_GAP",
                    "segment_id": cur.get("id"),
                    "start_sec": prev["end_sec"],
                    "end_sec": cur["start_sec"],
                    "detail": f"Gap of {gap:.1f}s between segments",
                    "gap": gap,
                    "prev_segment_id": prev.get("id"),
                }
            )

    return issues
