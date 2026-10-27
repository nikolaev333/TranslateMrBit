"""Optional long-audio chunking with global timestamp preservation.

Does not claim quality improvement. Disabled by default.
Never invents speech; uses silence gaps or fixed windows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class AudioChunk:
    index: int
    start_sec: float
    end_sec: float


def plan_fixed_chunks(
    duration_sec: float,
    *,
    chunk_sec: float = 600.0,
    overlap_sec: float = 1.0,
) -> list[AudioChunk]:
    if duration_sec <= 0 or chunk_sec <= 0:
        return []
    if duration_sec <= chunk_sec:
        return [AudioChunk(0, 0.0, duration_sec)]
    chunks: list[AudioChunk] = []
    start = 0.0
    idx = 0
    while start < duration_sec:
        end = min(duration_sec, start + chunk_sec)
        chunks.append(AudioChunk(idx, start, end))
        if end >= duration_sec:
            break
        start = max(0.0, end - overlap_sec)
        idx += 1
        if idx > 10_000:
            break
    return chunks


def merge_chunk_segments(
    chunk_results: list[tuple[AudioChunk, list[dict[str, Any]]]],
    *,
    overlap_dedupe_sec: float = 0.35,
) -> list[dict[str, Any]]:
    """Shift chunk-local timestamps to global and drop near-duplicate overlap text."""
    merged: list[dict[str, Any]] = []
    for chunk, segments in chunk_results:
        for seg in segments:
            try:
                local_start = float(seg.get("start", 0))
                local_end = float(seg.get("end", 0))
            except (TypeError, ValueError):
                continue
            text = str(seg.get("text") or "").strip()
            g_start = chunk.start_sec + local_start
            g_end = chunk.start_sec + local_end
            if merged:
                prev = merged[-1]
                # Overlap zone: skip near-duplicate text at boundary
                if (
                    g_start < prev["end"] + overlap_dedupe_sec
                    and text
                    and text == (prev.get("text") or "").strip()
                ):
                    continue
                if g_start < prev["end"]:
                    g_start = prev["end"]
            if g_end < g_start:
                g_end = g_start
            item = dict(seg)
            item["start"] = g_start
            item["end"] = g_end
            item["text"] = text
            merged.append(item)
    # Re-id
    for i, seg in enumerate(merged):
        seg["id"] = i
    return merged


def assemble_text(segments: list[dict[str, Any]]) -> str:
    return " ".join(
        str(s.get("text") or "").strip() for s in segments if str(s.get("text") or "").strip()
    ).strip()
