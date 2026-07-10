"""core/extract.py — recover original clips from an archival master, driven
entirely by its manifest. Pure: a RecoveryPlan builder + ffmpeg command
builders, no subprocess calls — matching core/review_media.py's split between
building commands here and running them in a worker (extract_workers.py).

Recovery sourcing, per clip (see DEVELOPMENT.md's Phase-2 audio model):
  - VIDEO: from its `archival_track` at `in_track_start`/`in_track_duration`
    if it has one (an odd-spec original), else from the baseline's video
    stream (index 0) at its `baseline_chapter_index`'s computed offset (every
    clip — conforming or not — gets a baseline chapter; clips concatenate
    back-to-back with no gaps, so summing preceding clips' durations gives
    the exact start).
  - CAMERA AUDIO: from `archival_audio_stream` (the SAME archival track as its
    video, same window) when set, else from the baseline's own camera-audio
    track at the SAME chapter offset as the video (every clip's camera audio
    is stream-copied into the baseline uniformly, independent of whether its
    video conformed).
  - WAV BACKUP: always from the baseline's WAV (ALAC) track — WAV never rides
    an archival track, only the baseline carries it. Seeked by the clip's
    MEASURED concat position (`concat_start`/`wav_track_duration`, probed from
    the temp files at merge time) when the manifest carries it; older manifests
    fall back to the chapter offset model, which can drift when a clip's audio
    doesn't run exactly as long as its video.

A clip whose archival track is shared with other same-spec clips (a
multi-clip concat group) recovers content-complete but not bit-exact at the
cut boundary (AAC audio priming) — see DEVELOPMENT.md's "Phase 2 finding".
Input-side `-ss` (before `-i`) naturally snaps to the nearest keyframe, so
video is frame-exact regardless; only concat-boundary audio has this caveat.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.manifest import ClipEntry, Manifest


# Seek guard for MEASURED windows (seconds). A measured boundary is the concat
# demuxer's own arithmetic, but commands print timestamps at ms precision and
# frame pts are rational — a hair of rounding either way must never cost a
# frame. Decode paths seek EPS early (accurate seek keeps frames with pts ≥ the
# target, so the previous clip's last frame — a full frame-interval earlier —
# stays excluded); stream-copy paths seek EPS late (input seek snaps to the
# nearest keyframe AT OR BEFORE the target, so landing fractionally before this
# clip's own IDR would otherwise fall back a whole GOP into the previous clip).
SEEK_EPS = 0.002


@dataclass
class RecoveryPlan:
    """Everything needed to recover one clip: where its video/audio/WAV live
    in the master, and where they should land on disk."""
    entry: ClipEntry
    video_stream: int              # absolute master stream index
    video_start: float
    video_duration: float
    audio_stream: Optional[int]    # absolute master stream index, or None
    wav_stream: Optional[int]
    wav_start: float
    wav_duration: float
    bit_exact: bool                # False only for a multi-clip concat group
    video_measured: bool = False   # video window comes from measured concat positions
                                   # (ClipEntry.concat_start, Task 87) — command builders
                                   # apply the SEEK_EPS guards only then; a modelled
                                   # window keeps the exact historical commands
    wav_archival_stream: Optional[int] = None   # this clip's OWN preserved-in-full WAV
                                   # (ClipEntry.wav_archival_stream) — a standalone,
                                   # untrimmed, byte-exact stream, or None if not preserved
    lrv_video_archival_track: Optional[int] = None   # this clip's preserved LRV proxy's
    lrv_audio_archival_track: Optional[int] = None   # own video/audio streams, or None if not preserved


def compute_baseline_offsets(manifest: Manifest) -> dict:
    """{baseline_chapter_index: (start, duration)} — every clip (conforming or
    not) occupies a baseline chapter; clips concatenate back-to-back with no
    gaps, so summing preceding durations gives each chapter's exact start."""
    ordered = sorted(manifest.clips, key=lambda c: (c.baseline_chapter_index
                                                    if c.baseline_chapter_index is not None else 1 << 30))
    offsets = {}
    t = 0.0
    for c in ordered:
        if c.baseline_chapter_index is None:
            continue
        offsets[c.baseline_chapter_index] = (t, c.duration)
        t += c.duration
    return offsets


