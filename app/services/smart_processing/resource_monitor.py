"""Lightweight hardware / container resource monitoring (no torch)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any


def _read_cgroup_memory_limit() -> int | None:
    """Return container memory limit in bytes when cgroup constrains it."""
    candidates = [
        Path("/sys/fs/cgroup/memory.max"),
        Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
    ]
    for path in candidates:
        try:
            raw = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if raw in {"max", ""}:
            continue
        try:
            value = int(raw)
        except ValueError:
            continue
        # Ignore absurdly large "unlimited" placeholders from cgroup v1
        if value > 1 << 60:
            continue
        return value
    return None


def _read_cgroup_memory_usage() -> int | None:
    candidates = [
        Path("/sys/fs/cgroup/memory.current"),
        Path("/sys/fs/cgroup/memory/memory.usage_in_bytes"),
    ]
    for path in candidates:
        try:
            return int(path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue
    return None


def _dir_size(path: str | Path, *, max_entries: int = 50_000) -> int | None:
    root = Path(path)
    if not root.exists():
        return None
    total = 0
    count = 0
    try:
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            try:
                total += p.stat().st_size
            except OSError:
                continue
            count += 1
            if count >= max_entries:
                break
    except OSError:
        return None
    return total


def collect_resources(
    *,
    upload_dir: str | None = None,
    output_dir: str | None = None,
    model_dir: str | None = None,
) -> dict[str, Any]:
    """Collect available metrics. Missing values are reported as null — never faked."""
    result: dict[str, Any] = {
        "cpu_count_logical": os.cpu_count(),
        "cpu_percent": None,
        "memory": {
            "host_total_bytes": None,
            "host_available_bytes": None,
            "process_rss_bytes": None,
            "cgroup_limit_bytes": _read_cgroup_memory_limit(),
            "cgroup_usage_bytes": _read_cgroup_memory_usage(),
            "effective_limit_bytes": None,
            "effective_available_bytes": None,
        },
        "disk": {
            "data_total_bytes": None,
            "data_free_bytes": None,
            "models_bytes": None,
            "uploads_bytes": None,
            "outputs_bytes": None,
        },
        "gpu": {
            "available": False,
            "detail": "GPU inspection skipped (no torch in API monitoring)",
            "devices": [],
        },
        "source": [],
    }

    try:
        import psutil  # type: ignore

        result["source"].append("psutil")
        vm = psutil.virtual_memory()
        result["memory"]["host_total_bytes"] = int(vm.total)
        result["memory"]["host_available_bytes"] = int(vm.available)
        result["cpu_percent"] = float(psutil.cpu_percent(interval=0.05))
        proc = psutil.Process(os.getpid())
        result["memory"]["process_rss_bytes"] = int(proc.memory_info().rss)
        # Disk for data mount
        probe = upload_dir or model_dir or "/"
        usage = shutil.disk_usage(probe)
        result["disk"]["data_total_bytes"] = int(usage.total)
        result["disk"]["data_free_bytes"] = int(usage.free)
    except ImportError:
        result["source"].append("stdlib-fallback")
        # Best-effort /proc meminfo
        try:
            info: dict[str, int] = {}
            with open("/proc/meminfo", encoding="utf-8") as fh:
                for line in fh:
                    parts = line.split()
                    if len(parts) >= 2 and parts[0].endswith(":"):
                        info[parts[0][:-1]] = int(parts[1]) * 1024
            result["memory"]["host_total_bytes"] = info.get("MemTotal")
            result["memory"]["host_available_bytes"] = info.get("MemAvailable")
        except OSError:
            pass
        try:
            usage = shutil.disk_usage(upload_dir or model_dir or ".")
            result["disk"]["data_total_bytes"] = int(usage.total)
            result["disk"]["data_free_bytes"] = int(usage.free)
        except OSError:
            pass

    mem = result["memory"]
    cgroup_limit = mem["cgroup_limit_bytes"]
    host_total = mem["host_total_bytes"]
    host_avail = mem["host_available_bytes"]
    if cgroup_limit is not None:
        mem["effective_limit_bytes"] = cgroup_limit
        used = mem["cgroup_usage_bytes"]
        if used is not None:
            mem["effective_available_bytes"] = max(0, cgroup_limit - used)
        elif host_avail is not None:
            mem["effective_available_bytes"] = min(host_avail, cgroup_limit)
    else:
        mem["effective_limit_bytes"] = host_total
        mem["effective_available_bytes"] = host_avail

    if model_dir:
        result["disk"]["models_bytes"] = _dir_size(model_dir)
    if upload_dir:
        result["disk"]["uploads_bytes"] = _dir_size(upload_dir)
    if output_dir:
        result["disk"]["outputs_bytes"] = _dir_size(output_dir)

    # Optional nvidia-smi (no torch)
    nvidia = shutil.which("nvidia-smi")
    if nvidia:
        try:
            import subprocess

            proc = subprocess.run(
                [
                    nvidia,
                    "--query-gpu=name,memory.total,memory.free",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                devices = []
                for line in proc.stdout.strip().splitlines():
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 3:
                        devices.append(
                            {
                                "name": parts[0],
                                "memory_total_mb": float(parts[1]),
                                "memory_free_mb": float(parts[2]),
                            }
                        )
                result["gpu"] = {
                    "available": bool(devices),
                    "detail": "nvidia-smi",
                    "devices": devices,
                }
        except (OSError, subprocess.TimeoutExpired, ValueError):
            pass

    return result


def effective_available_ram_gb(resources: dict[str, Any] | None = None) -> float | None:
    resources = resources or collect_resources()
    avail = (resources.get("memory") or {}).get("effective_available_bytes")
    if avail is None:
        return None
    return float(avail) / (1024**3)
