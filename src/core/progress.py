"""core/progress.py — parse ffmpeg -progress key/value output."""

from pathlib import Path


def read_progress(progress_file: Path) -> dict:
    """Read the latest key=value lines ffmpeg writes to its -progress file."""
    data = {}
    try:
        with open(progress_file, "r", errors="ignore") as f:
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    data[k] = v
    except Exception:
        pass
    return data


def parse_progress(data: dict, total_duration: float) -> dict:
    """Turn raw progress data into {pct, size, current_time}."""
    try:
        out_us = int(data.get("out_time_us", 0) or 0)
    except (ValueError, TypeError):
        out_us = 0
    try:
        size = int(data.get("total_size", 0) or 0)
    except (ValueError, TypeError):
        size = 0
    pct = min(100.0, out_us / (total_duration * 1e6) * 100) if total_duration > 0 else 0.0
    return {"pct": pct, "size": size, "current_time": out_us / 1e6}