def build_recovery_plan(manifest: Manifest, entry: ClipEntry) -> Optional[RecoveryPlan]:
    """A RecoveryPlan for one manifest clip entry, or None if it can't be
    located (shouldn't happen for a manifest produced by this app)."""
    baseline_offsets = compute_baseline_offsets(manifest)
    camera_idx = manifest.baseline_audio_tracks.get("camera")
    wav_idx = manifest.baseline_audio_tracks.get("wav")

    video_measured = False
    if entry.archival_track is not None:
        video_stream = entry.archival_track
        video_start, video_duration = entry.in_track_start, entry.in_track_duration
        bit_exact = entry.in_track_start == 0.0   # a lone clip always starts its track at 0
        audio_stream = entry.archival_audio_stream
    else:
        if entry.baseline_chapter_index is None or entry.baseline_chapter_index not in baseline_offsets:
            return None
        video_stream = 0
        video_start, video_duration = baseline_offsets[entry.baseline_chapter_index]
        # Measured concat positions (Task 87, same field the WAV window uses):
        # cumulative video durations drift ±1 frame at clip boundaries (measured
        # directly with tools/diagnose_midtrack_decode.py — every "unexplained"
        # video verify mismatch on a real 8-clip master was exactly this), while
        # concat_start is the concat demuxer's own timestamp arithmetic. The
        # window LENGTH is the gap to the next clip's measured start; the last
        # clip (or a poisoned successor) keeps the modelled duration — an end
        # overshoot is harmless when nothing follows, and honest when unmeasured.
        if entry.concat_start is not None:
            video_start = entry.concat_start
            video_measured = True
            nxt = next((c for c in manifest.clips
                        if c.baseline_chapter_index == entry.baseline_chapter_index + 1), None)
            if nxt is not None and nxt.concat_start is not None:
                video_duration = nxt.concat_start - entry.concat_start
        bit_exact = True   # the baseline's own concat boundaries are keyframe-cut by construction
        audio_stream = camera_idx if (entry.has_camera_audio and camera_idx is not None) else None

    wav_start = wav_duration = 0.0
    wav_stream = None
    if entry.has_wav and wav_idx is not None and entry.baseline_chapter_index in baseline_offsets:
        wav_stream = wav_idx
        if entry.concat_start is not None and (entry.wav_track_duration or 0) > 0:
            # Measured truth (Task 85): the concat demuxer advances each segment
            # by the temp FILE's container duration, so the WAV segment starts at
            # the measured concat position and runs its own measured length —
            # the video-offset model below can drift whenever any audio stream
            # doesn't run exactly as long as its video (the verify log's WAV
            # position-drift finding).
            wav_start, wav_duration = entry.concat_start, entry.wav_track_duration
        else:
            # Older manifest (no measured positions): the historical model.
            wav_start, wav_duration = baseline_offsets[entry.baseline_chapter_index]

    return RecoveryPlan(
        entry=entry, video_stream=video_stream, video_start=video_start,
        video_duration=video_duration, audio_stream=audio_stream,
        wav_stream=wav_stream, wav_start=wav_start, wav_duration=wav_duration,
        bit_exact=bit_exact, video_measured=video_measured,
        wav_archival_stream=entry.wav_archival_stream,
        lrv_video_archival_track=entry.lrv_video_archival_track,
        lrv_audio_archival_track=entry.lrv_audio_archival_track,
    )


