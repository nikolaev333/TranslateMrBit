"""Optional long-audio chunking with global timestamp preservation.

Does not claim quality improvement. Disabled by default.
Never invents speech; uses silence gaps or fixed windows.
Chunking must not silently drop speech — boundaries only move to silence when known.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class AudioChunk:
    index: int
    start_sec: float
    end_sec: float


@dataclass(frozen=True)
class SilenceGap:
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


def plan_silence_aware_chunks(
    duration_sec: float,
    silence_gaps: list[SilenceGap] | list[tuple[float, float]],
    *,
    chunk_sec: float = 600.0,
    overlap_sec: float = 1.0,
    min_chunk_sec: float = 30.0,
) -> list[AudioChunk]:
    """Prefer cutting inside known silence; fall back to fixed windows.

    Does not remove speech — silence gaps only adjust cut points.
    """
    if duration_sec <= 0 or chunk_sec <= 0:
        return []
    if duration_sec <= chunk_sec:
        return [AudioChunk(0, 0.0, duration_sec)]

    gaps: list[SilenceGap] = []
    for g in silence_gaps:
        if isinstance(g, SilenceGap):
            gaps.append(g)
        else:
            gaps.append(SilenceGap(float(g[0]), float(g[1])))
    gaps = sorted(
        (g for g in gaps if g.end_sec > g.start_sec),
        key=lambda x: x.start_sec,
    )

    chunks: list[AudioChunk] = []
    start = 0.0
    idx = 0
    while start < duration_sec - 1e-6:
        target = min(duration_sec, start + chunk_sec)
        cut = target
        # Look for a silence gap near the target window end
        window_lo = start + min_chunk_sec
        window_hi = target
        best: SilenceGap | None = None
        for gap in gaps:
            mid = (gap.start_sec + gap.end_sec) / 2.0
            if window_lo <= mid <= window_hi:
                best = gap
        if best is not None:
            cut = min(duration_sec, (best.start_sec + best.end_sec) / 2.0)
            if cut <= start + 1e-3:
                cut = target
        chunks.append(AudioChunk(idx, start, cut))
        if cut >= duration_sec - 1e-6:
            break
        start = max(0.0, cut - overlap_sec)
        idx += 1
        if idx > 10_000:
            break
    if not chunks:
        return plan_fixed_chunks(
            duration_sec, chunk_sec=chunk_sec, overlap_sec=overlap_sec
        )
    # Ensure last chunk reaches duration
    if chunks[-1].end_sec < duration_sec - 1e-3:
        chunks.append(AudioChunk(len(chunks), chunks[-1].end_sec, duration_sec))
    return chunks


def parse_ffmpeg_silencedetect(stderr_text: str) -> list[SilenceGap]:
    """Parse ffmpeg silencedetect filter output. Never invents gaps."""
    import re

    starts: list[float] = []
    gaps: list[SilenceGap] = []
    for line in stderr_text.splitlines():
        m_start = re.search(r"silence_start:\s*([0-9.]+)", line)
        if m_start:
            starts.append(float(m_start.group(1)))
            continue
        m_end = re.search(r"silence_end:\s*([0-9.]+)", line)
        if m_end and starts:
            end = float(m_end.group(1))
            start = starts.pop(0)
            if end > start:
                gaps.append(SilenceGap(start, end))
    return gaps


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


def detect_silence_gaps(
    audio_path: str,
    *,
    noise_db: float = -30.0,
    min_silence_sec: float = 0.4,
    timeout_sec: float = 60.0,
) -> list[SilenceGap]:
    """Best-effort ffmpeg silencedetect. Returns [] on failure — never invents gaps."""
    import subprocess
    from pathlib import Path

    path = Path(audio_path)
    if not path.is_file():
        return []
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-i",
        str(path),
        "-af",
        f"silencedetect=noise={noise_db}dB:d={min_silence_sec}",
        "-f",
        "null",
        "-",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return parse_ffmpeg_silencedetect((proc.stderr or "") + "\n" + (proc.stdout or ""))


def extract_audio_chunk(
    source_path: str,
    dest_path: str,
    start_sec: float,
    end_sec: float,
    *,
    timeout_sec: float = 120.0,
) -> None:
    """Extract [start, end) audio via ffmpeg. Raises on failure."""
    import subprocess
    from pathlib import Path

    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.05, float(end_sec) - float(start_sec))
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-ss",
        f"{float(start_sec):.3f}",
        "-i",
        str(source_path),
        "-t",
        f"{duration:.3f}",
        "-ac",
        "1",
        "-ar",
        "16000",
        str(dest),
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        check=False,
    )
    if proc.returncode != 0 or not dest.is_file() or dest.stat().st_size <= 0:
        raise RuntimeError(
            f"ffmpeg chunk extract failed (code={proc.returncode})"
        )
