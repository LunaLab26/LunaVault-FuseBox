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

def _mix_filtergraph(mix: MixSpec, n_outputs: int = 1) -> str:
    """Filter_complex string that turns camera [0:a:0] + WAV [1:a:0] into [mix].

    `n_outputs` > 1 fans the SAME derived mix out into that many distinct,
    single-use `[mix0]`..`[mixN-1]` pads via `asplit` instead of the single
    `[mix]` pad. Needed because a clip's Primary-slot override and a
    separately-enabled Mixed Audio track can BOTH resolve to "mix" for the
    same clip — an ffmpeg filtergraph output label can only be consumed once
    (`-map` on it twice fails with "Output with label 'mix' does not exist...
    or was already used elsewhere" — a real crash found this way), so each
    consumer needs its own pad even though the content is identical.
    """
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
    combine_label = "mix" if n_outputs <= 1 else "mixsrc"
    if mix.kind == "5050":
        combine = f"[cam_m][wav_m]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[{combine_label}]"
    else:  # "lr"
        combine = f"[cam_m][wav_m]join=inputs=2:channel_layout=stereo:map=0.0-FL|1.0-FR[{combine_label}]"
    parts = [cam_chain, wav_chain, combine]
    if n_outputs > 1:
        outs = "".join(f"[mix{i}]" for i in range(n_outputs))
        parts.append(f"[{combine_label}]asplit={n_outputs}{outs}")
    return ";".join(parts)


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
                vf_parts.append(_square_crop_graph(3840, 2160))
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


@dataclass
class ConformSpec:
    """The baseline every non-conforming clip is transcoded to. Defaults to the
    app's original 4K/HEVC/10-bit target, so behaviour is unchanged until the
    merge passes a user-chosen baseline."""
    width: int = 3840
    height: int = 2160
    fps: str = "30000/1001"       # ffmpeg fps expression
    codec: str = "hevc"           # "hevc" | "h264"
    pix_fmt: str = "yuv420p10le"
    color_space: str = "bt709"
    # Matrix coefficients (-colorspace), transfer characteristics (-color_trc), and
    # primaries (-color_primaries) are three DISTINCT ffmpeg options that only
    # coincidentally share one identifier for bt709 — a BT.2020 clip commonly probes
    # as color_space="bt2020nc" (matrix) with color_primaries="bt2020" and
    # color_transfer="arib-std-b67"/"smpte2084" (HDR), and feeding "bt2020nc" into
    # -color_primaries/-color_trc makes libx265 reject the command outright. Empty
    # (unset) falls back to color_space in _video_encoder_args, preserving the old
    # bt709-everywhere behaviour for any caller that only sets color_space.
    color_transfer: str = ""
    color_primaries: str = ""
    fill: str = "black"           # aspect-mismatch pad fill: "black" | "blur"
    hw_encoder: str = "off"       # "off" | "auto" | "nvenc" | "qsv" | "amf" | "vaapi"
    hw_decode: str = "off"        # "off" | "auto" | "vaapi" — GPU-decode the source (VAAPI only
                                  # on Linux); independent of hw_encoder, so all four
                                  # decode×encode pipeline combinations are expressible
    quality: int = 18             # CRF (software) / equivalent quality knob (GPU) — see QUALITY_PRESETS


DEFAULT_CONFORM = ConformSpec()

# Named quality presets for "Optimize baseline for delivery" — CRF numbers differ
# between codecs because x265/HEVC needs a higher number than x264/H.264 for
# equivalent perceived quality (it's simply a more efficient codec at the same
# number). Values are content-adaptive quality targets, not exact file sizes.
QUALITY_PRESETS = {
    "archival": {
        "label": "Archival / Mezzanine",
        "description": "Visually lossless — best if you'll re-edit or re-export this "
                        "footage later and don't want to compound quality loss. Largest files.",
        "h264": 16, "hevc": 20,
    },
    "master": {
        "label": "Master Quality",
        "description": "Excellent quality with a real size saving over Archival. "
                        "A safe default if you're not sure.",
        "h264": 18, "hevc": 22,
    },
    "youtube": {
        "label": "YouTube / Streaming",
        "description": "Matches what YouTube's own re-compression already targets on "
                        "upload — no visible loss survives their processing anyway, so "
                        "this is effectively free size savings.",
        "h264": 22, "hevc": 26,
    },
    "social": {
        "label": "Social / Compact",
        "description": "Noticeably smaller, minor visible softening. Good for quick "
                        "shares or when storage is tight.",
        "h264": 26, "hevc": 30,
    },
}
DEFAULT_QUALITY_PRESET = "youtube"


def quality_for_preset(preset: str, codec: str) -> int:
    """Resolve a named preset to the actual CRF/quality number for `codec`
    ("hevc"/"h265" vs "h264"/"avc"). Falls back to the Master-Quality/CRF-18
    default for an unrecognised preset name."""
    entry = QUALITY_PRESETS.get(preset, QUALITY_PRESETS["master"])
    key = "hevc" if (codec or "").lower() in ("hevc", "h265") else "h264"
    return entry[key]