def build_preview_sample_cmd(ff: str, master_path: str, plan: RecoveryPlan,
                             start_ts: float, duration: float, out_path: str) -> list:
    """Short 160p-tall playable proxy for a clip still embedded in the master
    (not yet recovered to its own file) — same seek/map approach as
    `build_recover_clip_cmd` (input-side `-ss` snaps to the nearest keyframe;
    `-map 0:v:N` selects this clip's own video stream, whether that's the
    baseline or one of its archival tracks), but scaled down and re-encoded
    for a quick look rather than stream-copied for a lossless recovery —
    same "use only the resources the task needs" reasoning as the Merge tab's
    per-clip preview sample."""
    cmd = [ff, "-y", "-v", "error",
           "-ss", f"{max(0.0, start_ts):.3f}", "-i", str(master_path),
           "-t", f"{max(0.1, duration):.3f}",
           "-map", f"0:v:{plan.video_stream}", "-vf", "scale=-2:160",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "28"]
    if plan.audio_stream is not None:
        cmd += ["-map", f"0:a:{plan.audio_stream}", "-c:a", "aac", "-b:a", "96k"]
    cmd += ["-movflags", "+faststart", str(out_path)]
    return cmd


def build_recover_clip_cmd(ff: str, master_path: str, plan: RecoveryPlan, out_path: str,
                           include_audio: bool = True) -> list:
    """Stream-copy this clip's video (+ camera audio, if any) out to its
    original filename/container. Input-side -ss snaps to the nearest keyframe
    — frame-exact regardless of concat-boundary drift.

    video_stream/audio_stream are TYPE-relative indices ("the Nth video/audio
    stream"), matching how archival_track/archival_audio_stream/
    baseline_audio_tracks are populated by assign_archival_locations — so the
    map specifiers here must be "0:v:N"/"0:a:N", not a bare "0:N".

    `include_audio=False` skips mapping camera audio at all — used when the
    chosen output container can't hold this clip's camera-audio codec (see
    `is_mp4_compatible_audio`) and the caller is separating it out instead via
    `build_recover_camera_audio_cmd`.

    Deliberately does NOT re-embed metadata here — GPS/creation-time/device
    tags live at the whole-FILE level in MOV/MP4, not per-stream, so a clip
    sharing the master with others (or on its own archival track, which never
    carried the master's own container tags in the first place) needs those
    re-attached explicitly. See `recover_metadata_args`, applied by the caller
    from the manifest's own recorded values — confirmed as a real gap this
    way: a recovered clip's video/audio can be perfectly bit-exact while its
    GPS/creation-time is silently absent, since that metadata was never a
    property of the copied stream to begin with.

    A MEASURED window seeks SEEK_EPS late (see SEEK_EPS): copy-mode input seek
    snaps to the nearest keyframe at-or-before the target, and a measured
    boundary that rounds a hair below this clip's own IDR timestamp would
    otherwise snap a whole GOP back into the previous clip.
    """
    seek = plan.video_start + (SEEK_EPS if plan.video_measured else 0.0)
    cmd = [ff, "-y", "-v", "error",
           "-ss", f"{max(0.0, seek):.3f}", "-i", str(master_path),
           "-t", f"{max(0.01, plan.video_duration):.3f}",
           "-map", f"0:v:{plan.video_stream}"]
    if include_audio and plan.audio_stream is not None:
        cmd += ["-map", f"0:a:{plan.audio_stream}"]
    cmd += ["-c", "copy", str(out_path)]
    return cmd


def recover_metadata_args(entry: ClipEntry) -> list:
    """-metadata args re-attaching a recovered clip's own GPS/creation-time/
    device provenance from the manifest — insert these BEFORE the output path
    in any recovery command. Replays `entry.metadata_tags` VERBATIM (the exact
    keys the original camera wrote — com.android.*/com.apple.quicktime.*/
    plain, whatever they were) rather than a fixed set of renamed/generic
    keys, since guessing a single naming convention silently fails for any
    camera that doesn't use it (confirmed directly: an early version of this
    wrote a generic "model" tag while the original used "com.android.model",
    so the values never matched).

    Also adds `-movflags use_metadata_tags`: ffmpeg's MOV/MP4 muxer otherwise
    silently DROPS any metadata key outside its own built-in whitelist
    (creation_time/location/title/... are recognised and kept; a vendor key
    like "com.android.model" is discarded with no warning at all) — confirmed
    directly by writing one to a fresh file and finding it simply absent
    afterward. This flag tells the muxer to keep arbitrary tags instead of
    filtering them.

    `creation_time` is also written explicitly even if it's not in
    metadata_tags, since it's tracked as its own first-class ClipEntry field.
    An older manifest (pre-task-77) simply contributes nothing here —
    recovery still works, just without re-attached metadata."""
    args = []
    if entry.creation_time:
        args += ["-metadata", f"creation_time={entry.creation_time}"]
    for key, value in (entry.metadata_tags or {}).items():
        if key == "creation_time":
            continue   # already handled above
        args += ["-metadata", f"{key}={value}"]
    if args:
        args += ["-movflags", "use_metadata_tags"]
    return args


