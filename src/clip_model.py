"""clip_model.py — clip data model, folder scanning, WAV pairing, end-alignment."""

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from probe import StreamInfo


@dataclass
class ClipInfo:
    path: Path
    wav_path: Optional[Path] = None
    stream: Optional[StreamInfo] = None
    wav_duration: float = 0.0
    wav_offset: float = 0.0        # constant offset for the LOSSLESS WAV track
    order_idx: int = 0
    manually_moved: bool = False
    filename_ts: Optional[int] = None
    selected: bool = True          # ticked in the merge table — unticked clips are excluded
    # ── Camera identity (multicam overhaul) — from camera_id.identify_camera ──
    camera_id: str = ""            # stable key: clips sharing it are one camera
    camera_label: str = ""         # human label, user-overridable
    # ── Sync analysis (Phase 2) — populated by core.sync_advanced.analyze_sync ──
    sync_done: bool = False
    sync_drift_ratio: float = 1.0          # WAV resample factor → MIX track only
    sync_confidence_ms: float = 0.0
    sync_polarity_inverted: bool = False
    sync_windows: int = 0
    sync_lags_ms: list = field(default_factory=list)
    manual_nudge_ms: float = 0.0           # user override from Advanced sync dialog
    # "auto" (end-alignment, or a coarse rescue if the clip/WAV durations differ
    # by far more than ordinary pre/post-roll — see core.sync_advanced), "start"
    # (force preroll=0: assume the WAV and clip begin together), or "end" (force
    # literal end-alignment even for a large mismatch — the user's own call that
    # the recordings really do finish together). User override from Advanced
    # sync dialog; re-running analysis re-reads this.
    alignment_mode: str = "auto"
    # User override for the MIX track's drift (tempo) correction — None means
    # "use the auto-detected sync_drift_ratio" (the default); any float
    # (including 1.0, meaning "no correction") replaces it outright. Never
    # touches the lossless WAV track, which is never resampled either way.
    drift_override: Optional[float] = None
    # Per-clip override of which source fills this clip's disposition-default
    # ("Primary") track — None/"auto" defers to the global Camera/WAV choice
    # (plus core.ffmpeg_cmd's own no-source fallback); "camera"/"wav"/"mix"
    # forces that source instead, for this clip only. Set via the Merge tab's
    # per-clip Primary column.
    primary_override: Optional[str] = None
    # Opt-in: also embed this clip's untouched original WAV in full on its own
    # standalone lossless track (the audio analogue of the video archival-track
    # mechanism), regardless of which alignment_mode/primary_override is used
    # for the actual playback track. Defaults OFF — an explicit per-clip choice,
    # not a silent doubling of every merge's audio footprint.
    preserve_wav_full: bool = False
    # ── Low-res proxy pairing (e.g. Insta360-style .lrv sidecar) ───────────────
    lrv_path: Optional[Path] = None
    lrv_duration: float = 0.0
    lrv_width: int = 0
    lrv_height: int = 0
    # Per-clip override of how this clip's VIDEO lands in the baseline: "auto"
    # (today's behaviour — stream copy if it matches spec, else transcode),
    # "transcode" (force a transcode even though it already matches, archiving
    # the byte-exact original on its own track — the existing archival-track
    # mechanism, just triggered manually instead of by a real spec mismatch),
    # or "lrv" (conform the paired low-res proxy into the baseline INSTEAD of
    # this clip's own footage — faster to encode — while still archiving the
    # byte-exact original). Ignored when has_lrv() is False for "lrv".
    video_source_override: str = "auto"
    # Opt-in: also embed this clip's low-res proxy, stream-copied, on its own
    # standalone track — independent of video_source_override (the proxy can
    # be preserved as a backup even when the original 4K is what plays).
    # Defaults OFF, same reasoning as preserve_wav_full above.
    preserve_lrv: bool = False

    def effective_drift_ratio(self) -> float:
        return self.drift_override if self.drift_override is not None else self.sync_drift_ratio

    def has_lrv(self) -> bool:
        return self.lrv_path is not None

    def effective_status(self) -> str:
        """clip.status, but forced to "transcode" when video_source_override
        requests it — core.ffmpeg_cmd/ffmpeg_runner.py read this (not the raw
        probed `status`) wherever they decide stream-copy vs. transcode, so a
        manual override reuses the SAME conform+archival machinery a genuine
        spec mismatch already goes through, rather than inventing a second
        path. "lrv" also resolves to "transcode" — using a different SOURCE
        for the transcode doesn't change that a transcode is happening — but
        only when a proxy is actually paired; with nothing to swap in, "lrv"
        falls back to the real (Auto) status rather than forcing a pointless
        re-encode of the original at its own unchanged spec."""
        if self.video_source_override == "transcode":
            return "transcode"
        if self.video_source_override == "lrv" and self.has_lrv():
            return "transcode"
        return self.status

    @property
    def status(self) -> str:
        return self.stream.status if self.stream else "unknown"

    @property
    def conflicts(self) -> list:
        return self.stream.conflicts if self.stream else []

    @property
    def duration(self) -> float:
        return self.stream.duration if self.stream else 0.0

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def stem(self) -> str:
        return self.path.stem

    def has_wav(self) -> bool:
        return self.wav_path is not None

    def has_camera_audio(self) -> bool:
        """True if the MP4 actually contains an audio stream.

        Some clips (e.g. a wireless mic that wasn't connected) have video only;
        mapping camera audio for those would make ffmpeg fail.
        """
        return bool(self.stream and self.stream.audio_codec)

    def wav_flags(self) -> list:
        """Return ffmpeg -ss / -itsoffset flags for WAV end-alignment."""
        if not self.has_wav():
            return []
        if self.wav_offset < -0.001:
            return ["-ss", f"{abs(self.wav_offset):.6f}", "-i", str(self.wav_path)]
        elif self.wav_offset > 0.001:
            return ["-itsoffset", f"{self.wav_offset:.6f}", "-i", str(self.wav_path)]
        else:
            return ["-i", str(self.wav_path)]

    def friendly_offset(self) -> str:
        if not self.has_wav():
            return "—"
        if self.wav_offset < -0.001:
            return f"trim WAV {abs(self.wav_offset)*1000:.0f}ms"
        elif self.wav_offset > 0.001:
            return f"delay WAV {self.wav_offset*1000:.0f}ms"
        return "in sync"


