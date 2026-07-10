"""core/review_media.py — ffmpeg command builders for the Review tab.

Frame-exact extraction, full-resolution snapshots and the tick-set audio
mix all go through ffmpeg — never `QVideoFrame.toImage()`, which the v1.4
playback spike confirmed silently converts genuine 10-bit frames to 8-bit
RGB32. Pure command-builders only, no subprocess calls, matching
core/ffmpeg_cmd.py's split between building commands here and running them
in a Qt worker (src/review_workers.py).
"""

import hashlib
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


def build_thumbnail_strip_cmd(ff: str, path: str, secs: float, out_jpg: str,
                              width: int = 160) -> list:
    """A small JPEG at `secs`, for the Review tab's overview thumbnail
    filmstrip. Unlike `build_frame_extract_cmd`/`build_snapshot_cmd`, this
    favours SPEED over frame-exactness — a filmstrip tile is a rough visual
    marker, not a precision reading, and a strip needs many of these.

    `-skip_frame nokey` tells the decoder to skip straight to the nearest
    keyframe rather than decode every P-frame in between to reach the exact
    target — on 4K 10-bit HEVC this is the difference between ~0.5s and
    several (increasingly, the later in the file) seconds per tile, measured
    directly: a single-frame extract without it took 1.1s at 0.2s in, 5.2s at
    9.5s in on a 10s 4K/10-bit clip; with it, every position took ~0.5s flat.
    A tile can land up to one GOP away from the requested timestamp — a
    non-issue for a rough filmstrip preview.

    `format=yuvj420p` in the filter chain forces full-range YUV before the
    MJPEG encode: virtually all camera footage is standard "tv"/limited-range
    yuv420p, which ffmpeg's mjpeg encoder REJECTS outright ("Non full-range
    YUV is non-standard") unless told to relax strict compliance — without
    this, every real-camera clip silently produced zero thumbnail tiles
    (returncode -22, and `-v quiet` swallows the error text), while only the
    synthetic full-range test sources used in development happened to work."""
    return [ff, "-y", "-v", "quiet",
            "-ss", f"{max(0.0, secs):.3f}", "-skip_frame", "nokey", "-i", str(path),
            "-frames:v", "1", "-an", "-q:v", "6",
            "-vf", f"scale={width}:-2,format=yuvj420p",
            str(out_jpg)]


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


def build_proxy_cmd(ff: str, path: str, out_path: str, height: int = 480) -> list:
    """A small, hardware-decode-friendly proxy of the WHOLE master: plain
    8-bit H.264 (High profile, yuv420p) scaled down to `height`, "veryfast"
    preset — fast enough to build as a one-time background job per master,
    and light enough that any GPU (or even software) decodes it instantly,
    unlike the source's own resolution/codec/bit-depth. `scale=-2:'min(h,
    ih)'` never upsamples a source that's already smaller than `height`.

    `-map 0:v:0 -map 0:a` preserves the master's own audio-track ORDER
    (matching `probe.probe_audio_tracks`' `audio_index` numbering), so
    `PlaybackEngine.set_audio_single(track_idx)` picks the same track on the
    proxy as it would on the master. Audio is re-encoded to AAC (not
    copied) rather than preserving the source codec (which may be ALAC) —
    this is a scrub/playback convenience proxy, not an archival copy, and a
    single uniform codec keeps track switching predictable.
    """
    return [ff, "-y", "-v", "error", "-i", str(path),
            "-map", "0:v:0", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-vf", f"scale=-2:min({height}\\,ih)",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            str(out_path)]


def proxy_cache_path(cache_dir: Path, master_path: str, height: int = 480) -> Path:
    """Deterministic cache path for a master's fast-preview proxy, keyed on
    the resolved path + size + mtime + target height — so a file replaced
    at the same path (a re-merge, say) gets a fresh proxy instead of
    silently reusing a stale one, and switching the target height doesn't
    collide with a previous proxy at the same name."""
    p = Path(master_path)
    try:
        st = p.stat()
        sig = f"{st.st_size}_{int(st.st_mtime)}"
    except OSError:
        sig = "0_0"
    key = f"{p.resolve()}|{sig}|{height}"
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return Path(cache_dir) / f"{p.stem}_{h}_{height}p.mp4"