_BIT_DEPTH_PCM = {16: "pcm_s16le", 24: "pcm_s24le", 32: "pcm_s32le"}

# Camera-audio codecs MP4 can't hold natively (unlike MOV, which is far more
# permissive) — practically just the uncompressed PCM variants some action
# cameras/gimbals use.
_MP4_INCOMPATIBLE_AUDIO_CODECS = {
    "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_s16be", "pcm_s24be", "pcm_s32be",
    "pcm_f32le", "pcm_f64le", "adpcm_ima_qt",
}


def is_mp4_compatible_audio(codec: str) -> bool:
    return (codec or "").lower() not in _MP4_INCOMPATIBLE_AUDIO_CODECS


def build_recover_wav_cmd(ff: str, master_path: str, plan: RecoveryPlan, out_wav_path: str,
                          bit_depth: int = 24) -> list:
    """Decode this clip's WAV backup segment (lossless ALAC in the baseline)
    back to a standalone PCM .wav — decoding a lossless codec's own encode
    reproduces the exact original samples."""
    codec = _BIT_DEPTH_PCM.get(bit_depth, "pcm_s24le")
    return [ff, "-y", "-v", "error",
            "-ss", f"{max(0.0, plan.wav_start):.3f}", "-i", str(master_path),
            "-t", f"{max(0.01, plan.wav_duration):.3f}",
            "-map", f"0:a:{plan.wav_stream}", "-c:a", codec, str(out_wav_path)]


def build_recover_wav_archival_cmd(ff: str, master_path: str, plan: RecoveryPlan,
                                   out_wav_path: str) -> list:
    """Recover this clip's "preserve WAV in full" archival stream — a plain
    stream copy of its own standalone, untrimmed track (see
    core.ffmpeg_cmd.build_wav_archival_mux_cmd), byte-exact by construction
    since it was never re-encoded or aligned in the first place. Caller must
    check `plan.wav_archival_stream is not None` first — only set for a clip
    whose WAV-mismatch resolution ticked "Also preserve this WAV in full"."""
    return [ff, "-y", "-v", "error", "-i", str(master_path),
            "-map", f"0:a:{plan.wav_archival_stream}", "-c", "copy", str(out_wav_path)]


def build_recover_lrv_archival_cmd(ff: str, master_path: str, plan: RecoveryPlan,
                                   out_path: str) -> list:
    """Recover this clip's preserved LRV proxy — a plain stream copy of its
    own standalone video (+ audio, if it was preserved) track (see
    core.ffmpeg_cmd.build_lrv_archival_mux_cmd), byte-exact by construction.
    Caller must check `plan.lrv_video_archival_track is not None` first —
    only set for a clip whose per-clip video options ticked "Also preserve
    the LRV proxy on its own track"."""
    cmd = [ff, "-y", "-v", "error", "-i", str(master_path),
           "-map", f"0:v:{plan.lrv_video_archival_track}"]
    if plan.lrv_audio_archival_track is not None:
        cmd += ["-map", f"0:a:{plan.lrv_audio_archival_track}"]
    cmd += ["-c", "copy", str(out_path)]
    return cmd


