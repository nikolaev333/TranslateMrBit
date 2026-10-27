"""Normalized transcript segments for MrBit Transcription Studio.

Original TranslateMrBit application code. Uses Whisper segment timestamps as-is;
never invents timing data.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any


def new_segment_id() -> str:
    return str(uuid.uuid4())


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_whisper_segments(result: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    """Convert Whisper result segments into persistable rows.

    Returns (segments, has_word_timestamps). Empty list means unsupported —
    caller must not invent timings.
    """
    raw = result.get("segments") or []
    if not isinstance(raw, list) or not raw:
        return [], False

    has_words = False
    out: list[dict[str, Any]] = []
    position = 0
    for item in raw:
        if not isinstance(item, dict):
            continue
        start = _safe_float(item.get("start"))
        end = _safe_float(item.get("end"))
        text = str(item.get("text") or "").strip()
        if start is None or end is None:
            continue
        if end < start:
            continue
        words_json = None
        words = item.get("words")
        if isinstance(words, list) and words:
            cleaned = []
            for w in words:
                if not isinstance(w, dict):
                    continue
                ws = _safe_float(w.get("start"))
                we = _safe_float(w.get("end"))
                wt = str(w.get("word") or w.get("text") or "").strip()
                if ws is None or we is None or not wt:
                    continue
                cleaned.append({"start": ws, "end": we, "word": wt})
            if cleaned:
                words_json = json.dumps(cleaned, ensure_ascii=False)
                has_words = True

        out.append(
            {
                "id": new_segment_id(),
                "position": position,
                "start_sec": start,
                "end_sec": end,
                "original_text": text,
                "edited_text": text,
                "is_edited": False,
                "words_json": words_json,
                "edit_source": None,
            }
        )
        position += 1

    return out, has_words


def join_segment_texts(segments: list[dict[str, Any]], *, use_edited: bool = True) -> str:
    parts: list[str] = []
    for seg in sorted(segments, key=lambda s: int(s.get("position") or 0)):
        text = seg.get("edited_text") if use_edited else seg.get("original_text")
        text = (text or "").strip()
        if text:
            parts.append(text)
    return " ".join(parts)


def format_srt_timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"


def format_vtt_timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{milli:03d}"


def segments_to_srt(segments: list[dict[str, Any]], *, use_edited: bool = True) -> str:
    lines: list[str] = []
    ordered = sorted(segments, key=lambda s: int(s.get("position") or 0))
    for i, seg in enumerate(ordered, start=1):
        text = (seg.get("edited_text") if use_edited else seg.get("original_text")) or ""
        text = text.strip()
        if not text:
            continue
        start = float(seg["start_sec"])
        end = float(seg["end_sec"])
        lines.append(str(i))
        lines.append(f"{format_srt_timestamp(start)} --> {format_srt_timestamp(end)}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines).rstrip() + ("\n" if lines else "")


def segments_to_vtt(segments: list[dict[str, Any]], *, use_edited: bool = True) -> str:
    lines = ["WEBVTT", ""]
    ordered = sorted(segments, key=lambda s: int(s.get("position") or 0))
    for seg in ordered:
        text = (seg.get("edited_text") if use_edited else seg.get("original_text")) or ""
        text = text.strip()
        if not text:
            continue
        start = float(seg["start_sec"])
        end = float(seg["end_sec"])
        lines.append(f"{format_vtt_timestamp(start)} --> {format_vtt_timestamp(end)}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines)


def segments_to_json_export(
    job_id: str,
    segments: list[dict[str, Any]],
    *,
    use_edited: bool = True,
) -> str:
    payload = {
        "job_id": job_id,
        "source": "edited" if use_edited else "original",
        "segments": [
            {
                "id": s["id"],
                "position": s["position"],
                "start": s["start_sec"],
                "end": s["end_sec"],
                "text": (s.get("edited_text") if use_edited else s.get("original_text")) or "",
                "original_text": s.get("original_text") or "",
                "edited_text": s.get("edited_text") or "",
                "is_edited": bool(s.get("is_edited")),
            }
            for s in sorted(segments, key=lambda x: int(x.get("position") or 0))
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def validate_split_at(start_sec: float, end_sec: float, split_at: float) -> None:
    if not (start_sec < split_at < end_sec):
        raise ValueError(
            f"split_at must be strictly inside segment range ({start_sec}, {end_sec})"
        )


def split_text_roughly(text: str) -> tuple[str, str]:
    """Split segment text for UI convenience only — timings come from split_at."""
    text = text or ""
    stripped = text.strip()
    if not stripped:
        return "", ""
    # Prefer splitting near middle on whitespace
    mid = len(stripped) // 2
    left = stripped.rfind(" ", 0, mid + 1)
    right = stripped.find(" ", mid)
    cut = left if left > 0 else (right if right > 0 else mid)
    a = stripped[:cut].strip()
    b = stripped[cut:].strip()
    if not a:
        a, b = stripped, ""
    return a, b


def guess_content_type(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return {
        "mp3": "audio/mpeg",
        "wav": "audio/wav",
        "m4a": "audio/mp4",
        "mp4": "video/mp4",
        "flac": "audio/flac",
        "ogg": "audio/ogg",
        "webm": "audio/webm",
        "aac": "audio/aac",
        "wma": "audio/x-ms-wma",
        "mkv": "video/x-matroska",
        "mov": "video/quicktime",
        "opus": "audio/opus",
    }.get(ext, "application/octet-stream")


_WHITESPACE_RE = re.compile(r"\s+")


def normalize_search_haystack(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", (text or "")).strip()
