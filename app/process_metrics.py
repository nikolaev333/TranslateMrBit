"""Lightweight process CPU sampling via /proc (no psutil)."""

from __future__ import annotations

import os
import time
from typing import Any


def _read_proc_stat_totals() -> float | None:
    """Sum of all CPU time fields from /proc/stat (jiffies)."""
    try:
        with open("/proc/stat", encoding="utf-8") as fh:
            line = fh.readline()
    except OSError:
        return None
    if not line.startswith("cpu "):
        return None
    parts = line.split()[1:]
    try:
        return float(sum(int(p) for p in parts))
    except ValueError:
        return None


def _read_self_cpu_jiffies() -> float | None:
    """utime + stime for this process from /proc/self/stat (jiffies)."""
    try:
        with open("/proc/self/stat", encoding="utf-8") as fh:
            data = fh.read()
    except OSError:
        return None
    # comm may contain spaces/parens — split after last ')'
    try:
        rest = data[data.rindex(")") + 2 :].split()
        utime = int(rest[11])
        stime = int(rest[12])
        return float(utime + stime)
    except (ValueError, IndexError):
        return None


def new_cpu_sampler() -> dict[str, Any]:
    return {
        "proc": _read_self_cpu_jiffies(),
        "total": _read_proc_stat_totals(),
        "wall": time.monotonic(),
        "ncpus": max(1, os.cpu_count() or 1),
    }


def sample_process_cpu_percent(state: dict[str, Any]) -> float | None:
    """Return process CPU % since last sample; update *state* in place.

    Percentage is normalized to a single core scale (0–100+ possible under load),
    clamped to 0–100 for UI display.
    """
    proc = _read_self_cpu_jiffies()
    total = _read_proc_stat_totals()
    wall = time.monotonic()
    prev_proc = state.get("proc")
    prev_total = state.get("total")
    prev_wall = state.get("wall")
    ncpus = float(state.get("ncpus") or max(1, os.cpu_count() or 1))

    state["proc"] = proc
    state["total"] = total
    state["wall"] = wall

    if proc is None or prev_proc is None:
        return None

    delta_proc = proc - prev_proc
    if delta_proc < 0:
        return None

    if total is not None and prev_total is not None and total > prev_total:
        # Share of total host CPU, scaled to 0–100 for this process.
        pct = 100.0 * delta_proc / (total - prev_total)
    elif prev_wall is not None:
        # Fallback: wall-clock based (assumes HZ≈100; rough without CLK_TCK).
        elapsed = wall - float(prev_wall)
        if elapsed <= 0:
            return None
        # jiffies ≈ 100/sec on Linux; convert to % of one core then / ncpus for host view
        pct = (delta_proc / 100.0) / elapsed * 100.0 / ncpus
    else:
        return None

    return max(0.0, min(100.0, pct))