def build_recover_camera_audio_cmd(ff: str, master_path: str, plan: RecoveryPlan, out_wav_path: str,
                                   bit_depth: int = 24) -> list:
    """Decode this clip's ORIGINAL camera audio (not the WAV backup) out to a
    standalone WAV file — used when the chosen output container (MP4) can't
    carry the camera audio's codec natively (see `is_mp4_compatible_audio`),
    same window as the video (`video_start`/`video_duration`, not the WAV
    backup's own offsets)."""
    codec = _BIT_DEPTH_PCM.get(bit_depth, "pcm_s24le")
    return [ff, "-y", "-v", "error",
            "-ss", f"{max(0.0, plan.video_start):.3f}", "-i", str(master_path),
            "-t", f"{max(0.01, plan.video_duration):.3f}",
            "-map", f"0:a:{plan.audio_stream}", "-c:a", codec, str(out_wav_path)]


def recovered_filenames(entry: ClipEntry, container: str = "native") -> tuple:
    """(video_filename, wav_filename_or_None) — the names to recover to.
    `container` is "native" (keep this clip's own original extension —
    today's default), or an explicit "mov"/"mp4" to re-container every
    recovered clip the same way regardless of its original format."""
    wav_name = None
    if entry.has_wav:
        wav_name = Path(entry.source_filename).stem + ".wav"
    stem = Path(entry.source_filename).stem
    if container == "native":
        ext = Path(entry.source_filename).suffix or ".mov"
    else:
        ext = f".{container}"
    return f"{stem}{ext}", wav_name


# ── No-manifest fallback: recover straight from a master's own chapters ──────
#
# Every master this app produces titles its chapters with the original clip's
# filename stem (see ffmpeg_runner.run()'s chapters_file writing) — so even
# without a manifest (an older master, or "Archival master" wasn't ticked),
# the chapter list alone is enough to recover each clip's time range AND its
# likely original filename/camera, at the cost of no archival-track/rotation/
# provenance awareness. A third-party chapter-marked MOV this app never
# produced still works, just with generic titles/camera grouping.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class GenericRecoveryPlan:
    """A best-effort recovery plan built from chapter markers alone (see
    `build_generic_recovery_plans`) — distinct from `RecoveryPlan`, which
    needs a real manifest entry. Video/audio are trimmed straight from the
    master's own first video/audio stream at the chapter's time range.

    Every field here is user-editable in the Extract tab's manual controls
    (no manifest means there's no ground truth to fall back to) — a plan can
    equally be produced by `build_generic_recovery_plans` from real chapter
    markers, hand-edited afterward to fix a wrong guess, or constructed from
    scratch by the user for a master with no chapters at all."""
    title: str            # chapter title — this app's own masters put the original
                          # clip's filename stem here; a third-party MOV's title
                          # (or "" if untitled) otherwise
    index: int
    start: float
    duration: float
    camera_id: str
    camera_label: str
    video_stream: int = 0
    audio_stream: Optional[int] = None
    wav_stream: Optional[int] = None    # a second audio track manually assigned the
                                        # "WAV backup" role — recovered as a standalone
                                        # WAV alongside the video (no manifest means
                                        # there's no automatic way to know this exists)
    rotation: Optional[int] = None      # manual rotation override in degrees (0/90/180/270),
                                        # or None to leave the stream's own rotation untouched


