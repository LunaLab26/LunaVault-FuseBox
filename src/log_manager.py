"""log_manager.py — Persistent JSON log of all export operations."""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from ffmpeg_runner import get_app_dir


_LOG_FILE = "export_log.json"


def _log_path() -> Path:
    return get_app_dir() / _LOG_FILE


def load_log() -> list:
    """Return all log entries, newest first."""
    p = _log_path()
    if not p.exists():
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return list(reversed(data))   # newest first
    except Exception:
        pass
    return []


def _append(entry: dict):
    p = _log_path()
    entries = []
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                entries = json.load(f)
        except Exception:
            entries = []
    entries.append(entry)
    # Keep last 500 entries
    if len(entries) > 500:
        entries = entries[-500:]
    with open(p, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)


def log_whatsapp(
    source: str,
    output: str,
    start_str: str,
    duration_str: str,
    grade: Optional[str],
    success: bool,
    message: str,
):
    """Log a WhatsApp clip export."""
    size_mb = 0.0
    if success and Path(output).exists():
        size_mb = Path(output).stat().st_size / 1024 / 1024

    _append({
        "timestamp":   datetime.now().isoformat(timespec="seconds"),
        "type":        "whatsapp",
        "output":      output,
        "source":      source,
        "start":       start_str,
        "duration":    duration_str,
        "grade":       grade or "None",
        "file_size_mb": round(size_mb, 2),
        "success":     success,
        "message":     message,
    })


def log_merge(
    source_folder: str,
    output: str,
    clips: list,          # list of ClipInfo
    track_order: str,
    success: bool,
    message: str,
    mix: Optional[dict] = None,   # {"enabled","kind","make_default","match_levels"}
    plan=None,                    # core.ffmpeg_cmd.OutputPlan (for arrangement reasoning)
):
    """Log a merge with per-clip audio offset + drift-correction details."""
    size_mb = 0.0
    if success and Path(output).exists():
        size_mb = Path(output).stat().st_size / 1024 / 1024

    # Per-clip arrangement reasoning (which tracks were created and why).
    reports = {}
    if plan is not None:
        try:
            from core.plan_report import analyze_clip
            for c in clips:
                rep = analyze_clip(c, plan)
                reports[c.name] = {
                    "video": rep.video_action,
                    "is_slowmo": rep.is_slowmo,
                    "slowmo_factor": round(rep.slowmo_factor, 2) if rep.is_slowmo else None,
                    "tracks": [{"label": t.label, "codec": t.out_codec,
                                "lossless": t.lossless, "role": t.role} for t in rep.audio],
                    "decisions": rep.notes,
                    "est_size_mb": round(rep.est_bytes / 1024 / 1024, 1),
                }
        except Exception:
            reports = {}

    clip_details = []
    for c in clips:
        clip_details.append({
            "name":            c.name,
            "duration_secs":   round(c.duration, 3),
            "has_wav":         c.has_wav(),
            "audio_offset_ms": round(c.wav_offset * 1000, 1) if c.has_wav() else None,
            "offset_summary":  c.friendly_offset() if c.has_wav() else "—",
            # Sync analysis (lossless track = constant offset; drift → mix only)
            "drift_ms_per_min": round((c.sync_drift_ratio - 1.0) * 60000, 1) if c.has_wav() else None,
            "drift_ratio":      round(c.sync_drift_ratio, 8) if c.has_wav() else None,
            "confidence_ms":    round(c.sync_confidence_ms, 2) if c.has_wav() else None,
            "polarity_inverted": c.sync_polarity_inverted if c.has_wav() else None,
            "sync_windows":     c.sync_windows if c.has_wav() else None,
            "window_lags_ms":   c.sync_lags_ms if c.has_wav() else None,
            "manual_nudge_ms":  round(c.manual_nudge_ms, 1) if c.has_wav() else None,
            # Track arrangement reasoning (why these tracks, in this order)
            "arrangement":      reports.get(c.name),
        })

    total_dur = sum(c.duration for c in clips)

    _append({
        "timestamp":        datetime.now().isoformat(timespec="seconds"),
        "type":             "merge",
        "output":           output,
        "source_folder":    source_folder,
        "track_order":      track_order,
        "mix":              mix or {"enabled": False},
        "clip_count":       len(clips),
        "total_duration_secs": round(total_dur, 3),
        "clips":            clip_details,
        "file_size_mb":     round(size_mb, 2),
        "success":          success,
        "message":          message,
    })