def _blur_pad_graph(w: int, h: int) -> str:
    """Filtergraph that fits the frame into w×h preserving aspect, filling the
    bars with a blurred, frame-filling copy of the image (nicer than black bars
    for vertical clips). Valid as a single -vf graph and inside filter_complex."""
    return (f"split=2[bg][fg];"
            f"[bg]scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={w}:{h},boxblur=20:1[bgb];"
            f"[fg]scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos[fgs];"
            f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2")


def _square_crop_graph(w: int, h: int) -> str:
    """Crop a SQUARE source (iw == ih — only ever called for one) to fill the
    w:h baseline aspect, then scale to w:h exactly. Direction depends on the
    target's own aspect, not just 16:9 — the baseline can be landscape
    (3840x2160), square (2160x2160), or portrait (1080x1920):
      - target aspect >= 1 (landscape or square): crop the source's HEIGHT
        down to iw*h/w, keep full width. (A square target makes this a
        harmless no-op crop, since h/w == 1.)
      - target aspect < 1 (portrait): crop the source's WIDTH down to
        ih*w/h, keep full height.
    Previously hardcoded to always crop WIDTH to a literal `ih*16/9` —
    mathematically impossible for a square source (16/9 > 1 means the
    requested crop width always exceeds the actual square width), which
    made ffmpeg reject the filter outright and fail the merge for every
    square-mode clip, regardless of which of these baselines was chosen."""
    if w >= h:
        return f"crop=iw:iw*{h}/{w}:0:(ih-iw*{h}/{w})/2,scale={w}:{h}:flags=lanczos"
    return f"crop=ih*{w}/{h}:ih:(iw-ih*{w}/{h})/2:0,scale={w}:{h}:flags=lanczos"


def _video_encoder_args(conform: "ConformSpec", ff: str = None) -> list:
    """Encoder args targeting the baseline codec/pixel-format/colour. Uses a
    GPU encoder (NVENC/QSV/AMF) when `conform.hw_encoder` requests one AND it
    actually probes as working on this machine; otherwise falls back to the
    software encoder unchanged from before this option existed."""
    cs = conform.color_space or "bt709"
    primaries = getattr(conform, "color_primaries", "") or cs
    trc = getattr(conform, "color_transfer", "") or cs
    codec = (conform.codec or "hevc").lower()

    quality = getattr(conform, "quality", 18) or 18
    hw_choice = getattr(conform, "hw_encoder", "off") or "off"
    if hw_choice != "off" and ff:
        from core.gpu_encode import detect_best_hw, hw_encode_plan
        vendor = hw_choice if hw_choice != "auto" else detect_best_hw(ff, codec)
        plan = hw_encode_plan(codec, vendor, conform.pix_fmt, quality)
        if plan:
            return plan["encoder_args"] + ["-colorspace", cs, "-color_primaries", primaries, "-color_trc", trc]

    args = ["-crf", str(quality), "-preset", "medium", "-pix_fmt", conform.pix_fmt]
    if codec in ("hevc", "h265"):
        return ["-c:v", "libx265"] + args + ["-tag:v", "hvc1",
                "-colorspace", cs, "-color_primaries", primaries, "-color_trc", trc]
    return ["-c:v", "libx264"] + args + [
        "-colorspace", cs, "-color_primaries", primaries, "-color_trc", trc]


def _resolve_hw_extras(conform: "ConformSpec", ff: str) -> "dict | None":
    """Resolve hardware DECODE and/or ENCODE offload for a transcode command,
    returning the pieces a caller must weave in that a plain trailing encoder-
    args list can't express:
      - ffmpeg_bin: the binary to actually run. VAAPI — decode OR encode —
        needs a real system ffmpeg; the bundled static build has no hardware
        acceleration at all (see core.gpu_encode's module docstring). None
        means the caller's own bundled ff is fine.
      - global_args: pre-input args — `-hwaccel vaapi -hwaccel_device <n>` for
        hardware decode, and/or `-vaapi_device <n>` for VAAPI encode.
      - filter_suffix: `format=<hwfmt>,hwupload` to append as the LAST video
        filter step when encoding on VAAPI (uploads the CPU-side frame to a GPU
        surface for the encoder); None otherwise.
      - encoder_args: the VAAPI encoder args, for callers that don't build
        their own via _video_encoder_args; None when encode isn't VAAPI.
    Returns None when neither decode nor encode needs any of this — pure
    software, or a GPU *encoder* like NVENC/QSV/AMF that just swaps -c:v on the
    bundled binary with no device/upload/hwaccel of its own."""
    if not ff:
        return None
    from core.gpu_encode import (detect_best_hw, hw_encode_plan, hw_pix_fmt,
                                 vaapi_decode_global_args, system_vaapi_ffmpeg,
                                 vaapi_render_device)
    codec = (conform.codec or "hevc").lower()
    quality = getattr(conform, "quality", 18) or 18

    # Encode: does it resolve to VAAPI specifically? NVENC/QSV/AMF are "hardware"
    # too but need none of this (they swap -c:v on the bundled binary), so they
    # don't count here — _video_encoder_args handles them directly.
    enc_choice = getattr(conform, "hw_encoder", "off") or "off"
    enc_plan = None
    if enc_choice != "off":
        vendor = enc_choice if enc_choice != "auto" else detect_best_hw(ff, codec)
        if vendor == "vaapi":
            enc_plan = hw_encode_plan(codec, "vaapi", conform.pix_fmt, quality)

    # Decode: hardware VAAPI requested and actually available on this machine?
    dec_choice = getattr(conform, "hw_decode", "off") or "off"
    dec_args = vaapi_decode_global_args() if dec_choice in ("auto", "vaapi") else None

    if enc_plan is None and dec_args is None:
        return None

    system_ff = system_vaapi_ffmpeg()
    device = vaapi_render_device()
    if system_ff is None or device is None:
        return None

    global_args = list(dec_args) if dec_args else []
    if enc_plan:
        global_args += ["-vaapi_device", device]
    return {
        "ffmpeg_bin": system_ff,
        "global_args": global_args,
        "filter_suffix": (f"format={hw_pix_fmt(conform.pix_fmt)},hwupload") if enc_plan else None,
        "encoder_args": enc_plan["encoder_args"] if enc_plan else None,
    }


def transcode_vf_parts(clip: ClipInfo, square_mode: str,
                       conform: ConformSpec = DEFAULT_CONFORM,
                       src_width: Optional[int] = None, src_height: Optional[int] = None) -> list:
    """Video filter parts to conform a non-matching clip to `conform` (aspect-
    preserving scale/pad + fps). Never stretches: odd aspects (incl. vertical
    clips, whose rotation ffmpeg auto-applies) are fitted and padded; only a
    square clip in 'crop' mode is cropped to 16:9.

    `src_width`/`src_height` override the clip's own probed dimensions —
    used when conforming a DIFFERENT source than the clip's own footage (the
    "use LRV proxy instead" per-clip override, ClipInfo.video_source_override):
    `clip.conflicts` was computed against the clip's own spec and says
    nothing about a proxy's, so scale/pad is applied unconditionally rather
    than gated on conflicts that don't describe this source. A harmless no-op
    when the override's dimensions already happen to match the baseline —
    ffmpeg's scale filter is a cheap pass-through then."""
    w, h = conform.width, conform.height
    st = clip.stream
    if src_width is not None or src_height is not None:
        sw = src_width if src_width is not None else (st.width if st else 0)
        sh = src_height if src_height is not None else (st.height if st else 0)
        parts = []
        if sw == sh and square_mode == "crop":
            parts.append(_square_crop_graph(w, h))
        elif conform.fill == "blur":
            parts.append(_blur_pad_graph(w, h))
        else:
            parts.append(f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
                         f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2")
        return parts

    conflicts = set(clip.conflicts)
    rotation = getattr(st, "rotation", 0) if st else 0
    # A 90/270 rotation swaps display dimensions on decode, so it needs fitting
    # even when the stored resolution already matches the baseline.
    need_scale = any("×" in x for x in conflicts) or rotation in (90, 270)
    need_fps = any("fps" in x for x in conflicts)
    parts = []
    if need_scale:
        if st and st.width == st.height and square_mode == "crop":
            parts.append(_square_crop_graph(w, h))
        elif conform.fill == "blur":
            parts.append(_blur_pad_graph(w, h))
        else:
            parts.append(f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
                         f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2")
    if need_fps:
        parts.append(f"fps={conform.fps}")
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
            t = "Camera Audio (AAC)" if has_wav else "Camera Audio (On-board mic)"
            return ("copy", "aac", t)
        if has_wav:
            return ("wav_aac", "aac", "Primary Audio (from WAV)")
        return ("silence", "aac", "Silent (no audio source)")

    if kind == "wav":             # ALAC lossless backup slot
        if has_wav:
            return ("wav_alac", "alac", "Backup WAV (Lossless)")
        if has_cam:
            # No WAV for this clip, but camera audio exists — without this,
            # the WAV slot (which the "primary" choice may point at as the
            # file-wide default track) falls silent for this clip even
            # though real audio is available on the camera slot. Mirrors the
            # wav_aac fallback above, just in the other direction.
            return ("cam_alac", "alac", "Backup Audio (from Camera)")
        return ("silence", "alac", "Silent backup")

    # kind == "mix"               # AAC combined-mix slot
    if is_conform and has_cam and has_wav:
        t = ("Split Mix (L: Camera · R: WAV)" if mix.kind == "lr"
             else "Combined Mix (Camera + WAV 50/50)")
        return ("mix", "aac", t)
    return ("silence", "aac", "Silent mix")


def _override_fill(target: str, slot_codec: str, clip: ClipInfo) -> Optional[tuple]:
    """Resolve (fill, codec, title) for a per-clip Primary override
    (ClipInfo.primary_override): force the clip's disposition-default slot —
    whose codec is fixed to `slot_codec` by the global Camera/WAV choice — to
    carry `target` ("camera"/"wav"/"mix") instead of its normal automatic
    source. Reuses the exact same fill vocabulary _slot_fill already returns
    for a slot of that native kind (camera-content-in-an-ALAC-slot is the
    same cam_alac fallback used when a clip has no WAV; wav-content-in-an-
    AAC-slot is the same wav_aac fallback used when a clip has no camera
    audio) — an override never invents an untested merge path, it just picks
    one on purpose instead of by availability.

    Returns None when the requested source isn't actually available on this
    clip (or, for "mix", when it can't be built) — the caller then falls
    back to the slot's normal automatic (Auto) behaviour rather than forcing
    something that would be silently wrong."""
    has_wav = clip.has_wav()
    has_cam = clip.has_camera_audio()
    if target == "camera":
        if not has_cam:
            return None
        if slot_codec == "aac":
            t = "Camera Audio (AAC)" if has_wav else "Camera Audio (On-board mic)"
            return ("copy", "aac", t)
        return ("cam_alac", "alac", "Backup Audio (from Camera)")
    if target == "wav":
        if not has_wav:
            return None
        if slot_codec == "alac":
            return ("wav_alac", "alac", "Backup WAV (Lossless)")
        return ("wav_aac", "aac", "Primary Audio (from WAV)")
    if target == "mix":
        if not (clip.status == "ok" and has_cam and has_wav):
            return None
        if slot_codec == "alac":
            return ("mix_alac", "alac", "Combined Mix (Camera + WAV, Lossless)")
        return ("mix", "aac", "Combined Mix (Camera + WAV 50/50)")
    return None


def build_mux_cmd_plan(ff: str, clip: ClipInfo, out: Path, progress_file: Path,
                       plan: OutputPlan, square_mode: str,
                       mix: Optional[MixSpec] = None,
                       conform: ConformSpec = DEFAULT_CONFORM) -> list:
    """Build one clip's ffmpeg command from a custom OutputPlan.

    Produces a uniform audio-track layout: every enabled plan slot is emitted for
    every clip (silence-filled where a source is missing) so the per-clip temp
    files all share the same streams and the final concat is clean regardless of
    clip order. Slow-motion clips fill the primary slot with the pitch-corrected
    stretched WAV; clips with no camera audio fall back to the WAV (or silence).
    """
    is_conform = clip.effective_status() == "ok"
    has_wav    = clip.has_wav()
    slowmo     = is_slowmo(clip)
    dur        = clip.duration
    if mix is None:
        mix = MixSpec(kind=plan.mix_kind, match_levels=plan.mix_match_levels)

    # Only a transcoding (non-conform) clip ever touches an encoder at all —
    # an "ok" clip stream-copies (-c:v copy below) regardless of hw_encoder.
    hw_extras = None if is_conform else _resolve_hw_extras(conform, ff)

    # "Use the LRV proxy instead" (per-clip override): conform the low-res
    # proxy into the baseline in place of this clip's own footage, on its own
    # input — camera AUDIO still comes from the clip's own file (input 0)
    # unaffected, since the proxy carries its own (unwanted) audio track too.
    # Only takes effect when this clip is actually transcoding (is_conform
    # False); Auto/matching-spec clips ignore it entirely.
    use_lrv = (not is_conform and clip.video_source_override == "lrv"
              and clip.has_lrv())

    # The disposition-default slot (index 0 — see the Disposition section below)
    # is the only one a per-clip Primary override can affect; every other slot
    # keeps its normal automatic fill regardless. Slow-motion clips are excluded
    # (guarding _override_fill's "copy" path would feed the un-stretched camera
    # audio against the pitch-corrected, time-stretched video — genuinely wrong
    # sync, not just a different choice), so they always use Auto behaviour.
    enabled_kinds = [t.kind for t in plan.tracks if t.enabled]
    override = getattr(clip, "primary_override", None)
    fills = []
    for i, kind in enumerate(enabled_kinds):
        overridden = None
        if i == 0 and override and override != "auto" and not slowmo:
            slot_codec = "alac" if kind == "wav" else "aac"
            overridden = _override_fill(override, slot_codec, clip)
        fills.append((kind,) + (overridden if overridden is not None else _slot_fill(kind, clip, mix)))

    # ── Inputs ────────────────────────────────────────────────────────────────
    # A VAAPI encode runs the WHOLE clip command through a different ffmpeg
    # binary (system, not the bundled static one — see _resolve_hw_extras)
    # with its device declared as a global arg before the first -i.
    cmd_ff = hw_extras["ffmpeg_bin"] if hw_extras else ff
    cmd = [cmd_ff, "-y"] + (hw_extras["global_args"] if hw_extras else []) + ["-i", str(clip.path)]
    next_idx = 1
    lrv_idx = None
    if use_lrv:
        cmd += ["-i", str(clip.lrv_path)]
        lrv_idx = next_idx
        next_idx += 1
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
    video_src_idx = lrv_idx if use_lrv else 0
    vf_parts = [] if is_conform else transcode_vf_parts(
        clip, square_mode, conform,
        src_width=(clip.lrv_width or None) if use_lrv else None,
        src_height=(clip.lrv_height or None) if use_lrv else None)
    if hw_extras and hw_extras.get("filter_suffix"):
        # VAAPI needs the upload onto its hw surface as the LAST step, after
        # every software scale/pad/crop step above has already run.
        vf_parts = vf_parts + [hw_extras["filter_suffix"]]
    # A clip's Primary-slot override and a separately-enabled Mixed Audio
    # track can both independently resolve to "mix"/"mix_alac" for the same
    # clip — each needs its OWN single-use filtergraph pad (see
    # _mix_filtergraph's docstring for the crash this avoids).
    mix_fill_count = sum(1 for f in fills if f[1] in ("mix", "mix_alac"))
    has_fc_audio = any(f[1] in ("stretch", "mix", "mix_alac") for f in fills)
    # A plain "-vf" shorthand implicitly picks its own input regardless of any
    # explicit -map, so once video comes from a NON-zero input (the LRV proxy)
    # it must always go through filter_complex (with its input spelled out
    # explicitly) — never the ambiguous shorthand, even without mix/stretch audio.
    uses_fc_video = plan.include_video and not is_conform and vf_parts and (has_fc_audio or use_lrv)
    fc = []
    if uses_fc_video:
        fc.append(f"[{video_src_idx}:v:0]{','.join(vf_parts)}[v]")
    if any(f[1] == "stretch" for f in fills):
        factor = (clip.wav_duration / dur) if dur > 0 else 1.0
        fc.append(f"[{wav_idx}:a:0]{atempo_chain(factor)}[s]")
    if mix_fill_count:
        fc.append(_mix_filtergraph(mix, n_outputs=mix_fill_count))
    if fc:
        cmd += ["-filter_complex", ";".join(fc)]

    # ── Maps ──────────────────────────────────────────────────────────────────
    if plan.include_video:
        cmd += ["-map", "[v]" if uses_fc_video else f"{video_src_idx}:v:0"]
    mix_slot_i = 0
    for (kind, fill, codec, title) in fills:
        if fill in ("copy", "cam_alac"):
            cmd += ["-map", "0:a:0"]
        elif fill in ("wav_alac", "wav_aac"):
            cmd += ["-map", f"{wav_idx}:a:0"]
        elif fill == "stretch":
            cmd += ["-map", "[s]"]
        elif fill in ("mix", "mix_alac"):
            cmd += ["-map", f"[mix{mix_slot_i}]" if mix_fill_count > 1 else "[mix]"]
            mix_slot_i += 1
        else:  # silence
            cmd += ["-map", f"{silence_idx}:a:0"]

    # A source clip carrying a QuickTime chapter track (`tref` type 'chap' on
    # its video/audio track, pointing at a small `text`-handler marker track —
    # confirmed directly on real camera footage) gets that track auto-carried
    # into the output by ffmpeg's MOV muxer REGARDLESS of the explicit -map
    # list above, since dropping a chapter-referenced track silently would
    # break chapter navigation for players that rely on it. That extra track
    # (surfacing as a stray `bin_data`/"text"-tagged stream in the delivered
    # master) isn't wanted here — this per-clip temp file gets its OWN
    # chapters written fresh at the final concat step (see build_concat_cmd),
    # so an inherited chapter track at this stage is never useful and no
    # `-map`/`-dn`/negative-map combination suppresses it; only this flag does.
    cmd += ["-map_chapters", "-1"]

    # ── Codecs ────────────────────────────────────────────────────────────────
    if plan.include_video:
        if is_conform:
            cmd += ["-c:v", "copy"]
        else:
            if not uses_fc_video and vf_parts:
                cmd += ["-vf", ",".join(vf_parts)]
            cmd += _video_encoder_args(conform, ff)
    for i, (kind, fill, codec, title) in enumerate(fills):
        if fill == "copy":
            cmd += [f"-c:a:{i}", "copy"]
        elif codec == "alac":
            # A fixed sample format is required here, not just the codec name.
            # Without it, ffmpeg's ALAC encoder auto-picks a bit depth from
            # whatever it's fed — a real WAV backup (often 24-in-32-bit) encodes
            # at one depth, while the SILENCE filler used for a clip with no WAV
            # (`anullsrc`, no format specified) defaults to 16-bit. Concatenating
            # ALAC segments that declare different bit depths corrupts the
            # stream at the seam (confirmed directly: decoding a real merge's
            # WAV-backup track threw hundreds of "invalid element channel
            # count"/"invalid zero block size" errors — traced to exactly this
            # 16-bit/24-bit mismatch — and forcing the same sample_fmt on every
            # ALAC segment, real or silent, made it decode clean). s32p is a
            # safe superset of any real source's precision.
            cmd += [f"-c:a:{i}", "alac", f"-sample_fmt:a:{i}", "s32p"]
        else:
            cmd += [f"-c:a:{i}", "aac", f"-b:a:{i}", "256k"]

    # ── Disposition + titles (first slot is the default track) ────────────────
    for i, (kind, fill, codec, title) in enumerate(fills):
        cmd += [f"-disposition:a:{i}", "default" if i == 0 else "0"]
        cmd += [f"-metadata:s:a:{i}", f"title={title}"]

    # Cut the OUTPUT to this clip's own true (video) duration — UNCONDITIONALLY,
    # not just for the LRV-proxy-swap path. Any audio input can legitimately run
    # longer than the video: a WAV recorder commonly keeps rolling a beat past
    # the camera stopping, and a clip-split WAV that still carries the NEXT
    # clip's audio can overrun by that clip's ENTIRE duration. Without this,
    # ffmpeg has no -shortest and no -t, so the per-clip temp file's container
    # duration follows the LONGEST stream, not the video — confirmed as a real,
    # high-impact bug this way: a real user's clip-split master had one clip's
    # segment run ~384s longer than its own video (its WAV backup was still the
    # camera's UN-split original, spanning both the clip and its split
    # successor), which the concat demuxer then advanced by, drifting every
    # later clip's presentation position by the overrun and leaving the video
    # decoder holding a frozen last frame for the difference — exactly the
    # "freeze frame" symptom reported. Previously this cutoff only existed for
    # the LRV-swap case (a smaller, more consistent instance of the same class
    # of bug — a proxy's own duration rarely matches its paired clip's to the
    # millisecond); reproduced directly and confirmed this is the general case,
    # not LRV-specific.
    cmd += ["-t", f"{max(0.01, dur):.3f}"]
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

    Maps VIDEO + AUDIO explicitly rather than a blanket `-map 0`: a camera
    clip's own footage can carry a `bin_data` metadata track (Pixel motion-
    photo / telemetry, codec tag "text"), which survives into the per-clip
    temp files even though those are built with explicit v/a maps — ffmpeg
    auto-carries that data track. A blanket `-map 0` then tries to copy it
    into the .mov output, which the muxer rejects outright ("Tag text
    incompatible with output codec id", header write fails, whole merge dies).
    Same lesson build_archival_concat_cmd already applies; `-map 0:a?` keeps
    every audio track (0/1/2…) while the `?` tolerates a video-only clip.
    """
    cmd = [ff, "-y",
           "-f", "concat", "-safe", "0", "-i", str(concat_file),
           "-i", str(chapters_file),
           "-map_metadata", "1", "-map", "0:v", "-map", "0:a?", "-c", "copy",
           "-progress", str(progress_file), "-nostats"]
    if extra_out_args:
        cmd += list(extra_out_args)
    cmd += [str(output)]
    return cmd


# ProRes profile numbers for prores_ks's -profile:v, keyed by the Compatible-
# playback-master UI's own quality names.
PRORES_PROFILES = {"proxy": 0, "standard": 2, "hq": 3}


def build_concat_reencode_cmd(ff: str, concat_file: Path, chapters_file: Path,
                              output: Path, progress_file: Path,
                              crf: int = 20, extra_out_args: Optional[list] = None,
                              codec: str = "h264", prores_profile: str = "hq",
                              hw_encoder: str = "off", hw_decode: str = "off") -> list:
    """Concatenate the per-clip temp files but RE-ENCODE the video into ONE clean,
    continuous, widely-compatible stream — the fix for the broken-splice playback
    a stream-copy concat produces.

    A stream-copy concat (build_concat_cmd) of independently-encoded H.264/HEVC
    segments keeps each segment's reference-frame structure but severs continuity
    at the joins: frames near a splice reference pictures (by Picture Order Count)
    from the other segment that the decoder can't resolve, so different players
    show green frames / freezes / digital static at the boundaries (confirmed
    directly on a real master — a mix of stream-copied and transcoded HEVC
    segments). Re-encoding the whole concatenation in a single pass hands the
    encoder one coherent GOP/reference structure end to end, so there are no
    splices left to break.

    `codec="h264"` (default): 8-bit H.264 (yuv420p, high profile) for the widest
    device/player support. `codec="prores"`: Apple ProRes (prores_ks) instead —
    an edit-friendly intermediate rather than a delivery format, useful when the
    footage itself is troublesome for H.264 (e.g. a 4K 10-bit HEVC source whose
    decode is heavy enough that an H.264 re-encode on a loaded machine drops/
    duplicates frames — ProRes's intra-only GOP is far more forgiving of that).
    `prores_profile` (only used when codec="prores") is one of PRORES_PROFILES'
    keys: "proxy" (smallest, offline-edit quality), "standard" (422), or "hq"
    (422 HQ, the default — matches this app's own one-off ProRes transcodes).

    Audio is stream-copied either way (concat-safe for playback); `+faststart`
    puts the moov atom up front for H.264 (harmless no-op for ProRes/.mov, kept
    for a uniform command shape). The baseline is the WATCHABLE copy — the
    lossless originals live in the archival tracks / kept clip files — so
    re-encoding it here costs nothing that matters.

    Validated on a real merge (task #13): the H.264 path's resulting baseline is
    one clean h264/yuv420p stream that decodes end-to-end with ZERO errors,
    versus the hundreds of broken-reference errors a stream-copy concat of the
    same clips produced.

    `hw_encoder` ("off"/"auto"/vendor name) offloads this pass's re-encode to
    a GPU encoder the same way core.gpu_encode covers everywhere else —
    "off" by default so no existing caller's behaviour changes. Only ever
    applies to codec="h264" (ProRes is a software-only edit intermediate;
    there's no such thing as a hardware ProRes encoder to offload to). This
    is a SINGLE continuous re-encode of the whole master, not short per-clip
    segments, so it's usually the single most expensive step in a
    "Compatible playback master" merge — worth accelerating even though the
    per-clip conform path already is.
    """
    hw_plan = None
    if codec != "prores" and hw_encoder and hw_encoder != "off":
        from core.gpu_encode import detect_best_hw, hw_encode_plan
        vendor = hw_encoder if hw_encoder != "auto" else detect_best_hw(ff, "h264")
        hw_plan = hw_encode_plan("h264", vendor, "yuv420p", crf)

    # Hardware decode is independent of the encoder choice (works with ProRes and
    # software H.264 too), but — like VAAPI encode — needs the system ffmpeg, so
    # a decode-only request still forces cmd_ff to that binary.
    dec_args = None
    if hw_decode and hw_decode != "off":
        from core.gpu_encode import vaapi_decode_global_args, system_vaapi_ffmpeg
        dec_args = vaapi_decode_global_args()
        if dec_args and hw_plan is None and system_vaapi_ffmpeg() is None:
            dec_args = None   # can't hardware-decode without a VAAPI-capable ffmpeg

    if hw_plan:
        # hw_encode_plan()'s own contract: "ffmpeg_bin": None means "the
        # caller's own bundled ffmpeg is fine" (true for NVENC/QSV/AMF, which
        # swap the trailing -c:v on the SAME binary — only VAAPI needs a
        # different one). `hw_plan` itself is a truthy dict either way, so
        # using it as the binary directly (dropped here) put a literal
        # `None` in cmd[0] for every non-VAAPI vendor — Windows' only real
        # hardware encoders. subprocess.Popen can't stringify that, so this
        # crashed EVERY "Compatible playback master" + GPU-encode merge on
        # Windows outright (confirmed directly: cmd[0] was None). VAAPI
        # never hit it since its ffmpeg_bin is always a real path, which is
        # why this survived two rounds of Linux-only battle testing.
        cmd_ff = hw_plan["ffmpeg_bin"] or ff
    elif dec_args:
        from core.gpu_encode import system_vaapi_ffmpeg
        cmd_ff = system_vaapi_ffmpeg() or ff
    else:
        cmd_ff = ff
    pre_args = list(dec_args) if dec_args else []
    if hw_plan:
        pre_args += hw_plan["global_args"]
        # The concat demuxer's segments are NOT parameter-uniform: a
        # stream-copied ("ok"-conform) clip keeps its camera's exact decoded
        # parameters (e.g. yuvj420p FULL range), while a transcoded clip's
        # segment carries the encoder's (limited range). At the first seam
        # where they differ, ffmpeg reinitialises the filter graph — and a
        # graph containing hwupload cannot be reinitialised: the whole encode
        # dies with -38/ENOSYS ("Function not implemented") the moment the
        # second segment starts. Confirmed directly on real mixed footage;
        # never triggered by parameter-uniform segments, which is why a
        # single-clip merge or an all-transcode merge worked. This is also
        # why this exact combination (H.264 compat master + VAAPI encode)
        # was the ONE cell family that "timed out" in battle-test rounds 1-2
        # — a fast failure, not a slow encode; VAAPI here actually runs
        # ~1.7x realtime vs software's ~0.6x once it survives the seam.
        # `-reinit_filter 0` (input option, scoped to the concat input) keeps
        # the ORIGINAL graph and instead converts mismatched frames to the
        # graph's negotiated input format. The scale=out_range prefix below
        # pins what that negotiation produces.
        pre_args += ["-reinit_filter", "0"]
    cmd = [cmd_ff, "-y"] + pre_args + [
           "-f", "concat", "-safe", "0", "-i", str(concat_file),
           "-i", str(chapters_file),
           # VIDEO + AUDIO only, never a blanket `-map 0`: the concatenated temp
           # clips can carry a camera `bin_data` metadata track (Pixel motion-
           # photo / telemetry, codec tag "text") that ffmpeg auto-attached; the
           # .mov muxer rejects it ("Tag text incompatible with output codec id
           # '98314'", header write fails), which killed the whole merge — in
           # BOTH software and hardware encode. See build_concat_cmd / the
           # archival concat, which map v+a explicitly for the same reason.
           "-map_metadata", "1", "-map", "0:v", "-map", "0:a?"]
    if codec == "prores":
        profile_num = PRORES_PROFILES.get(prores_profile, PRORES_PROFILES["hq"])
        cmd += ["-c:v", "prores_ks", "-profile:v", str(profile_num),
               "-vendor", "apl0", "-pix_fmt", "yuv422p10le"]
    elif hw_plan:
        if hw_plan.get("filter_suffix"):
            # scale=out_range=tv BEFORE the hwupload suffix: with
            # -reinit_filter 0 (above), every segment's frames are converted
            # to whatever format the graph negotiated on the FIRST segment —
            # left implicit, a full-range first segment makes the whole
            # output full-range (a real ~5-point luma shift against the
            # software path, measured directly) inside a tv-tagged stream.
            # Forcing limited range in the chain pins the negotiation to the
            # same range the software path produces, segment order be damned;
            # -color_range tv below tags the output to match. Verified: luma
            # stats agree with the libx264 reference to within 0.05 at both
            # segment halves of a mixed-range concat.
            cmd += ["-vf", "scale=out_range=tv," + hw_plan["filter_suffix"]]
        cmd += hw_plan["encoder_args"] + ["-color_range", "tv"]
    else:
        cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
               "-pix_fmt", "yuv420p", "-profile:v", "high"]
    cmd += ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
           "-c:a", "copy",
           "-movflags", "+faststart",
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
    """Concat one spec-group's original clips (stream copy, video + optional
    audio) into a single archival intermediate track-file. No chapters, no
    re-encode — the originals must share a spec (that's what the spec-group
    guarantees) so the concat demuxer stays valid.

    Maps only `v:0`/`a:0` rather than a blanket `-map 0`: real camera files can
    carry extra streams `-map 0` would blindly pull in — e.g. Google Pixel
    phones embed a `mett` (motion-photo/telemetry) data track as stream #0:2,
    which the MOV muxer refuses to stream-copy ("Cannot map stream #0:2 -
    unsupported type"). We only want the archived video/audio anyway.

    `-map_chapters -1`: a source clip whose video/audio track carries a
    `tref` of type 'chap' (a QuickTime chapter/marker track — confirmed
    directly on real camera footage, distinct from the Pixel `mett` case
    above) gets that chapter track auto-carried by the MOV muxer regardless
    of the explicit maps above, surfacing as a stray `bin_data`/"text"
    stream in this archival intermediate — and then it self-propagates
    below in `build_final_archival_mux_cmd`, which maps this file's own
    `v`/`a` streams straight into the final master. Excluding chapters here,
    where the archival originals are actually read, is what stops it.
    """
    return [ff, "-y", "-v", "error",
            "-f", "concat", "-safe", "0", "-i", str(concat_file),
            "-map", "0:v:0", "-map", "0:a:0?", "-map_chapters", "-1", "-c", "copy", str(output)]


def build_final_archival_mux_cmd(ff: str, baseline: Path, archival_files: list,
                                 output: Path, progress_file: Path,
                                 extra_out_args: Optional[list] = None,
                                 base_has_video: bool = True) -> list:
    """Mux the baseline (input 0) + each archival intermediate into the final
    master, stream-copied. Baseline video stays default (track 1); archival
    videos are non-default so external tools ignore them. Archival audio is
    mapped optionally (`a?`) since a group's originals might be video-only.

    Stream order in the output: all baseline streams first (its video, then its
    audio tracks), then each archival track's video + audio — so archival audio
    lands after the baseline's own audio slots (see the Phase-2 spike).

    `base_has_video` must match the OutputPlan the baseline was built from
    (False for an Advanced-output "no video" export) — it decides both the
    video map and which output stream index gets the "default" disposition,
    since every video-stream index shifts down by one when the baseline
    contributes none."""
    cmd = [ff, "-y", "-v", "error", "-i", str(baseline)]
    for f in archival_files:
        cmd += ["-i", str(f)]
    # Explicit video+audio, NOT a blanket "-map 0": the baseline was built with
    # chapters, and ffmpeg's MOV muxer represents those internally as a hidden
    # "chapter text" data stream (the classic QuickTime chapter-track
    # mechanism) — copying THAT pre-existing stream via -c copy into a new
    # file that also carries chapters (and other video tracks) hits a codec
    # tag/id conflict ("Tag text incompatible with output codec id ..."), a
    # real failure found on the user's 9-clip multicam merge. Chapters survive
    # anyway via -map_chapters below (metadata-level, independent of this
    # stream) — the muxer freshly (and safely) regenerates its own chapter
    # track for THIS output rather than copying the conflicting one.
    # "0:a?" (optional), not "0:a": a baseline with every audio track disabled
    # in the OutputPlan has zero audio streams, and ffmpeg hard-errors on a
    # non-optional map matching nothing ("Stream map '' matches no streams").
    # "0:v?" (optional) for the same reason: an Advanced-output "no video"
    # export's baseline has zero video streams — confirmed directly as a real
    # crash ("Stream map '' matches no streams" on 0:v) when Archival master
    # was also on for an audio-only export.
    cmd += ["-map", "0:v?" if not base_has_video else "0:v", "-map", "0:a?"]
    for i in range(1, len(archival_files) + 1):
        cmd += ["-map", f"{i}:v", "-map", f"{i}:a?"]
    cmd += ["-c", "copy", "-map_metadata", "0", "-map_chapters", "0"]
    # Output video-stream indices shift down by one whenever the baseline
    # contributes no video (0:v? matched nothing) — the first archival file's
    # video lands at v:0 instead of v:1, and so on. base_has_video picks the
    # right "default" slot instead of assuming the baseline always owns v:0.
    n_archival_video = len(archival_files)
    if base_has_video:
        cmd += ["-disposition:v:0", "default"]
        for vi in range(1, 1 + n_archival_video):
            cmd += [f"-disposition:v:{vi}", "0"]
    elif n_archival_video:
        cmd += ["-disposition:v:0", "default"]
        for vi in range(1, n_archival_video):
            cmd += [f"-disposition:v:{vi}", "0"]
    cmd += ["-progress", str(progress_file), "-nostats"]
    if extra_out_args:
        cmd += list(extra_out_args)
    cmd += [str(output)]
    return cmd


def build_wav_archival_mux_cmd(ff: str, master: Path, wav_files: list, existing_audio_count: int,
                               output: Path, progress_file: Path,
                               extra_out_args: Optional[list] = None) -> list:
    """Append each requested clip's untouched original WAV as one more
    standalone, non-default audio track onto an already-finished master — the
    "preserve this WAV in full" opt-in's audio analogue of
    build_final_archival_mux_cmd's video archival tracks, but simpler: a WAV
    file has no keyframe/GOP concerns, no spec-grouping, and no concat demuxer
    involved, so each preserved WAV is muxed in directly as its own extra
    stream. `-c copy` on the new streams keeps them byte-exact (MOV supports
    linear PCM natively, so the original codec never needs to change) —
    matching the archival system's own "never transcode what's meant to be
    recoverable" philosophy.

    Stream order: every existing master stream first (unchanged), then one
    more audio stream per entry in `wav_files`, in order — so a WAV's OUTPUT
    audio-stream index is `existing_audio_count + its position in wav_files`.
    `existing_audio_count` (the master's own audio-stream count before this
    pass) must come from the caller — it varies with the OutputPlan and
    whether video archival tracks were added first, so it can't be assumed
    here — and is exactly what lets the new streams be explicitly marked
    non-default without disturbing the master's own.

    Explicit video+audio, NOT a blanket "-map 0": a master built with
    chapters carries a hidden internal "chapter text" data stream (the
    classic QuickTime chapter-track mechanism) — copying THAT via -c copy
    into a new file that also carries chapters hits a codec tag/id conflict
    ("Tag text incompatible with output codec id ..."), a real failure found
    on a real merge whose master had "preserve WAV/LRV in full" enabled.
    Chapters survive anyway via -map_chapters below (metadata-level,
    independent of this stream) — the muxer regenerates its own chapter
    track for THIS output rather than copying the conflicting one. "0:a?"
    (optional) since a video-only master (no audio tracks at all) must not
    hard-error on a non-optional map matching nothing."""
    cmd = [ff, "-y", "-v", "error", "-i", str(master)]
    for f in wav_files:
        cmd += ["-i", str(f)]
    cmd += ["-map", "0:v", "-map", "0:a?"]
    for i in range(1, len(wav_files) + 1):
        cmd += ["-map", f"{i}:a:0"]
    cmd += ["-c", "copy", "-map_metadata", "0", "-map_chapters", "0"]
    for wi in range(len(wav_files)):
        cmd += [f"-disposition:a:{existing_audio_count + wi}", "0"]
    cmd += ["-progress", str(progress_file), "-nostats"]
    if extra_out_args:
        cmd += list(extra_out_args)
    cmd += [str(output)]
    return cmd


def build_lrv_archival_mux_cmd(ff: str, master: Path, lrv_files: list,
                               existing_video_count: int, existing_audio_count: int,
                               output: Path, progress_file: Path,
                               extra_out_args: Optional[list] = None) -> list:
    """Append each requested clip's low-res proxy (video + its own audio) as
    standalone, non-default tracks onto an already-finished master — the
    "preserve this LRV proxy on its own track" opt-in. `-c copy` keeps it
    byte-exact (no re-encode); mirrors build_wav_archival_mux_cmd's pattern
    (existing stream counts supplied by the caller, since they vary with
    whatever else — video archival, preserved WAVs — already ran first) but
    for a source that carries BOTH stream types, so both video and audio
    counts/dispositions are tracked independently.

    A proxy's audio track is optional (`?`) since some LRV variants are
    video-only — the per-file audio disposition index assumes every file in
    `lrv_files` uniformly does or doesn't carry audio (true for a same-camera
    shoot's own proxies); a mixed batch would drift the disposition index for
    files after the first audio-less one, a cosmetic (non-default flag only)
    rather than correctness issue.

    Explicit video+audio for the MASTER too, NOT a blanket "-map 0" — same
    hidden chapter-text-stream conflict build_wav_archival_mux_cmd's docstring
    describes (confirmed directly: this exact command failed a real merge
    with "Tag text incompatible with output codec id" before this fix)."""
    cmd = [ff, "-y", "-v", "error", "-i", str(master)]
    for f in lrv_files:
        cmd += ["-i", str(f)]
    cmd += ["-map", "0:v", "-map", "0:a?"]
    for i in range(1, len(lrv_files) + 1):
        cmd += ["-map", f"{i}:v?", "-map", f"{i}:a?"]
    cmd += ["-c", "copy", "-map_metadata", "0", "-map_chapters", "0"]
    for li in range(len(lrv_files)):
        cmd += [f"-disposition:v:{existing_video_count + li}", "0"]
        cmd += [f"-disposition:a:{existing_audio_count + li}", "0"]
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
                      grade: Optional[Grade], out_path: str, video_track: int = 0) -> list:
    """Single graded frame at a timecode → 854×480 JPEG for the before/after
    pane (and, via HybridPlaybackEngine, every tick of the software-decode
    playback "slideshow"). `-skip_frame nokey` jumps straight to the nearest
    keyframe instead of decoding every intervening frame — measured directly
    on a real 4K 10-bit HEVC clip: 1.9s-7.4s per frame (worse deeper into the
    file) without it, a flat ~0.7s with it. This is the same fix
    build_thumbnail_strip_cmd already uses for the overview filmstrip; a
    preview frame is a rough visual reference, not a precision reading, so
    landing up to one GOP away from the exact timestamp is the right trade
    for turning "worse than the slideshow's own 300ms tick" into something
    that can actually keep up.

    `video_track` (0-based, ffmpeg's `-map 0:v:N`) picks which VIDEO STREAM
    to grab from — always the baseline (0) except when HybridPlaybackEngine
    is showing one of a master's archival tracks (the Review tab's "view the
    individual clip originals" feature). Explicit even for 0: ffmpeg's
    default "best stream" pick with no -map isn't guaranteed to be the
    baseline once a master has more than one video stream."""
    vf = "scale=854:480:flags=lanczos"
    if grade:
        vf = grade.filter_chain() + ",scale=854:480:flags=lanczos"
    return [ff, "-y", "-ss", timecode, "-skip_frame", "nokey", "-i", source,
            "-map", f"0:v:{video_track}",
            "-frames:v", "1", "-q:v", "3", "-vf", vf, out_path]


def _preview_video_encoder_args(gpu_vendor: Optional[str], fast: bool) -> list:
    """Video-encode args for a preview sample. Software libx264 by default;
    `gpu_vendor` ("nvenc"/"qsv"/"amf") swaps in that GPU encoder tuned for speed
    (a throwaway 160p proxy, so latency matters far more than quality)."""
    if gpu_vendor:
        from core import gpu_encode
        enc = gpu_encode.encoder_name("h264", gpu_vendor)
        args = ["-c:v", enc]
        if gpu_vendor == "nvenc":
            args += ["-preset", "p1"]          # p1 = fastest
        elif gpu_vendor == "qsv":
            args += ["-preset", "veryfast"]
        elif gpu_vendor == "amf":
            args += ["-quality", "speed"]
        return args + ["-pix_fmt", "nv12"]
    return ["-c:v", "libx264", "-preset", "ultrafast" if fast else "veryfast", "-crf", "28"]


def build_clip_sample_cmd(ff: str, source: str, start_ts: float, duration: float,
                          out_path: str, *, hw_decode: bool = False,
                          gpu_vendor: Optional[str] = None, fast: bool = False,
                          height: int = 160) -> list:
    """Short playable proxy (default 160p tall) starting at `start_ts`, for a
    clip-table preview button. Deliberately transcodes down to a tiny file rather
    than asking the player to decode+scale the real source — same "use only the
    resources the task actually needs" reasoning as the thumbnail/preview frame
    extraction above, just for a moving sample instead of a still.

    The keyword options are driven by the hidden Developer panel and are all
    optional/experimental (each independently switchable, defaults off):
      hw_decode  — prepend `-hwaccel auto` so the GPU decodes the source.
      gpu_vendor — encode the proxy with that vendor's GPU encoder, not libx264.
      fast       — use libx264's ultrafast preset (ignored when gpu_vendor set).
      height     — proxy scale height in px (taller = clearer but slower)."""
    decode = ["-hwaccel", "auto"] if hw_decode else []
    venc = _preview_video_encoder_args(gpu_vendor, fast)
    return [ff, "-y", *decode, "-ss", f"{start_ts:.3f}", "-i", source, "-t", f"{duration:.3f}",
            "-vf", f"scale=-2:{int(height)}", *venc,
            "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", out_path]


def build_thumbnail_cmd(ff: str, source: str, ts: float,
                        grade: Optional[Grade], thumb_path: str) -> list:
    """Single frame at `ts` → 480px-wide JPEG for the live render preview
    during a merge — same `-skip_frame nokey` rationale as build_preview_cmd."""
    vf = grade.filter_chain() if grade else ""
    vf_part = ["-vf", f"{vf},scale=480:-2" if vf else "scale=480:-2"]
    return [ff, "-y", "-ss", f"{ts:.3f}", "-skip_frame", "nokey", "-i", source,
            "-frames:v", "1", "-q:v", "5"] + vf_part + [thumb_path]
