"""log_manager.py — Persistent JSON log of all export operations."""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from ffmpeg_runner import get_app_dir


_LOG_FILE = "export_log.json"
_FAILURE_LOG_DIR = "failure_logs"


def _log_path() -> Path:
    return get_app_dir() / _LOG_FILE


def _fmt_dur(secs: float) -> str:
    if secs <= 0:
        return "—"
    h, r = divmod(int(secs), 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def render_entry_text(entry: dict) -> str:
    """Human-readable rendering of one log entry — shared by the Log tab's detail
    pane and both the manual Export button and the auto-save-on-failure file, so
    all three always show exactly the same thing."""
    lines = []
    t = entry.get("type", "?")
    lines.append(f"{'─'*60}")
    lines.append(f"  {t.upper()} EXPORT — {entry.get('timestamp','')}")
    lines.append(f"{'─'*60}")
    lines.append(f"  Output  : {entry.get('output','—')}")
    lines.append(f"  Source  : {entry.get('source', entry.get('source_folder','—'))}")
    lines.append(f"  Size    : {entry.get('file_size_mb', 0):.2f} MB")
    lines.append(f"  Status  : {'OK' if entry.get('success') else 'FAILED — ' + entry.get('message','')}")

    if t == "whatsapp":
        lines.append(f"  Start   : {entry.get('start','—')}")
        lines.append(f"  Duration: {entry.get('duration','—')}")
        lines.append(f"  Grade   : {entry.get('grade','None')}")

    elif t == "merge":
        lines.append(f"  Folder  : {entry.get('source_folder','—')}")
        lines.append(f"  Clips   : {entry.get('clip_count',0)}    Total: {_fmt_dur(entry.get('total_duration_secs',0))}")
        mix = entry.get("mix", {}) or {}
        if mix.get("mix_enabled") or mix.get("enabled"):
            lines.append(f"  Mix     : on  ·  {mix.get('kind','lr')}"
                         + ("  ·  level-matched" if mix.get('match_levels') else ""))
        if mix.get("include_video") is False:
            lines.append("  Video   : excluded from output")
        lines.append("")
        lines.append("  Per-clip arrangement and sync:")
        clips = entry.get("clips", [])
        if not clips:
            lines.append("    —")
        for c in clips:
            name = c.get("name", "?")
            arr  = c.get("arrangement") or {}
            lines.append("")
            head = f"  • {name}"
            if arr.get("video"):
                head += f"   [video: {arr['video']}]"
            lines.append(head)

            tracks = arr.get("tracks") or []
            for tk in tracks:
                role = "  (default)" if tk.get("role") == "primary" else ""
                loss = "lossless" if tk.get("lossless") else "lossy"
                lines.append(f"      - {tk.get('label','?')}  [{tk.get('codec','')} · {loss}]{role}")

            for note in (arr.get("decisions") or []):
                lines.append(f"        → {note}")

            if c.get("has_wav") and not arr.get("is_slowmo"):
                off = c.get("audio_offset_ms")
                drift = c.get("drift_ms_per_min")
                conf = c.get("confidence_ms")
                pol = c.get("polarity_inverted")
                bits = []
                if off is not None:   bits.append(f"offset {off:+.1f} ms")
                if drift is not None: bits.append(f"drift {drift:+.1f} ms/min")
                if conf is not None:  bits.append(f"±{conf:.1f} ms")
                if pol:               bits.append("polarity flipped")
                if bits:
                    lines.append(f"        sync: {'  ·  '.join(bits)}")
            elif arr.get("is_slowmo"):
                f = arr.get("slowmo_factor")
                lines.append(f"        sync: slow-motion {f:.1f}× — WAV stretched, pitch-corrected"
                             if f else "        sync: slow-motion — WAV stretched")

    return "\n".join(lines)


def _auto_save_enabled() -> bool:
    try:
        from settings import Settings
        return bool(Settings().get("auto_save_log_on_failure", True))
    except Exception:
        return False


def _write_failure_txt(entry: dict) -> Optional[Path]:
    """Best-effort: dump a failed entry to its own timestamped .txt file, so a
    crash/failure log survives even if the user never opens the Log tab. Never
    raises — a failure to write this diagnostic must not mask the real failure."""
    try:
        d = get_app_dir() / _FAILURE_LOG_DIR
        d.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = d / f"{entry.get('type','export')}_failed_{ts}.txt"
        out.write_text(render_entry_text(entry), encoding="utf-8")
        return out
    except Exception:
        return None


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

    # Every entry lands in export_log.json regardless; a FAILED one additionally
    # gets its own standalone timestamped .txt so a diagnostic survives even if
    # the user never opens the Log tab — opt-out via the Log tab's checkbox.
    if not entry.get("success", True) and _auto_save_enabled():
        _write_failure_txt(entry)


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
    """Log a merge with per-clip audio offset + drift-correction details.

    The whole per-clip enrichment below is wrapped in one outer try/except: a
    failure ANYWHERE in it used to raise straight out of this function, before
    `_append()` (and therefore `_write_failure_txt()`) ever ran — confirmed as
    a real gap: a real merge failure left NO entry in export_log.json or
    failure_logs\\ at all, because building the rich per-clip breakdown for
    the log threw first. A failed export must always leave a record, even a
    thin one, so the fallback below still calls `_append()` with whatever
    survived unchanged plus a note about what broke."""
    try:
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

        entry = {
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
        }
    except Exception as e:
        entry = {
            "timestamp":        datetime.now().isoformat(timespec="seconds"),
            "type":             "merge",
            "output":           output,
            "source_folder":    source_folder,
            "track_order":      track_order,
            "mix":              mix or {"enabled": False},
            "clip_count":       len(clips) if clips is not None else 0,
            "total_duration_secs": 0,
            "clips":            [],
            "file_size_mb":     0.0,
            "success":          success,
            "message":          f"{message}\n\n(log enrichment failed: {e})",
        }
    _append(entry)
