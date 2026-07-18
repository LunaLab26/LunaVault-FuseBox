"""core/verify.py — MD5 verification that a recovered clip's raw compressed
video/audio (and WAV backup) genuinely match the original camera file, byte
for byte — proving recovery is real, not just recorded as bit_exact=True.

Deliberately hashes RAW ELEMENTARY STREAMS (video) and DECODED PCM (audio),
never whole-file bytes: a genuinely bit-exact recovered clip can still land
in a slightly different container (different moov atom, duration rounding,
metadata tags) than the original file, which changes its whole-file MD5 even
though the actual samples are identical — confirmed directly while
investigating the rotation-loss bug (DEVELOPMENT.md, task 74/75). Pure
command builders + a result record here; core.extract's RecoveryPlan is
reused so verification and real recovery never diverge.
"""

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from probe import KEY_METADATA_TAGS
from core import seam_diag

_ISO6709_RE = re.compile(r"^([+-]\d+\.?\d*)([+-]\d+\.?\d*)")


def tags_equal(key: str, a: str, b: str) -> bool:
    """True if two metadata tag values should count as the same, tolerating
    ffmpeg's own re-formatting where it applies. Confirmed directly: writing
    back a GPS tag through ffmpeg's muxer can zero-pad the longitude's
    integer part (e.g. "-3.3728/" becomes "-003.3728/") — same coordinate,
    different string. Also confirmed directly: the mov muxer can duplicate a
    value into "X;X" when the same tag (e.g. creation_time) gets set through
    two of its own internal metadata paths that happen to agree — same
    information, just repeated, not a real difference. Either would
    otherwise look like a false mismatch."""
    if a == b:
        return True
    b_parts = [p.strip() for p in (b or "").split(";")]
    if len(b_parts) > 1 and all(p == a for p in b_parts):
        return True
    if key in ("location", "location-eng"):
        ma, mb = _ISO6709_RE.match(a or ""), _ISO6709_RE.match(b or "")
        if ma and mb:
            try:
                return (round(float(ma.group(1)), 4) == round(float(mb.group(1)), 4)
                        and round(float(ma.group(2)), 4) == round(float(mb.group(2)), 4))
            except ValueError:
                pass
    return False


