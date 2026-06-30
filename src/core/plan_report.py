"""core/plan_report.py — explain and estimate what a merge will do.

A single analysis that mirrors the decisions build_mux_cmd_plan makes (missing
camera audio, slow-motion stretch, mix availability, video copy vs transcode) and
turns them into human-readable reasoning plus size/time estimates. Powers both
the richer Log detail and the pre-flight breakdown dialog, so the explanation and
the actual command never drift (a test asserts they agree).
"""

from dataclasses import dataclass, field
from typing import List

from clip_model import ClipInfo
from core.ffmpeg_cmd import OutputPlan, is_slowmo

# Rough size model
_ALAC_RATIO   = 0.6      # ALAC ≈ 60% of PCM WAV
_AAC_BPS      = 256_000  # mix / stretched track
_CAMERA_BPS   = 192_000  # camera AAC (copy ≈ source)
_TRANSCODE_VIDEO_RATIO = 0.85   # 4K HEVC CRF18 ≈ 85% of source size (very rough)

# Rough time model (realtime multipliers)
_GPU_TRANSCODE_X = 4.0
_CPU_TRANSCODE_X = 0.5
_ALAC_X          = 25.0     # ALAC encode speed vs realtime
_COPY_MBPS       = 300.0    # stream-copy / concat disk throughput


@dataclass
class TrackPlan:
    label: str
    out_codec: str
    lossless: bool
    role: str          # "primary" | "secondary"
    est_bytes: int = 0


@dataclass
class ClipReport:
    name: str
    duration: float
    has_wav: bool
    video_included: bool
    video_action: str          # "Stream copy" | "Transcode → 4K HEVC" | "Excluded"
    is_slowmo: bool
    slowmo_factor: float       # video / wav
    audio: List[TrackPlan] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    est_bytes: int = 0
    best_secs: float = 0.0
    worst_secs: float = 0.0


@dataclass
class MergeReport:
    clips: List[ClipReport] = field(default_factory=list)
    total_bytes: int = 0
    best_secs: float = 0.0
    worst_secs: float = 0.0
    n_transcode: int = 0
    n_slowmo: int = 0
    n_no_camera: int = 0


def _wav_bytes(clip) -> int:
    try:
        return clip.wav_path.stat().st_size
    except Exception:
        return 0


def _video_bytes(clip) -> int:
    try:
        return clip.path.stat().st_size
    except Exception:
        return 0


def analyze_clip(clip: ClipInfo, plan: OutputPlan) -> ClipReport:
    is_conform = clip.status == "ok"
    has_wav    = clip.has_wav()
    has_cam    = clip.has_camera_audio()
    dur        = clip.duration

    r = ClipReport(name=clip.stem, duration=dur, has_wav=has_wav,
                   video_included=plan.include_video, is_slowmo=False,
                   slowmo_factor=0.0, video_action="Excluded")

    # ── Video ───────────────────────────────────────────────────────────────
    vbytes = 0
    if plan.include_video:
        if is_conform:
            r.video_action = "Stream copy"
            vbytes = _video_bytes(clip)
        else:
            r.video_action = "Transcode → 4K HEVC"
            conf = ", ".join(clip.conflicts) if clip.conflicts else "spec mismatch"
            r.notes.append(f"Video conformed ({conf}) — re-encoded at high quality")
            vbytes = int(_video_bytes(clip) * _TRANSCODE_VIDEO_RATIO)
    else:
        r.notes.append("Video excluded from output")

    # ── Audio ── built from the same per-slot logic the muxer uses ───────────
    from core.ffmpeg_cmd import _slot_fill, MixSpec
    mix = MixSpec(kind=plan.mix_kind, match_levels=plan.mix_match_levels)
    slowmo = is_slowmo(clip)

    if slowmo:
        r.is_slowmo = True
        factor = dur / clip.wav_duration if clip.wav_duration else 0.0
        r.slowmo_factor = factor
        r.notes.append(f"Slow-motion: video {dur:.1f}s vs WAV {clip.wav_duration:.1f}s "
                       f"(~{factor:.1f}× slower)")
        r.notes.append(f"Primary audio = WAV stretched {clip.wav_duration:.1f}s → {dur:.1f}s, "
                       "pitch-corrected (atempo)")
    elif not has_cam and has_wav:
        r.notes.append("MP4 has no camera audio → primary uses the WAV")
    elif not has_cam and not has_wav:
        r.notes.append("Clip has no audio source → silent tracks (kept for a consistent layout)")

    _codec_label = {"copy": "AAC (copy)", "wav_alac": "ALAC", "wav_aac": "AAC 256k",
                    "stretch": "AAC 256k", "mix": "AAC 256k"}
    had_silence = False
    for kind in (t.kind for t in plan.tracks if t.enabled):
        fill, codec, title = _slot_fill(kind, clip, mix)
        lossless = (codec == "alac")
        if fill == "silence":
            had_silence = True
            label = f"silent {'ALAC' if lossless else 'AAC'}"
            eb = 0
        else:
            label = _codec_label.get(fill, "AAC 256k")
            if fill == "copy":
                eb = int(_CAMERA_BPS / 8 * dur)
            elif fill == "wav_alac":
                eb = int(_wav_bytes(clip) * _ALAC_RATIO)
            else:
                eb = int(_AAC_BPS / 8 * dur)
        r.audio.append(TrackPlan(title, label, lossless, "", eb))
    if r.audio:
        r.audio[0].role = "primary"
    if had_silence and not (not has_cam and not has_wav):
        r.notes.append("Some slots are silent — kept so every clip has an identical track layout")

    r.est_bytes = vbytes + sum(a.est_bytes for a in r.audio)

    # ── Time estimate ─────────────────────────────────────────────────────────
    if r.video_action.startswith("Transcode"):
        best = dur / _GPU_TRANSCODE_X
        worst = dur / _CPU_TRANSCODE_X
    else:
        copy_io = (r.est_bytes / 1024 / 1024) / _COPY_MBPS
        alac = (clip.wav_duration / _ALAC_X) if (has_wav and clip.wav_duration) else 0.0
        best = worst = copy_io + alac
    r.best_secs, r.worst_secs = best, worst
    return r


def analyze_merge(clips, plan: OutputPlan) -> MergeReport:
    rep = MergeReport()
    ordered = sorted(clips, key=lambda c: c.order_idx)
    for c in ordered:
        cr = analyze_clip(c, plan)
        rep.clips.append(cr)
        rep.total_bytes += cr.est_bytes
        rep.best_secs   += cr.best_secs
        rep.worst_secs  += cr.worst_secs
        if cr.video_action.startswith("Transcode"):
            rep.n_transcode += 1
        if cr.is_slowmo:
            rep.n_slowmo += 1
        if not c.has_camera_audio() and c.has_wav():
            rep.n_no_camera += 1
    # final concat is a stream copy of the whole thing
    concat_io = (rep.total_bytes / 1024 / 1024) / _COPY_MBPS
    rep.best_secs += concat_io
    rep.worst_secs += concat_io
    return rep
