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
  - WAV BACKUP: always from the baseline's WAV (ALAC) track at the chapter
    offset — WAV never rides an archival track, only the baseline carries it.

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
        bit_exact = True   # the baseline's own concat boundaries are keyframe-cut by construction
        audio_stream = camera_idx if (entry.has_camera_audio and camera_idx is not None) else None

    wav_start = wav_duration = 0.0
    wav_stream = None
    if entry.has_wav and wav_idx is not None and entry.baseline_chapter_index in baseline_offsets:
        wav_stream = wav_idx
        wav_start, wav_duration = baseline_offsets[entry.baseline_chapter_index]

    return RecoveryPlan(
        entry=entry, video_stream=video_stream, video_start=video_start,
        video_duration=video_duration, audio_stream=audio_stream,
        wav_stream=wav_stream, wav_start=wav_start, wav_duration=wav_duration,
        bit_exact=bit_exact,
    )


def build_recover_clip_cmd(ff: str, master_path: str, plan: RecoveryPlan, out_path: str) -> list:
    """Stream-copy this clip's video (+ camera audio, if any) out to its
    original filename/container. Input-side -ss snaps to the nearest keyframe
    — frame-exact regardless of concat-boundary drift.

    video_stream/audio_stream are TYPE-relative indices ("the Nth video/audio
    stream"), matching how archival_track/archival_audio_stream/
    baseline_audio_tracks are populated by assign_archival_locations — so the
    map specifiers here must be "0:v:N"/"0:a:N", not a bare "0:N".
    """
    cmd = [ff, "-y", "-v", "error",
           "-ss", f"{max(0.0, plan.video_start):.3f}", "-i", str(master_path),
           "-t", f"{max(0.01, plan.video_duration):.3f}",
           "-map", f"0:v:{plan.video_stream}"]
    if plan.audio_stream is not None:
        cmd += ["-map", f"0:a:{plan.audio_stream}"]
    cmd += ["-c", "copy", str(out_path)]
    return cmd


_BIT_DEPTH_PCM = {16: "pcm_s16le", 24: "pcm_s24le", 32: "pcm_s32le"}


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


def recovered_filenames(entry: ClipEntry) -> tuple:
    """(video_filename, wav_filename_or_None) — the original names to recover to."""
    wav_name = None
    if entry.has_wav:
        wav_name = Path(entry.source_filename).stem + ".wav"
    return entry.source_filename, wav_name