_TS_PATTERN = re.compile(r'_(\d{2})(\d{2})(\d{2})_')


def _parse_ts(stem: str) -> Optional[int]:
    m = _TS_PATTERN.search(stem)
    if not m:
        return None
    h, mn, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return h * 3600 + mn * 60 + s


_KEY_DATE = re.compile(r"(\d{8})")
_KEY_TIME = re.compile(r"\d{8}[_\-]?(\d{6})")
_KEY_NUMS = re.compile(r"\d+")


def _clip_key(stem: str):
    """A camera-agnostic pairing key: (date, time, trailing clip-number).

    Lets cross-brand names pair — e.g. Insta360's audio `LRV_20260703_130055_01_004.lrv`
    ↔ video `VID_20260703_130055_00_004`, which share date+time and the trailing 004
    (the differing `_00_`/`_01_` index and `LRV`/`VID` prefix are ignored). Returns
    None if the stem has no recognisable date/number."""
    d = _KEY_DATE.search(stem or "")
    nums = _KEY_NUMS.findall(stem or "")
    if not d or not nums:
        return None
    t = _KEY_TIME.search(stem)
    return (d.group(1), t.group(1) if t else "", nums[-1])


def _pair_wav(mp4_stem: str, wav_stems: dict) -> Optional[Path]:
    # 1. Exact / prefix (the app's own `_backup.wav` and Luna convention).
    if mp4_stem in wav_stems:
        return wav_stems[mp4_stem]
    for wstem, wpath in wav_stems.items():
        if wstem.startswith(mp4_stem) or mp4_stem.startswith(wstem):
            return wpath
    # 2. Cross-brand: match on the (date, time, clip-number) key.
    vk = _clip_key(mp4_stem)
    if vk:
        for wstem, wpath in wav_stems.items():
            if _clip_key(wstem) == vk:
                return wpath
    return None


def _pair_lrv(mp4_stem: str, lrv_stems: dict) -> Optional[Path]:
    """Same matching cascade as `_pair_wav`, for a camera's own low-res proxy
    (e.g. Insta360's `LRV_<date>_<time>_<idx>_<n>.lrv` alongside
    `VID_<date>_<time>_<idx>_<n>.mp4`)."""
    if mp4_stem in lrv_stems:
        return lrv_stems[mp4_stem]
    for lstem, lpath in lrv_stems.items():
        if lstem.startswith(mp4_stem) or mp4_stem.startswith(lstem):
            return lpath
    vk = _clip_key(mp4_stem)
    if vk:
        for lstem, lpath in lrv_stems.items():
            if _clip_key(lstem) == vk:
                return lpath
    return None