def probe_rotation(ffprobe_bin: str, path: str, video_stream_index: int = 0, **kwargs) -> int:
    """The clip's rotation in degrees (0 if none/unreadable) — reads the
    Display Matrix side-data directly via ffprobe, no extraction needed."""
    try:
        r = subprocess.run(
            [ffprobe_bin, "-v", "error", "-select_streams", f"v:{video_stream_index}",
             "-show_entries", "stream_side_data", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30, **kwargs)
        data = json.loads(r.stdout or "{}")
        for stream in data.get("streams", []):
            for sd in stream.get("side_data_list", []):
                if "rotation" in sd:
                    return int(sd["rotation"]) % 360
    except Exception:
        pass
    return 0


def clip_has_audio_priming_gap(ffprobe_bin: str, path: str, audio_stream_index: int = 0,
                               **kwargs) -> bool:
    """Does this clip's OWN audio stream carry an edit-list-driven encoder
    priming/discard packet at the very start — a negative-PTS packet flagged
    'D' (discard), standard for AAC-LC encoder lookahead delay that a
    correctly-written `elst` box tells a normal player to skip? Confirmed
    directly (battle-test round 2) as the root cause of an "unexpected...
    nothing to explain it" camera-audio MD5 mismatch for ANY clip recovered
    via a concat-based cut (the shared baseline, or even a lone clip's own
    segment of it) rather than a genuinely standalone archival copy: the
    concat/cut process doesn't preserve this discard marking, so the
    recovered audio keeps the priming samples un-discarded — a constant
    ~1-AAC-frame (~21-23ms) time shift throughout the whole clip, which is
    why the existing ~300ms boundary guard never resolves it (it isn't a
    boundary artifact). Reproduced on both a synthetic ffmpeg-encoded clip
    and real camera footage (a source with 43 consecutive discard packets,
    ~917ms) — this is a real, standard MP4 muxing convention, not a fixture
    quirk. Only the first couple of packets need checking; the discard
    region is always right at the start."""
    try:
        r = subprocess.run(
            [ffprobe_bin, "-v", "error", "-select_streams", f"a:{audio_stream_index}",
             "-show_entries", "packet=pts_time,flags", "-read_intervals", "%+#3",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30, **kwargs)
        packets = json.loads(r.stdout or "{}").get("packets", [])
        return any(
            "d" in (p.get("flags") or "").lower() and float(p.get("pts_time", 0) or 0) < 0
            for p in packets)
    except Exception:
        return False


def probe_video_codec(ffprobe_bin: str, path: str, video_stream_index: int = 0, **kwargs) -> str:
    """The ACTUAL codec of a given video stream, read directly rather than
    assumed — needed because a transcoded clip's compressed bitstream in the
    master is whatever the baseline's own target codec is, not the original
    clip's codec, and picking the wrong one crashes the annexb bitstream
    filter used for elementary-stream extraction (confirmed directly: an
    h264 clip re-encoded into an HEVC baseline errored out trying to
    initialize h264_mp4toannexb on HEVC data)."""
    try:
        r = subprocess.run(
            [ffprobe_bin, "-v", "error", "-select_streams", f"v:{video_stream_index}",
             "-show_entries", "stream=codec_name", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30, **kwargs)
        streams = json.loads(r.stdout or "{}").get("streams", [])
        return (streams[0].get("codec_name") or "") if streams else ""
    except Exception:
        return ""


def probe_keyframe_times(ffprobe_bin: str, path: str, video_stream_index: int = 0,
                         **kwargs) -> list:
    """Sorted list of the video stream's keyframe presentation timestamps (s).
    Used at BUILD time to pin a concatenated archival track's real per-clip
    boundaries to actual keyframes, instead of trusting a cumulative sum of
    probed container durations — that sum drifts (container duration != the
    exact frame-duration sum), and since recovery seeks with `-ss` before
    `-i` (which snaps to the nearest keyframe at or before the timestamp), a
    drifted offset snaps to the WRONG clip's boundary for clips deep in a
    multi-clip track. Confirmed directly: a 3-clip shared archival track
    recovered only its middle clip byte-exact; first and last drifted."""
    try:
        r = subprocess.run(
            [ffprobe_bin, "-v", "error", "-select_streams", f"v:{video_stream_index}",
             "-skip_frame", "nokey", "-show_entries", "frame=best_effort_timestamp_time,pts_time",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=300, **kwargs)
        frames = json.loads(r.stdout or "{}").get("frames", [])
        times = []
        for fr in frames:
            ts = fr.get("best_effort_timestamp_time")
            if ts is None:
                ts = fr.get("pts_time")
            try:
                times.append(float(ts))
            except (TypeError, ValueError):
                continue
        return sorted(times)
    except Exception:
        return []


def probe_video_stream_duration(ffprobe_bin: str, path: str, video_stream_index: int = 0,
                                **kwargs) -> float:
    """The video stream's own duration in seconds (0.0 if unreadable) — more
    precise than the container/format duration for estimating where each clip
    lands inside a concatenated archival track (see probe_keyframe_times)."""
    try:
        r = subprocess.run(
            [ffprobe_bin, "-v", "error", "-select_streams", f"v:{video_stream_index}",
             "-show_entries", "stream=duration", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30, **kwargs)
        streams = json.loads(r.stdout or "{}").get("streams", [])
        if streams and streams[0].get("duration") not in (None, "N/A"):
            return float(streams[0]["duration"])
    except Exception:
        pass
    return 0.0


def probe_audio_stream_count(ffprobe_bin: str, path: str, **kwargs) -> int:
    """How many audio streams a file already has — needed before appending
    more (build_wav_archival_mux_cmd's "preserve WAV in full" pass) so the
    new streams' OUTPUT indices, and their explicit non-default disposition,
    are computed from what's actually there rather than assumed from the
    OutputPlan (which doesn't account for any video-archival pass that may
    have already run first)."""
    try:
        r = subprocess.run(
            [ffprobe_bin, "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30, **kwargs)
        return len(json.loads(r.stdout or "{}").get("streams", []))
    except Exception:
        return 0


def probe_video_stream_count(ffprobe_bin: str, path: str, **kwargs) -> int:
    """How many video streams a file already has — the video analogue of
    probe_audio_stream_count, needed before appending more (an LRV proxy
    preserved on its own track) so the new streams' OUTPUT indices, and their
    explicit non-default disposition, are computed from what's actually
    there."""
    try:
        r = subprocess.run(
            [ffprobe_bin, "-v", "error", "-select_streams", "v",
             "-show_entries", "stream=index", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30, **kwargs)
        return len(json.loads(r.stdout or "{}").get("streams", []))
    except Exception:
        return 0


def probe_key_tags(ffprobe_bin: str, path: str, **kwargs) -> dict:
    """Format-level metadata tags worth comparing (GPS/location, creation
    time, device make/model) — whatever's actually present on this file."""
    try:
        r = subprocess.run(
            [ffprobe_bin, "-v", "error", "-show_entries", "format_tags", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30, **kwargs)
        tags = json.loads(r.stdout or "{}").get("format", {}).get("tags", {}) or {}
        return {k: v for k, v in tags.items() if k in KEY_METADATA_TAGS}
    except Exception:
        return {}


def build_video_es_cmd(ff: str, source: str, out_path: str, codec: str,
                       seek: Optional[float] = None, duration: Optional[float] = None,
                       video_stream: int = 0) -> list:
    """Raw compressed video elementary stream, no container — the only video
    comparison immune to remux-level metadata differences. Omit seek/duration
    to read a whole standalone file; pass both to cut a window out of a
    shared master (matching a RecoveryPlan's video_start/video_duration)."""
    bsf = "hevc_mp4toannexb" if (codec or "").lower() in ("hevc", "h265") else "h264_mp4toannexb"
    fmt = "hevc" if (codec or "").lower() in ("hevc", "h265") else "h264"
    cmd = [ff, "-y", "-v", "error"]
    if seek is not None:
        cmd += ["-ss", f"{max(0.0, seek):.3f}"]
    cmd += ["-i", str(source)]
    if duration is not None:
        cmd += ["-t", f"{max(0.01, duration):.3f}"]
    cmd += ["-map", f"0:v:{video_stream}", "-c", "copy", "-bsf:v", bsf, "-f", fmt, str(out_path)]
    return cmd


def build_audio_pcm_cmd(ff: str, source: str, out_path: str,
                        seek: Optional[float] = None, duration: Optional[float] = None,
                        audio_stream: int = 0) -> list:
    """Decode audio to one fixed, HEADERLESS PCM spec (raw s16le, no WAV
    container) so two sides encoded with different original codecs/containers
    are always compared on equal footing. Deliberately not `-f wav`: ffmpeg
    writes a LIST/INFO metadata chunk into WAV output (e.g. an ISFT/INAM tag)
    that can differ between two otherwise-identical extractions and would
    silently corrupt the hash comparison — confirmed directly while building
    this feature (identical raw PCM, different whole-file MD5 due to nothing
    but that chunk)."""
    cmd = [ff, "-y", "-v", "error"]
    if seek is not None:
        cmd += ["-ss", f"{max(0.0, seek):.3f}"]
    cmd += ["-i", str(source)]
    if duration is not None:
        cmd += ["-t", f"{max(0.01, duration):.3f}"]
    cmd += ["-map", f"0:a:{audio_stream}", "-c:a", "pcm_s16le", "-ar", "48000", "-f", "s16le", str(out_path)]
    return cmd


def build_decoded_video_md5_cmd(ff: str, source: str, video_stream: int = 0,
                                seek: Optional[float] = None, duration: Optional[float] = None) -> list:
    """MD5 of the DECODED video frames (canonical yuv420p rawvideo), via
    ffmpeg's own `md5` muxer so nothing huge is written to disk. This is the
    decode-lossless fallback: a clip recovered from a CONCATENATED track (a
    shared archival track, or the baseline itself) is decode-identical to its
    original — same pixels — but NOT byte-identical, because the concat demuxer
    strips SEI/AUD metadata NAL units that carry no picture data (confirmed
    directly: raw-ES differed by ~15 bytes/frame while the decoded frames
    hashed identically). Comparing decoded frames proves the actual footage
    survived even when the raw bitstream isn't a byte-for-byte copy."""
    cmd = [ff, "-v", "error"]
    if seek is not None:
        cmd += ["-ss", f"{max(0.0, seek):.3f}"]
    cmd += ["-i", str(source)]
    if duration is not None:
        cmd += ["-t", f"{max(0.01, duration):.3f}"]
    cmd += ["-map", f"0:v:{video_stream}", "-pix_fmt", "yuv420p", "-c:v", "rawvideo", "-f", "md5", "-"]
    return cmd


def build_decoded_audio_md5_cmd(ff: str, source: str, audio_stream: int = 0,
                                seek: Optional[float] = None, duration: Optional[float] = None) -> list:
    """MD5 of the DECODED audio (s16le@48k) via ffmpeg's `md5` muxer — the
    audio analogue of build_decoded_video_md5_cmd. Used to prove a recovered
    clip's audio SAMPLES match even when the raw compressed bytes don't; and,
    with a small `seek` guard, to isolate the one genuine sample difference a
    concatenated track introduces — a NON-FIRST clip is decoded after an `-ss`
    seek into the middle of the AAC stream, so only its priming samples at the
    very start are wrong (confirmed directly: skipping the first ~0.2s made the
    decoded PCM byte-identical again)."""
    cmd = [ff, "-v", "error"]
    if seek is not None:
        cmd += ["-ss", f"{max(0.0, seek):.3f}"]
    cmd += ["-i", str(source)]
    if duration is not None:
        cmd += ["-t", f"{max(0.01, duration):.3f}"]
    cmd += ["-map", f"0:a:{audio_stream}", "-c:a", "pcm_s16le", "-ar", "48000", "-f", "md5", "-"]
    return cmd


def decoded_md5(cmd, **kwargs) -> str:
    """Run a build_decoded_*_md5_cmd and return its MD5 hex (or "" on failure).
    ffmpeg's md5 muxer prints a single `MD5=<hex>` line to stdout."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, **kwargs)
        for line in (r.stdout or "").splitlines():
            if line.startswith("MD5="):
                return line[4:].strip()
    except Exception:
        pass
    return ""


def md5_of_file(path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class StreamCheck:
    label: str            # "Video", "Camera audio", "WAV backup", "Rotation", "Metadata"
    source_md5: str = ""
    recovered_md5: str = ""
    match: bool = False
    skipped_reason: str = ""   # non-empty if this stream wasn't present to check
    diagnosis: str = ""        # set on a real mismatch OR an auto-corrected retry — the
                               # WHY, not just the pass/fail (root cause or what was healed)


@dataclass
class ClipVerifyResult:
    name: str
    checks: list = field(default_factory=list)   # list[StreamCheck]
    error: str = ""

    @property
    def passed(self) -> bool:
        if self.error:
            return False
        real_checks = [c for c in self.checks if not c.skipped_reason]
        return bool(real_checks) and all(c.match for c in real_checks)


_PREDICTED_PREFIX = "predicted unverifiable"


def predict_unverifiable(entry, plan, own_archival_track: bool,
                         safe_to_read_unbounded: bool,
                         source_has_audio_priming_gap: bool = False) -> dict:
    """Which MD5 checks are known, ahead of any extraction, to be unable to
    produce a meaningful pass — from the SAME facts ffmpeg_runner.py's
    reactive diagnosis already uses when a comparison mismatches, just
    consulted BEFORE spending a full extraction+hash pass (source AND
    recovered side, plus retries) on something that's certain to fail, not
    merely likely to. Returns `{label: reason}`; a label absent here is still
    worth attempting — most real mismatches ARE genuine surprises, and this
    must never suppress those. Every reason string here is prefixed
    `_PREDICTED_PREFIX` when actually used as a StreamCheck.skipped_reason
    (by the caller), so a verify log/summary can count real skips precisely.

    Three categories, matched 1:1 to text already used after-the-fact in
    ffmpeg_runner.py's `compare_adaptive`:
      - Video: a transcoded clip with no archival track of its own was
        re-encoded straight into the shared baseline — nothing byte-exact
        survives to compare against, by design, every time.
      - Camera audio (non-first clip on a shared track): a NON-FIRST clip
        sharing a track (baseline or a grouped archival track) has its audio
        seeked by a video-based offset that drifts from the audio's own true
        cumulative position, and AAC priming shifts at the concat seam —
        confirmed directly (real multi-clip masters) that this fails for
        every clip meeting this condition, not just some.
      - Camera audio (edit-list priming gap): ANY clip recovered via a
        concat-based cut rather than its own dedicated archival track — first
        clip included — whose ORIGINAL audio carries an edit-list-driven
        encoder priming/discard packet (see
        `clip_has_audio_priming_gap`'s own docstring for the full mechanism)
        loses that discard marking during the cut. This is a CONSTANT
        time-base shift through the whole clip, not a boundary artifact, so
        it fails regardless of position — confirmed directly on both a
        synthetic ffmpeg-encoded clip and real camera footage (battle-test
        round 2). Checked separately from the non-first-clip case above
        since a first/lone clip on a shared track wouldn't otherwise be
        predicted here at all, yet fails just the same when its source has
        this gap.
    """
    predicted = {}
    # Any clip whose video isn't a byte-exact copy of its own archival track —
    # "transcode" (odd spec), "hdr" (routed through the same encoder path —
    # see manifest.ClipEntry.recovery_fidelity's own docstring), or an
    # "ok"-conform clip whose shared baseline itself got re-encoded (compat
    # baseline) — is expected to differ from its original here. Checking only
    # the literal string "transcode" missed both "hdr" and the compat-baseline
    # case, producing a spurious "unexpected mismatch" report for video that
    # was never promised to survive byte-exact.
    if entry.recovery_fidelity == "transcoded" and plan.video_stream == 0:
        predicted["Video"] = (
            "expected: this clip needed conforming and has no archival track of its own, "
            "so it was re-encoded straight into the shared baseline — its video is supposed "
            "to differ from the original after transcoding. Enable Archival master + "
            "\"One track per clip\" (or \"Optimize baseline for delivery\") if you need a "
            "byte-exact copy of this clip as well.")
    if entry.has_camera_audio and plan.audio_stream is not None and not own_archival_track:
        if plan.video_start > 0 and not safe_to_read_unbounded:
            predicted["Camera audio"] = (
                "this clip's audio sits mid-way in a shared archival track; its recovered "
                "samples can't be aligned for exact verification here because AAC priming plus "
                "audio/video boundary drift at the concat seam shift the window — use "
                "One-track-per-clip archival for verifiable, byte-exact audio.")
        elif source_has_audio_priming_gap:
            predicted["Camera audio"] = (
                "this clip's original audio has encoder priming samples (a standard AAC/MP4 "
                "convention) that its container's edit list marks for a player to skip — cutting "
                "this clip out of a shared baseline/archival track doesn't preserve that marking, "
                "so the recovered audio keeps those samples and every sample after them shifts by "
                "one AAC frame's worth of time (~20ms). Enable Archival master + \"One track per "
                "clip\" for a byte-exact copy that avoids this cut entirely.")
    return predicted


def _run_framemd5(ff: str, path, seek: float, duration: float, stream: int = 0, **kwargs) -> list:
    """Decode-hash one short window of a video stream via ffmpeg's framemd5
    muxer — the cheap building block quick_video_rounding_check uses to
    distinguish benign window-rounding (core.seam_diag Mechanism 2) from a
    genuine mismatch without a full-duration extraction+hash pass. Same
    per-frame technique tools/diagnose_midtrack_decode.py runs on demand,
    just sized down to run automatically ahead of every measured-window
    video comparison."""
    cmd = [ff, "-v", "error"]
    if seek > 0:
        cmd += ["-ss", f"{seek:.3f}"]
    cmd += ["-t", f"{duration:.3f}", "-i", str(path),
            "-map", f"0:v:{stream}", "-f", "framemd5", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, **kwargs)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "")[-300:])
    return seam_diag.parse_framemd5(r.stdout)


def quick_video_rounding_check(ff: str, source_path, master_path, clip_start: float,
                               clip_duration: float, fps: float, video_stream: int = 0,
                               window_s: float = 3.0, lead_s: float = 2.0, **kwargs) -> tuple:
    """Cheap pre-check for a mid-concat 'measured window' Video mismatch
    that's about to fail a full byte-exact comparison: decode a SHORT window
    of frame-hashes from the clip's head and tail (not the whole clip) and
    classify each against the matching slice of the master (core.seam_diag)
    — the same technique tools/diagnose_midtrack_decode.py uses on demand,
    small enough to run automatically before spending a full extraction pass
    that a real merge (Task 87/93 investigation) showed fails this way on
    nearly every mid-concat clip, always for the same benign reason.

    Returns (benign, detail). `benign` is True only when EVERY window
    checked classifies as MATCH or WINDOW-OFFSET (Mechanism 2) — a SEAM or
    DIVERGENT verdict on either end, or a decode error, means this must NOT
    be treated as benign, so real corruption at a join is never silently
    waved through; the caller falls back to the full comparison in that case."""
    verdicts = []
    try:
        expected = int(round(lead_s * fps))
        orig_head = _run_framemd5(ff, source_path, 0.0, window_s, stream=0, **kwargs)
        mast_head = _run_framemd5(ff, master_path, max(0.0, clip_start - lead_s),
                                  lead_s + window_s + 1.0, stream=video_stream, **kwargs)
    except Exception as e:
        return False, f"quick pre-check couldn't decode the head window: {e}"
    verdicts.append(("head", seam_diag.classify_window(orig_head, mast_head, expected)))

    if clip_duration > window_s + 1:
        tail_lead = 2.0
        try:
            orig_tail = _run_framemd5(ff, source_path, max(0.0, clip_duration - window_s),
                                      window_s + 1.0, stream=0, **kwargs)
            mast_tail_seek = max(0.0, clip_start + clip_duration - window_s - tail_lead)
            mast_tail = _run_framemd5(ff, master_path, mast_tail_seek,
                                      window_s + tail_lead + 1.0, stream=video_stream, **kwargs)
        except Exception as e:
            return False, f"quick pre-check couldn't decode the tail window: {e}"
        tail_expected = int(round(tail_lead * fps))
        verdicts.append(("tail", seam_diag.classify_window(orig_tail, mast_tail, tail_expected)))

    ok = (seam_diag.VERDICT_MATCH, seam_diag.VERDICT_OFFSET)
    benign = all(v.verdict in ok for _, v in verdicts)
    parts = []
    for name, v in verdicts:
        if v.verdict == seam_diag.VERDICT_OFFSET:
            ms = (v.shift_frames / fps) * 1000 if fps > 0 else 0
            parts.append(f"{name} decodes identically but {v.shift_frames:+d} frame(s) "
                         f"(≈{ms:+.0f}ms) from the modelled position")
        elif v.verdict == seam_diag.VERDICT_MATCH:
            parts.append(f"{name} decodes identically at the modelled position")
        else:
            parts.append(f"{name}: {v.verdict}")
    return benign, "; ".join(parts)


def quick_wav_rounding_check(ff: str, source_wav_path, master_path, wav_start: float,
                             wav_stream: int, src_seek: Optional[float] = None,
                             window_s: float = 3.0, max_shift_ms: float = 120.0,
                             step_ms: float = 15.0, **kwargs) -> tuple:
    """Cheap pre-check for a measured-window WAV-backup mismatch that's about
    to fail a full-duration decode+hash comparison: decode a SHORT window
    from the head of the source WAV, then scan a small range of seek offsets
    around the master's modelled position for an EXACT decoded-PCM match at
    a shifted offset — the audio analogue of quick_video_rounding_check's
    window-rounding detection. A match at any shift confirms the same benign
    drift already diagnosed for measured WAV windows (the WAV track's own
    cumulative position isn't always frame-for-frame identical to the
    video-based window), not stream corruption.

    Returns (benign, detail). No matching shift found (or a decode error)
    means this is NOT treated as benign — the caller falls through to the
    existing full comparison so a genuine mismatch is still reported."""
    try:
        src_cmd = build_decoded_audio_md5_cmd(ff, source_wav_path, audio_stream=0,
                                              seek=src_seek, duration=window_s)
        src_hash = decoded_md5(src_cmd, **kwargs)
    except Exception as e:
        return False, f"quick pre-check couldn't decode the source window: {e}"
    if not src_hash:
        return False, "quick pre-check: source window didn't decode"

    steps = int(max_shift_ms // step_ms)
    shifts_ms = [0.0]
    for k in range(1, steps + 1):
        shifts_ms += [k * step_ms, -k * step_ms]
    for shift_ms in shifts_ms:
        seek = max(0.0, wav_start + shift_ms / 1000.0)
        try:
            cmd = build_decoded_audio_md5_cmd(ff, master_path, audio_stream=wav_stream,
                                              seek=seek, duration=window_s)
            h = decoded_md5(cmd, **kwargs)
        except Exception:
            continue
        if h and h == src_hash:
            return True, (f"window rounding confirmed: the master's WAV decode matches the "
                          f"source exactly {shift_ms:+.0f}ms from the modelled position")
    return False, ""


def write_verify_log(path: Path, master_name: str, results: list):
    """Human-readable report: pass/fail per clip/stream plus every MD5 value,
    so a mismatch is provable rather than asserted. Sibling to the existing
    manifest/restore-log files (<master-stem>.verify.log)."""
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    lines = [
        f"LunaVault FuseBox — MD5 recovery verification for: {master_name}",
        f"Result: {passed} / {total} clips verified byte-identical to their originals.",
        "",
    ]
    for r in results:
        lines.append(f"{'PASS' if r.passed else 'FAIL'}  {r.name}")
        if r.error:
            lines.append(f"       error: {r.error}")
        for c in r.checks:
            if c.skipped_reason:
                lines.append(f"       {c.label}: skipped ({c.skipped_reason})")
                continue
            status = "match" if c.match else "MISMATCH"
            lines.append(f"       {c.label}: {status}")
            if c.source_md5 or c.recovered_md5:
                lines.append(f"           original:  {c.source_md5}")
                lines.append(f"           recovered: {c.recovered_md5}")
            if c.diagnosis:
                lines.append(f"           diagnosis: {c.diagnosis}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
