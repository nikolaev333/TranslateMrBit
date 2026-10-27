"""Optional lightweight audio diagnostics via FFmpeg/FFprobe (no torch)."""

from __future__ import annotations

import json
import logging
import math
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def probe_duration_seconds(path: str | Path) -> float | None:
    path = Path(path)
    if not path.is_file() or shutil.which("ffprobe") is None:
        return None
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        if proc.returncode != 0:
            return None
        data = json.loads(proc.stdout or "{}")
        dur = (data.get("format") or {}).get("duration")
        return float(dur) if dur is not None else None
    except (OSError, ValueError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


def _decode_mono_pcm16(path: Path, *, max_seconds: float = 600.0) -> tuple[list[float], int] | None:
    """Decode a limited mono PCM preview via ffmpeg. Memory-bounded."""
    if shutil.which("ffmpeg") is None:
        return None
    # Cap samples: 16kHz * max_seconds
    sr = 16000
    max_samples = int(sr * max_seconds)
    try:
        proc = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(path),
                "-t",
                str(max_seconds),
                "-ac",
                "1",
                "-ar",
                str(sr),
                "-f",
                "s16le",
                "-",
            ],
            capture_output=True,
            timeout=15,
            check=False,
        )
        if proc.returncode != 0 or not proc.stdout:
            return None
        raw = proc.stdout[: max_samples * 2]
        samples: list[float] = []
        for i in range(0, len(raw) - 1, 2):
            val = int.from_bytes(raw[i : i + 2], "little", signed=True) / 32768.0
            samples.append(val)
        return samples, sr
    except (OSError, subprocess.TimeoutExpired):
        return None


def analyze_audio_file(
    path: str | Path,
    *,
    silence_threshold_sec: float = 2.0,
    low_volume_rms: float = 0.02,
    clip_peak: float = 0.98,
    transcript_end_sec: float | None = None,
    duration_mismatch_sec: float = 3.0,
    max_analyze_seconds: float = 600.0,
) -> list[dict[str, Any]]:
    """Return audio diagnostic issues. Silence is informational, not an error."""
    path = Path(path)
    issues: list[dict[str, Any]] = []
    if not path.is_file():
        return issues

    duration = probe_duration_seconds(path)
    if (
        duration is not None
        and transcript_end_sec is not None
        and abs(duration - transcript_end_sec) >= duration_mismatch_sec
    ):
        issues.append(
            {
                "type": "AUD_DURATION_MISMATCH",
                "start_sec": 0.0,
                "end_sec": duration,
                "detail": (
                    f"Audio duration {duration:.1f}s vs last transcript end "
                    f"{transcript_end_sec:.1f}s (threshold {duration_mismatch_sec}s)"
                ),
                "audio_duration": duration,
                "transcript_end": transcript_end_sec,
            }
        )

    decoded = _decode_mono_pcm16(path, max_seconds=max_analyze_seconds)
    if not decoded:
        # Fallback: try reading WAV directly for synthetic tests
        try:
            with wave.open(str(path), "rb") as wf:
                sr = wf.getframerate()
                n = wf.getnframes()
                raw = wf.readframes(min(n, int(sr * max_analyze_seconds)))
                width = wf.getsampwidth()
                if width != 2:
                    return issues
                samples = [
                    int.from_bytes(raw[i : i + 2], "little", signed=True) / 32768.0
                    for i in range(0, len(raw) - 1, 2)
                ]
                decoded = (samples, sr)
        except (OSError, wave.Error):
            return issues

    samples, sr = decoded
    if not samples:
        return issues

    window = max(1, int(sr * 0.5))  # 500ms windows
    silence_run_start: float | None = None
    low_run_start: float | None = None

    for i in range(0, len(samples), window):
        chunk = samples[i : i + window]
        if not chunk:
            continue
        t0 = i / sr
        t1 = (i + len(chunk)) / sr
        peak = max(abs(x) for x in chunk)
        rms = math.sqrt(sum(x * x for x in chunk) / len(chunk))

        if peak >= clip_peak:
            issues.append(
                {
                    "type": "AUD_CLIP",
                    "start_sec": t0,
                    "end_sec": t1,
                    "detail": f"Potential clipping (peak={peak:.3f})",
                    "peak": peak,
                }
            )

        if rms < low_volume_rms:
            if low_run_start is None:
                low_run_start = t0
            # silence vs low volume: very low rms counts as silence candidate
            if rms < low_volume_rms * 0.35:
                if silence_run_start is None:
                    silence_run_start = t0
            else:
                if silence_run_start is not None:
                    if t0 - silence_run_start >= silence_threshold_sec:
                        issues.append(
                            {
                                "type": "AUD_SILENCE",
                                "start_sec": silence_run_start,
                                "end_sec": t0,
                                "detail": (
                                    f"Silence interval ~{t0 - silence_run_start:.1f}s "
                                    "(informational — not an error by itself)"
                                ),
                            }
                        )
                    silence_run_start = None
        else:
            if low_run_start is not None and t0 - low_run_start >= silence_threshold_sec:
                issues.append(
                    {
                        "type": "AUD_LOWVOL",
                        "start_sec": low_run_start,
                        "end_sec": t0,
                        "detail": f"Low-volume region ~{t0 - low_run_start:.1f}s",
                    }
                )
            low_run_start = None
            if silence_run_start is not None:
                if t0 - silence_run_start >= silence_threshold_sec:
                    issues.append(
                        {
                            "type": "AUD_SILENCE",
                            "start_sec": silence_run_start,
                            "end_sec": t0,
                            "detail": (
                                f"Silence interval ~{t0 - silence_run_start:.1f}s "
                                "(informational — not an error by itself)"
                            ),
                        }
                    )
                silence_run_start = None

    # Cap issue count to keep reports bounded
    return issues[:200]
