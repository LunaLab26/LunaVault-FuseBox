"""core/ffmpeg_cmd.py — build ffmpeg argument lists (pure, UI-agnostic).

Every function returns a plain list of strings — no subprocess, no Qt — so the
exact command for any job can be unit tested and reused behind other front-ends.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from clip_model import ClipInfo
from grade_manager import Grade


@dataclass
class MixSpec:
    """Describes the optional derived combined-mix track.

    kind: "lr" (camera→Left, WAV→Right; no summing/echo) or "5050" (summed mono).
    make_default: promote the mix to track 0 (else appended after the lossless mics).
    match_levels: balance loudness between the two mics.
    drift_ratio / polarity_inverted: from sync analysis, applied to the WAV side of
    the mix ONLY — never the lossless WAV track.
    """
    kind: str = "lr"
    make_default: bool = False
    match_levels: bool = False
    drift_ratio: float = 1.0
    polarity_inverted: bool = False


def hms_to_seconds(hms: str) -> float:
    """Parse HH:MM:SS(.mmm) / MM:SS / SS into seconds. Returns 0.0 on failure."""
    try:
        parts = [float(x) for x in hms.strip().split(":")]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        return float(parts[0])
    except Exception:
        return 0.0


# ── Audio track modes ─────────────────────────────────────────────────────────
#
#  "camera" → 2 tracks: [0] camera copy (default)  [1] WAV ALAC
#  "wav"    → 2 tracks: [0] WAV ALAC (default)      [1] camera copy
#  "mixed"  → 3 tracks: [0] amix AAC 50/50 (default) [1] camera copy [2] WAV ALAC
#
# When no WAV is paired, only camera audio is included regardless of mode.
# (Phase 2 will add the L/R split track and per-clip drift correction.)
# ─────────────────────────────────────────────────────────────────────────────

def _mix_filtergraph(mix: MixSpec) -> str:
    """Filter_complex string that turns camera [0:a:0] + WAV [1:a:0] into [mix]."""
    cam = "[0:a:0]"
    wav = "[1:a:0]"
    cam_pre = []
    wav_pre = []
    if mix.match_levels:
        cam_pre.append("dynaudnorm=f=200")
        wav_pre.append("dynaudnorm=f=200")
    if mix.polarity_inverted:
        wav_pre.append("volume=-1.0")
    if abs(mix.drift_ratio - 1.0) > 1e-6:
        # tempo-correct the WAV side to track the camera clock (pitch preserved)
        wav_pre.append(f"atempo={mix.drift_ratio:.6f}")
    cam_chain = cam + ",".join(cam_pre + ["aformat=channel_layouts=mono"]) + "[cam_m]"
    wav_chain = wav + ",".join(wav_pre + ["aformat=channel_layouts=mono"]) + "[wav_m]"
    if mix.kind == "5050":
        combine = "[cam_m][wav_m]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix]"
    else:  # "lr"
        combine = "[cam_m][wav_m]join=inputs=2:channel_layout=stereo:map=0.0-FL|1.0-FR[mix]"
    return ";".join([cam_chain, wav_chain, combine])


def _build_conform_with_mix(ff: str, clip: ClipInfo, out: Path, progress_file: Path,
                            track_order: str, mix: MixSpec) -> list:
    """Stream-copy mux that also carries the derived combined-mix track (AAC).

    Lossless tracks (camera copy + WAV ALAC) are preserved exactly; the mix is an
    additional encoded track, appended last unless `make_default`.
    """
    cmd = [ff, "-y", "-i", str(clip.path)] + clip.wav_flags()  # input 1 = WAV (offset)
    cmd += ["-filter_complex", _mix_filtergraph(mix), "-map", "0:v:0"]

    mix_title = ("Split Mix (L: Camera · R: WAV)" if mix.kind == "lr"
                 else "Combined Mix (Camera + WAV 50/50)")

    if track_order == "wav":
        lossless = [("1:a:0", "alac", "Backup WAV (Lossless)"),
                    ("0:a:0", "copy", "Camera Audio (Original)")]
    else:  # camera (default)
        lossless = [("0:a:0", "copy", "Camera Audio (Original)"),
                    ("1:a:0", "alac", "Camera Audio (Original)")]
        lossless[1] = ("1:a:0", "alac", "Backup WAV (Lossless)")

    # Track maps: two lossless first, then the mix.
    for src, _codec, _title in lossless:
        cmd += ["-map", src]
    cmd += ["-map", "[mix]"]

    cmd += ["-c:v", "copy"]
    cmd += ["-c:a:0", lossless[0][1]]
    cmd += ["-c:a:1", lossless[1][1]]
    cmd += ["-c:a:2", "aac", "-b:a:2", "256k"]

    default_idx = 2 if mix.make_default else 0
    for i in range(3):
        cmd += [f"-disposition:a:{i}", "default" if i == default_idx else "0"]
    cmd += ["-metadata:s:a:0", f"title={lossless[0][2]}"]
    cmd += ["-metadata:s:a:1", f"title={lossless[1][2]}"]
    cmd += ["-metadata:s:a:2", f"title={mix_title}"]

    cmd += ["-progress", str(progress_file), "-nostats", str(out)]
    return cmd


def build_mux_cmd(ff: str, clip: ClipInfo, out: Path, progress_file: Path,
                  track_order: str, square_mode: str,
                  mix: Optional[MixSpec] = None) -> list:
    """Build the ffmpeg command for one clip (stream-copy or transcode path).

    When `mix` is given and the clip both conforms and has a paired WAV, an extra
    combined-mix track (L/R or 50/50) is added per `mix`. Otherwise behaviour is
    unchanged from v1.2.
    """

    has_wav    = clip.has_wav()
    is_conform = clip.status == "ok"

    if mix is not None and has_wav and is_conform:
        return _build_conform_with_mix(ff, clip, out, progress_file, track_order, mix)

    if is_conform:
        wav_flags = clip.wav_flags() if has_wav else []
        cmd = [ff, "-y", "-i", str(clip.path)]
        if has_wav:
            cmd += wav_flags   # adds -ss/-itsoffset + -i wav

        if not has_wav:
            cmd += ["-map", "0:v:0", "-map", "0:a:0",
                    "-c:v", "copy", "-c:a:0", "copy"]

        elif track_order == "mixed":
            cmd += [
                "-filter_complex",
                "[0:a:0][1:a:0]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mixed]",
                "-map", "0:v:0",
                "-map", "[mixed]",   # track 0: combined
                "-map", "0:a:0",     # track 1: camera original
                "-map", "1:a:0",     # track 2: WAV for ALAC
                "-c:v", "copy",
                "-c:a:0", "aac", "-b:a:0", "256k",
                "-c:a:1", "copy",
                "-c:a:2", "alac",
                "-disposition:a:0", "default",
                "-disposition:a:1", "0",
                "-disposition:a:2", "0",
                "-metadata:s:a:0", "title=Mixed Audio (Camera + Backup 50/50)",
                "-metadata:s:a:1", "title=Camera Audio (Original)",
                "-metadata:s:a:2", "title=Backup WAV (Lossless)",
            ]

        elif track_order == "wav":
            cmd += [
                "-map", "0:v:0",
                "-map", "1:a:0",   # WAV first (default)
                "-map", "0:a:0",   # camera second
                "-c:v", "copy",
                "-c:a:0", "alac",
                "-c:a:1", "copy",
                "-disposition:a:0", "default",
                "-disposition:a:1", "0",
                "-metadata:s:a:0", "title=Backup WAV (Lossless)",
                "-metadata:s:a:1", "title=Camera Audio (Original)",
            ]

        else:  # "camera" (default)
            cmd += [
                "-map", "0:v:0",
                "-map", "0:a:0",   # camera first (default)
                "-map", "1:a:0",   # WAV second
                "-c:v", "copy",
                "-c:a:0", "copy",
                "-c:a:1", "alac",
                "-disposition:a:0", "default",
                "-disposition:a:1", "0",
                "-metadata:s:a:0", "title=Camera Audio (Original)",
                "-metadata:s:a:1", "title=Backup WAV (Lossless)",
            ]

        cmd += ["-progress", str(progress_file), "-nostats", str(out)]
        return cmd

    # ── Transcode path — conform to Luna Ultra spec ──────────────────────────
    conflicts  = set(clip.conflicts)
    need_scale = any(x for x in conflicts if "×" in x)
    need_fps   = any("fps" in x for x in conflicts)

    vf_parts = []
    if need_scale:
        if clip.stream and clip.stream.width == clip.stream.height:
            if square_mode == "crop":
                vf_parts.append("crop=ih*16/9:ih:(iw-ih*16/9)/2:0,scale=3840:2160:flags=lanczos")
            else:
                vf_parts.append("scale=3840:2160:force_original_aspect_ratio=decrease:flags=lanczos,pad=3840:2160:(ow-iw)/2:(oh-ih)/2")
        else:
            vf_parts.append("scale=3840:2160:flags=lanczos")
    if need_fps:
        vf_parts.append("fps=30000/1001")

    cmd = [ff, "-y", "-i", str(clip.path)]
    if vf_parts:
        cmd += ["-vf", ",".join(vf_parts)]
    cmd += [
        "-c:v", "libx265", "-crf", "18", "-preset", "medium",
        "-pix_fmt", "yuv420p10le", "-tag:v", "hvc1",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-c:a", "aac", "-b:a", "192k",
        "-progress", str(progress_file), "-nostats",
        str(out),
    ]
    return cmd


# ── Custom output track plan ──────────────────────────────────────────────────
#
#  An OutputPlan lets the user pick exactly which tracks the master carries and in
#  what order: the video, and any of camera / WAV / mix audio. The first enabled
#  audio track becomes the default. Camera audio is always stream-copied (lossless),
#  WAV → ALAC (lossless), mix → AAC. Mix is only available on conforming clips.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class OutputTrack:
    kind: str            # "camera" | "wav" | "mix"
    enabled: bool = True


@dataclass
class OutputPlan:
    include_video: bool = True
    tracks: list = None              # list[OutputTrack], audio in output order
    mix_kind: str = "lr"
    mix_match_levels: bool = False

    def __post_init__(self):
        if self.tracks is None:
            self.tracks = [OutputTrack("camera"), OutputTrack("wav"), OutputTrack("mix", enabled=False)]

    @staticmethod
    def preset(track_order: str, mix_enabled: bool, mix_kind: str,
               mix_make_default: bool, mix_match_levels: bool) -> "OutputPlan":
        """Build a plan from the simple presets (camera/wav + optional mix)."""
        if track_order == "wav":
            order = [OutputTrack("wav"), OutputTrack("camera")]
        else:
            order = [OutputTrack("camera"), OutputTrack("wav")]
        mix = OutputTrack("mix", enabled=mix_enabled)
        if mix_enabled and mix_make_default:
            order.insert(0, mix)
        else:
            order.append(mix)
        return OutputPlan(include_video=True, tracks=order,
                          mix_kind=mix_kind, mix_match_levels=mix_match_levels)


# A clip whose video is much longer than its (real-time) WAV is a slow-motion
# recording. Its camera audio is usually absent; we synthesise a primary track by
# time-stretching the WAV (pitch-preserved) to the video length.
SLOWMO_RATIO = 1.25


def is_slowmo(clip: ClipInfo) -> bool:
    return (clip.has_wav() and getattr(clip, "wav_duration", 0.0) > 0.0
            and clip.duration > clip.wav_duration * SLOWMO_RATIO)


def atempo_chain(factor: float) -> str:
    """ffmpeg atempo filter chain that scales playback speed by `factor`.

    atempo only accepts 0.5–2.0 per stage, so large ratios are split across
    several stages. factor < 1 slows down (stretches), > 1 speeds up; pitch is
    preserved either way.
    """
    if factor <= 0:
        return "atempo=1.0"
    stages = []
    f = factor
    while f < 0.5:
        stages.append(0.5)
        f /= 0.5
    while f > 2.0:
        stages.append(2.0)
        f /= 2.0
    stages.append(f)
    return ",".join(f"atempo={x:.6f}" for x in stages)


def transcode_vf_parts(clip: ClipInfo, square_mode: str) -> list:
    """Video filter parts needed to conform a non-matching clip (scale/fps)."""
    conflicts = set(clip.conflicts)
    need_scale = any("×" in x for x in conflicts)
    need_fps   = any("fps" in x for x in conflicts)
    parts = []
    if need_scale:
        if clip.stream and clip.stream.width == clip.stream.height:
            if square_mode == "crop":
                parts.append("crop=ih*16/9:ih:(iw-ih*16/9)/2:0,scale=3840:2160:flags=lanczos")
            else:
                parts.append("scale=3840:2160:force_original_aspect_ratio=decrease:flags=lanczos,pad=3840:2160:(ow-iw)/2:(oh-ih)/2")
        else:
            parts.append("scale=3840:2160:flags=lanczos")
    if need_fps:
        parts.append("fps=30000/1001")
    return parts


_TRACK_TITLES = {
    "camera": "Camera Audio (Original)",
    "wav":    "Backup WAV (Lossless)",
}


def _slot_fill(kind: str, clip: ClipInfo, mix: MixSpec) -> tuple:
    """Decide how a clip fills one audio slot. Returns (fill, codec, title).

    Every clip fills every enabled slot — with silence if it has no source — so
    that all per-clip temp files share an identical track layout and the final
    concat is consistent regardless of clip order. Slot codecs are fixed
    (camera→AAC, wav→ALAC, mix→AAC) so a stream copy concat stays valid.
    """
    is_conform = clip.status == "ok"
    has_wav    = clip.has_wav()
    has_cam    = clip.has_camera_audio()
    slowmo     = is_slowmo(clip)

    if kind == "camera":          # AAC "primary" slot
        if slowmo and has_wav:
            return ("stretch", "aac", "Synced Audio (WAV stretched to video)")
        if has_cam:
            t = "Camera Audio (Bluetooth mic)" if has_wav else "Camera Audio (On-board mic)"
            return ("copy", "aac", t)
        if has_wav:
            return ("wav_aac", "aac", "Primary Audio (from WAV)")
        return ("silence", "aac", "Silent (no audio source)")

    if kind == "wav":             # ALAC lossless backup slot
        if has_wav:
            return ("wav_alac", "alac", "Backup WAV (Lossless)")
        return ("silence", "alac", "Silent backup")

    # kind == "mix"               # AAC combined-mix slot
    if is_conform and has_cam and has_wav:
        t = ("Split Mix (L: Camera · R: WAV)" if mix.kind == "lr"
             else "Combined Mix (Camera + WAV 50/50)")
        return ("mix", "aac", t)
    return ("silence", "aac", "Silent mix")


def build_mux_cmd_plan(ff: str, clip: ClipInfo, out: Path, progress_file: Path,
                       plan: OutputPlan, square_mode: str,
                       mix: Optional[MixSpec] = None) -> list:
    """Build one clip's ffmpeg command from a custom OutputPlan.

    Produces a uniform audio-track layout: every enabled plan slot is emitted for
    every clip (silence-filled where a source is missing) so the per-clip temp
    files all share the same streams and the final concat is clean regardless of
    clip order. Slow-motion clips fill the primary slot with the pitch-corrected
    stretched WAV; clips with no camera audio fall back to the WAV (or silence).
    """
    is_conform = clip.status == "ok"
    has_wav    = clip.has_wav()
    slowmo     = is_slowmo(clip)
    dur        = clip.duration
    if mix is None:
        mix = MixSpec(kind=plan.mix_kind, match_levels=plan.mix_match_levels)

    fills = [(kind,) + _slot_fill(kind, clip, mix)
             for kind in (t.kind for t in plan.tracks if t.enabled)]

    # ── Inputs ────────────────────────────────────────────────────────────────
    cmd = [ff, "-y", "-i", str(clip.path)]                 # input 0 = clip
    next_idx = 1
    wav_idx = None
    if has_wav:
        cmd += (["-i", str(clip.wav_path)] if slowmo else clip.wav_flags())
        wav_idx = next_idx
        next_idx += 1
    silence_idx = None
    if any(f[1] == "silence" for f in fills):
        cmd += ["-f", "lavfi", "-t", f"{max(dur, 0.1):.3f}",
                "-i", "anullsrc=r=48000:cl=stereo"]
        silence_idx = next_idx
        next_idx += 1

    # ── Filtergraph (stretch / mix, plus video scale if it must share it) ─────
    vf_parts = [] if is_conform else transcode_vf_parts(clip, square_mode)
    has_fc_audio = any(f[1] in ("stretch", "mix") for f in fills)
    uses_fc_video = plan.include_video and not is_conform and vf_parts and has_fc_audio
    fc = []
    if uses_fc_video:
        fc.append(f"[0:v:0]{','.join(vf_parts)}[v]")
    if any(f[1] == "stretch" for f in fills):
        factor = (clip.wav_duration / dur) if dur > 0 else 1.0
        fc.append(f"[{wav_idx}:a:0]{atempo_chain(factor)}[s]")
    if any(f[1] == "mix" for f in fills):
        fc.append(_mix_filtergraph(mix))
    if fc:
        cmd += ["-filter_complex", ";".join(fc)]

    # ── Maps ──────────────────────────────────────────────────────────────────
    if plan.include_video:
        cmd += ["-map", "[v]" if uses_fc_video else "0:v:0"]
    for (kind, fill, codec, title) in fills:
        if fill == "copy":
            cmd += ["-map", "0:a:0"]
        elif fill in ("wav_alac", "wav_aac"):
            cmd += ["-map", f"{wav_idx}:a:0"]
        elif fill == "stretch":
            cmd += ["-map", "[s]"]
        elif fill == "mix":
            cmd += ["-map", "[mix]"]
        else:  # silence
            cmd += ["-map", f"{silence_idx}:a:0"]

    # ── Codecs ────────────────────────────────────────────────────────────────
    if plan.include_video:
        if is_conform:
            cmd += ["-c:v", "copy"]
        else:
            if not uses_fc_video and vf_parts:
                cmd += ["-vf", ",".join(vf_parts)]
            cmd += ["-c:v", "libx265", "-crf", "18", "-preset", "medium",
                    "-pix_fmt", "yuv420p10le", "-tag:v", "hvc1",
                    "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]
    for i, (kind, fill, codec, title) in enumerate(fills):
        if fill == "copy":
            cmd += [f"-c:a:{i}", "copy"]
        elif codec == "alac":
            cmd += [f"-c:a:{i}", "alac"]
        else:
            cmd += [f"-c:a:{i}", "aac", f"-b:a:{i}", "256k"]

    # ── Disposition + titles (first slot is the default track) ────────────────
    for i, (kind, fill, codec, title) in enumerate(fills):
        cmd += [f"-disposition:a:{i}", "default" if i == 0 else "0"]
        cmd += [f"-metadata:s:a:{i}", f"title={title}"]

    cmd += ["-progress", str(progress_file), "-nostats", str(out)]
    return cmd


def build_mix_sample_cmd(ff: str, clip: ClipInfo, mix: MixSpec,
                         out_path: str, seconds: float = 10.0) -> list:
    """Render a short audio-only sample of the combined mix for auditioning."""
    cmd = [ff, "-y", "-i", str(clip.path)] + clip.wav_flags()
    cmd += ["-filter_complex", _mix_filtergraph(mix),
            "-map", "[mix]", "-t", f"{seconds:.2f}",
            "-c:a", "aac", "-b:a", "256k", out_path]
    return cmd


def build_concat_cmd(ff: str, concat_file: Path, chapters_file: Path,
                     output: Path, progress_file: Path,
                     extra_out_args: Optional[list] = None) -> list:
    """Concatenate the per-clip temp files (stream copy) + attach chapters.

    `extra_out_args` (e.g. the archival manifest's `metadata_embed_args`) are
    inserted as output options just before the output filename — additive only,
    they don't touch the copied A/V streams.
    """
    cmd = [ff, "-y",
           "-f", "concat", "-safe", "0", "-i", str(concat_file),
           "-i", str(chapters_file),
           "-map_metadata", "1", "-map", "0", "-c", "copy",
           "-progress", str(progress_file), "-nostats"]
    if extra_out_args:
        cmd += list(extra_out_args)
    cmd += [str(output)]
    return cmd


# ── Archival master (Phase 2) — parallel original tracks for lossless recovery ──
# The baseline master (build_concat_cmd above) stays the default, playable track 1.
# For each spec group of NON-conforming clips we concat that group's ORIGINALS
# (stream copy, video + audio) into an intermediate, then mux the baseline plus all
# those intermediates into the final master. Proven in tools/spike_archival_p2.py.

def build_archival_concat_cmd(ff: str, concat_file: Path, output: Path) -> list:
    """Concat one spec-group's original clips (stream copy, ALL streams) into a
    single archival intermediate track-file. No chapters, no re-encode — the
    originals must share a spec (that's what the spec-group guarantees) so the
    concat demuxer stays valid."""
    return [ff, "-y", "-v", "error",
            "-f", "concat", "-safe", "0", "-i", str(concat_file),
            "-map", "0", "-c", "copy", str(output)]


def build_final_archival_mux_cmd(ff: str, baseline: Path, archival_files: list,
                                 output: Path, progress_file: Path,
                                 extra_out_args: Optional[list] = None) -> list:
    """Mux the baseline (input 0) + each archival intermediate into the final
    master, stream-copied. Baseline video stays default (track 1); archival
    videos are non-default so external tools ignore them. Archival audio is
    mapped optionally (`a?`) since a group's originals might be video-only.

    Stream order in the output: all baseline streams first (its video, then its
    audio tracks), then each archival track's video + audio — so archival audio
    lands after the baseline's own audio slots (see the Phase-2 spike)."""
    cmd = [ff, "-y", "-v", "error", "-i", str(baseline)]
    for f in archival_files:
        cmd += ["-i", str(f)]
    cmd += ["-map", "0"]                      # all baseline streams
    for i in range(1, len(archival_files) + 1):
        cmd += ["-map", f"{i}:v", "-map", f"{i}:a?"]
    cmd += ["-c", "copy", "-map_metadata", "0", "-map_chapters", "0",
            "-disposition:v:0", "default"]
    for vi in range(1, len(archival_files) + 1):
        cmd += [f"-disposition:v:{vi}", "0"]
    cmd += ["-progress", str(progress_file), "-nostats"]
    if extra_out_args:
        cmd += list(extra_out_args)
    cmd += [str(output)]
    return cmd


def build_whatsapp_cmd(ff: str, source: str, start: str, duration: str,
                       output: Path, grade: Optional[Grade],
                       progress_file: Path) -> list:
    """Trim + optional grade → 720p H.264 MP4 for sharing."""
    vf_chain = "scale=1280:720:flags=lanczos"
    if grade:
        vf_chain = grade.filter_chain() + ",scale=1280:720:flags=lanczos"
    return [ff, "-y",
            "-ss", start, "-i", source, "-t", duration,
            "-vf", vf_chain,
            "-c:v", "libx264", "-crf", "26", "-preset", "fast",
            "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-map", "0:v:0", "-map", "0:a:0",
            "-c:a", "aac", "-b:a", "128k", "-ac", "2",
            "-movflags", "+faststart",
            "-progress", str(progress_file), "-nostats",
            str(output)]


def build_preview_cmd(ff: str, source: str, timecode: str,
                      grade: Optional[Grade], out_path: str) -> list:
    """Single graded frame at a timecode → 854×480 JPEG for the before/after pane."""
    vf = "scale=854:480:flags=lanczos"
    if grade:
        vf = grade.filter_chain() + ",scale=854:480:flags=lanczos"
    return [ff, "-y", "-ss", timecode, "-i", source,
            "-frames:v", "1", "-q:v", "3", "-vf", vf, out_path]


def build_thumbnail_cmd(ff: str, source: str, ts: float,
                        grade: Optional[Grade], thumb_path: str) -> list:
    """Single frame at `ts` → 480px-wide JPEG for the live render preview."""
    vf = grade.filter_chain() if grade else ""
    vf_part = ["-vf", f"{vf},scale=480:-2" if vf else "scale=480:-2"]
    return [ff, "-y", "-ss", f"{ts:.3f}", "-i", source,
            "-frames:v", "1", "-q:v", "5"] + vf_part + [thumb_path]