def _iso_epoch(ct: str):
    """Parse an ISO-8601 creation_time (…Z or with offset) to a POSIX epoch, or None."""
    if not ct:
        return None
    try:
        return datetime.fromisoformat(ct.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def assign_cameras(clips: list, saved_labels: dict = None) -> list:
    """Set each clip's camera_id/camera_label from its probed device metadata +
    filename (call after probing). Preserves any label the user has already
    overridden (non-empty camera_label with the same camera_id). `saved_labels`
    is an optional {camera_id: label} map (e.g. from Settings) remembered from
    a previous folder — takes priority over the guessed default label so a
    camera the user has named once is recognized on future clip loads too."""
    from camera_id import identify_camera
    saved_labels = saved_labels or {}
    for c in clips:
        device = c.stream.device if c.stream else ""
        key, label = identify_camera(device, c.name)
        if c.camera_id == key and c.camera_label:
            continue   # keep a user override for this camera
        c.camera_id, c.camera_label = key, saved_labels.get(key, label)
    return clips


def group_clips_by_camera(clips: list) -> dict:
    """{camera_id: [clips…]} preserving first-seen order of both cameras and clips."""
    groups: dict = {}
    for c in clips:
        groups.setdefault(c.camera_id or "unknown", []).append(c)
    return groups


def order_clips_by_time(clips: list) -> list:
    """Reorder clips chronologically and reassign order_idx.

    Prefers container `creation_time` (UTC — reliable across cameras and immune to
    DST/filename quirks); only used when EVERY clip has one, else falls back to the
    filename-timestamp sort. Call after probing (needs clip.stream.creation_time)."""
    epochs = {id(c): _iso_epoch(getattr(c.stream, "creation_time", "") if c.stream else "")
              for c in clips}
    if clips and all(epochs[id(c)] is not None for c in clips):
        clips.sort(key=lambda c: epochs[id(c)])
    else:
        clips.sort(key=lambda c: (c.filename_ts if c.filename_ts is not None else 99999999, c.name))
    for i, c in enumerate(clips):
        c.order_idx = i
    return clips


def scan_folder(folder: Path) -> list:
    mp4s = sorted(folder.glob("*.mp4"), key=lambda p: p.name.lower())
    wavs = sorted(folder.glob("*.wav"), key=lambda p: p.name.lower())
    wav_stems = {w.stem: w for w in wavs}
    lrvs = sorted(folder.glob("*.lrv"), key=lambda p: p.name.lower())
    lrv_stems = {l.stem: l for l in lrvs}

    clips = []
    for mp4 in mp4s:
        wav = _pair_wav(mp4.stem, wav_stems)
        lrv = _pair_lrv(mp4.stem, lrv_stems)
        ts  = _parse_ts(mp4.stem)
        clips.append(ClipInfo(path=mp4, wav_path=wav, lrv_path=lrv, filename_ts=ts))

    clips.sort(key=lambda c: (
        c.filename_ts if c.filename_ts is not None else 99999999,
        c.name,
    ))
    for i, c in enumerate(clips):
        c.order_idx = i
    return clips


def unpaired_wavs(folder: Path, clips: list) -> list:
    paired = {c.wav_path for c in clips if c.wav_path}
    return [w for w in folder.glob("*.wav") if w not in paired]


def check_dst_warning(clips: list) -> bool:
    ordered = sorted(clips, key=lambda c: c.order_idx)
    for i in range(1, len(ordered)):
        a = ordered[i-1].filename_ts
        b = ordered[i].filename_ts
        if a is not None and b is not None:
            if 55 * 60 <= abs(b - a) <= 65 * 60:
                return True
    return False


# Camera file-split detection: a camera hitting its own length/size limit splits
# one continuous take into two video files, but a separate audio-backup device
# often just keeps rolling — leaving the SECOND clip looking like it has no WAV
# at all, when really its audio is sitting in the tail of the FIRST clip's WAV
# (found directly on a real 8-clip shoot: clip N's WAV ran ~6m24s past its own
# video, matching clip N+1's video length to within half a second, while clip
# N+1 itself had no WAV of its own).
_SPLIT_ADJACENCY_TOLERANCE_S = 5.0    # normal camera file-split gap is 1-2s
_SPLIT_MATCH_TOLERANCE_S = 3.0        # how close "wav_dur ≈ a.dur + b.dur" must be


def _clip_gap_seconds(a: "ClipInfo", b: "ClipInfo") -> Optional[float]:
    """Seconds between `a` ending and `b` starting, using each clip's own
    container creation_time when both have one (reliable regardless of
    filename convention), else the filename-embedded time as a fallback.
    None if neither source is available for both clips."""
    a_ct = _iso_epoch(getattr(a.stream, "creation_time", "") if a.stream else "")
    b_ct = _iso_epoch(getattr(b.stream, "creation_time", "") if b.stream else "")
    if a_ct is not None and b_ct is not None:
        return (b_ct) - (a_ct + a.duration)
    if a.filename_ts is not None and b.filename_ts is not None:
        return (b.filename_ts) - (a.filename_ts + a.duration)
    return None


def detect_clip_splits(clips: list) -> list:
    """[(clip_a, clip_b), …] — adjacent clip pairs where clip_a's WAV looks
    like it also covers clip_b's video: clip_a has a WAV, clip_b doesn't, the
    two are time-adjacent (no meaningful gap), and clip_a's WAV runs about as
    long as clip_a's own video PLUS clip_b's video combined. Deliberately
    requires all three signals together — any one alone (e.g. just a long
    WAV) is too weak to act on without risking a false positive."""
    ordered = sorted(clips, key=lambda c: c.order_idx)
    pairs = []
    for i in range(len(ordered) - 1):
        a, b = ordered[i], ordered[i + 1]
        if not a.has_wav() or b.has_wav():
            continue
        if a.duration <= 0 or b.duration <= 0 or a.wav_duration <= 0:
            continue
        gap = _clip_gap_seconds(a, b)
        if gap is None or abs(gap) > _SPLIT_ADJACENCY_TOLERANCE_S:
            continue
        combined = a.duration + b.duration
        if abs(a.wav_duration - combined) <= _SPLIT_MATCH_TOLERANCE_S:
            pairs.append((a, b))
    return pairs