def build_generic_recovery_plans(chapters: list, audio_track_indices: list,
                                 camera_audio_index: Optional[int] = None,
                                 wav_audio_index: Optional[int] = None,
                                 video_stream_index: int = 0) -> list:
    """One GenericRecoveryPlan per chapter. Camera identity is guessed from
    each chapter's title via the same `camera_id.identify_camera` cascade
    used at merge time — since this app's own masters title every chapter
    with the original clip's filename, this recovers real camera grouping
    for free on any master this app produced, manifest or not.

    `camera_audio_index`/`wav_audio_index`/`video_stream_index` are the
    Extract tab's manual-mode overrides (audio-role table, video-stream
    picker); by default (all None/0) this assumes the master's audio track 0
    is camera audio and there's no WAV-backup track — this app's own
    convention when a baseline has camera+WAV — since no manifest means
    there's no reliable way to know which track is which otherwise, so that's
    the sensible default rather than a guarantee.
    """
    from camera_id import identify_camera
    audio_stream = camera_audio_index if camera_audio_index is not None else (
        audio_track_indices[0] if audio_track_indices else None)
    plans = []
    for i, ch in enumerate(chapters):
        title = ch.title or f"chapter_{i + 1:03d}"
        key, label = identify_camera("", title)
        plans.append(GenericRecoveryPlan(
            title=title, index=i, start=ch.start,
            duration=max(0.01, ch.end - ch.start),
            camera_id=key, camera_label=label,
            video_stream=video_stream_index, audio_stream=audio_stream,
            wav_stream=wav_audio_index,
        ))
    return plans


def _rotation_metadata_args(plan_video_stream: int, rotation: Optional[int]) -> list:
    """-metadata args forcing a video stream's rotation — ffmpeg's MOV/MP4
    muxer translates a `rotate` stream tag into the correct display-matrix
    side data automatically, so this works alongside a plain stream copy
    (no re-encode needed) to correct a foreign master's missing/wrong
    rotation flag. Returns [] when there's no override (None) — the
    stream's own existing rotation, whatever it is, is left untouched."""
    if rotation is None:
        return []
    return [f"-metadata:s:v:{plan_video_stream}", f"rotate={rotation}"]


def build_generic_recover_clip_cmd(ff: str, master_path: str, plan: GenericRecoveryPlan,
                                   out_path: str) -> list:
    """Stream-copy a chapter's time range out of the master's own baseline
    video (+ first audio track, if any) — the no-manifest fallback. Input-
    side -ss snaps to the nearest keyframe (frame-exact); a chapter that
    shares a concat-boundary cut with its neighbours may be a frame or two
    off exactly like the manifest-driven recovery's non-bit-exact case."""
    cmd = [ff, "-y", "-v", "error",
           "-ss", f"{max(0.0, plan.start):.3f}", "-i", str(master_path),
           "-t", f"{plan.duration:.3f}",
           "-map", f"0:v:{plan.video_stream}"]
    if plan.audio_stream is not None:
        cmd += ["-map", f"0:a:{plan.audio_stream}"]
    cmd += ["-c", "copy"]
    cmd += _rotation_metadata_args(plan.video_stream, plan.rotation)
    if plan.rotation is not None:
        cmd += ["-movflags", "use_metadata_tags"]
    cmd += [str(out_path)]
    return cmd


def build_generic_recover_wav_cmd(ff: str, master_path: str, plan: GenericRecoveryPlan,
                                  out_wav_path: str, bit_depth: int = 24) -> list:
    """Decode this plan's manually-assigned WAV-backup track (plan.wav_stream)
    to a standalone PCM .wav, over the same chapter time range as the video —
    the generic-path analogue of build_recover_wav_cmd. Caller must check
    `plan.wav_stream is not None` first."""
    codec = _BIT_DEPTH_PCM.get(bit_depth, "pcm_s24le")
    return [ff, "-y", "-v", "error",
            "-ss", f"{max(0.0, plan.start):.3f}", "-i", str(master_path),
            "-t", f"{plan.duration:.3f}",
            "-map", f"0:a:{plan.wav_stream}", "-c:a", codec, str(out_wav_path)]


def generic_recovered_filename(plan: GenericRecoveryPlan, container: str = "native") -> str:
    """Output filename for a generic (no-manifest) recovery — `plan.title` is
    either the recovered original filename stem (this app's own masters) or
    the positional "chapter_NNN" fallback already assigned by
    `build_generic_recovery_plans` for an untitled chapter. There's no real
    original container to preserve here (no manifest), so "native" just means
    the long-standing default of ".mov"."""
    real_container = "mov" if container == "native" else container
    return f"{plan.title}.{real_container}"
