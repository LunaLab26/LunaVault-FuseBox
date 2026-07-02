"""core/review_media.py — ffmpeg command builders for the Review tab.

Frame-exact extraction, full-resolution snapshots and the tick-set audio
mix all go through ffmpeg — never `QVideoFrame.toImage()`, which the v1.4
playback spike confirmed silently converts genuine 10-bit frames to 8-bit
RGB32. Pure command-builders only, no subprocess calls, matching
core/ffmpeg_cmd.py's split between building commands here and running them
in a Qt worker (src/review_workers.py).
"""

from pathlib import Path

# Seconds of coarse (keyframe-nearest) lead-in before the accurate seek that
# lands exactly on the requested timestamp — cheap and frame-exact without
# decoding from the start of the file.
_ACCURATE_SEEK_LEAD = 2.0


def _split_seek(secs: float) -> tuple:
    secs = max(0.0, secs)
    coarse = max(0.0, secs - _ACCURATE_SEEK_LEAD)
    return coarse, secs - coarse


def build_frame_extract_cmd(ff: str, path: str, secs: float,
                            width: int = 0, height: int = 0,
                            pix_fmt: str = "rgb48le") -> list:
    """Exact single-frame extraction as raw pixels to stdout.

    `pix_fmt=rgb48le` preserves full precision (10-bit content scaled into
    16-bit-per-channel) for the scopes panel; the caller knows the frame
    size (from probe) and can pass `width`/`height` to size the output, or
    read the raw stream and reshape from a known probe result.
    """
    coarse, fine = _split_seek(secs)
    cmd = [ff, "-v", "quiet",
           "-ss", f"{coarse:.3f}", "-i", str(path),
           "-ss", f"{fine:.3f}",
           "-frames:v", "1", "-an",
           "-pix_fmt", pix_fmt]
    if width and height:
        cmd += ["-s", f"{width}x{height}"]
    cmd += ["-f", "rawvideo", "pipe:1"]
    return cmd


def build_snapshot_cmd(ff: str, path: str, secs: float, out_png: str) -> list:
    """Full-resolution PNG at an exact timestamp, preserving 16-bit precision."""
    coarse, fine = _split_seek(secs)
    return [ff, "-y", "-v", "quiet",
            "-ss", f"{coarse:.3f}", "-i", str(path),
            "-ss", f"{fine:.3f}",
            "-frames:v", "1", "-an",
            "-pix_fmt", "rgb48be",
            str(out_png)]


def snapshot_filename(master_path: str, frame_idx: int) -> Path:
    """`<master-stem>_f012345.png` next to the master, suffixed on collision."""
    base = Path(master_path)
    stem = f"{base.stem}_f{frame_idx:06d}"
    candidate = base.with_name(f"{stem}.png")
    n = 1
    while candidate.exists():
        candidate = base.with_name(f"{stem}_{n}.png")
        n += 1
    return candidate


def mix_cache_key(track_indices) -> str:
    """Stable cache key for a tick-set, independent of selection order."""
    return "-".join(str(i) for i in sorted(set(track_indices)))


def build_review_mix_cmd(ff: str, path: str, track_indices, out_path: str) -> list:
    """Mix an arbitrary subset of the master's own audio tracks into one AAC file.

    `track_indices` are 0-based AUDIO-stream indices (ffmpeg's `0:a:N`),
    matching `probe.parse_audio_tracks`. A single index is copied straight
    through (still re-encoded to AAC, so the output format is uniform for
    1..N tracks) — the Review tab prefers native single-track switching for
    that case and only calls this for an actual mix.
    """
    idxs = sorted(set(track_indices))
    if not idxs:
        raise ValueError("track_indices must not be empty")
    cmd = [ff, "-y", "-v", "quiet", "-i", str(path)]
    if len(idxs) == 1:
        cmd += ["-map", f"0:a:{idxs[0]}", "-c:a", "aac", "-b:a", "256k", str(out_path)]
        return cmd
    labels_in = "".join(f"[0:a:{i}]" for i in idxs)
    filt = f"{labels_in}amix=inputs={len(idxs)}:duration=longest:normalize=0[mix]"
    cmd += ["-filter_complex", filt, "-map", "[mix]",
            "-c:a", "aac", "-b:a", "256k", str(out_path)]
    return cmd
