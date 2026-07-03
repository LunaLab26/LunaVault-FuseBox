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
    # ── Sync analysis (Phase 2) — populated by core.sync_advanced.analyze_sync ──
    sync_done: bool = False
    sync_drift_ratio: float = 1.0          # WAV resample factor → MIX track only
    sync_confidence_ms: float = 0.0
    sync_polarity_inverted: bool = False
    sync_windows: int = 0
    sync_lags_ms: list = field(default_factory=list)
    manual_nudge_ms: float = 0.0           # user override from Advanced sync dialog

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


def _iso_epoch(ct: str):
    """Parse an ISO-8601 creation_time (…Z or with offset) to a POSIX epoch, or None."""
    if not ct:
        return None
    try:
        return datetime.fromisoformat(ct.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


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

    clips = []
    for mp4 in mp4s:
        wav = _pair_wav(mp4.stem, wav_stems)
        ts  = _parse_ts(mp4.stem)
        clips.append(ClipInfo(path=mp4, wav_path=wav, filename_ts=ts))

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
