"""core/track_info.py — gather human-readable details for a clip's tracks.

Used by the Custom-audio and Advanced-output dialogs to show codec, duration,
bitrate, file size and lossy/lossless for the video and each audio source
(camera / WAV / mix). Qt-free; WAV details come from a quick ffprobe.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from probe import probe, probe_duration


@dataclass
class TrackMeta:
    kind: str             # "video" | "camera" | "wav" | "mix"
    label: str
    src_codec: str = ""
    out_codec: str = ""
    lossless: bool = False
    duration: float = 0.0
    bitrate: int = 0      # bits/sec (source), 0 = unknown
    filesize: int = 0     # bytes (source), 0 = n/a (derived)
    channels: int = 0
    available: bool = True
    note: str = ""


def fmt_duration(secs: float) -> str:
    if secs <= 0:
        return "—"
    h, r = divmod(int(secs), 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fmt_bitrate(bps: int) -> str:
    if not bps:
        return "—"
    if bps >= 1_000_000:
        return f"{bps/1_000_000:.1f} Mbps"
    return f"{bps/1000:.0f} kbps"


def fmt_size(b: int) -> str:
    if not b:
        return "—"
    if b >= 1024**3:
        return f"{b/1024**3:.2f} GB"
    if b >= 1024**2:
        return f"{b/1024**2:.1f} MB"
    return f"{b/1024:.0f} KB"


def _display_codec(name: str) -> str:
    return {
        "hevc": "HEVC", "h265": "HEVC", "h264": "H.264", "avc": "H.264",
        "aac": "AAC", "alac": "ALAC", "pcm_s16le": "PCM 16-bit",
        "pcm_s24le": "PCM 24-bit", "ac3": "AC-3", "mp3": "MP3",
    }.get((name or "").lower(), (name or "—").upper())


def video_meta(clip) -> TrackMeta:
    st = clip.stream
    conform = clip.status == "ok"
    codec = _display_codec(st.codec) if st else "—"
    res = f"{st.width}×{st.height}" if st else ""
    return TrackMeta(
        kind="video",
        label=f"Video — {res}".strip(" —"),
        src_codec=codec,
        out_codec=f"{codec} (copy)" if conform else "HEVC (transcode)",
        lossless=conform,     # stream copy is lossless; transcode is not
        duration=clip.duration,
        filesize=clip.path.stat().st_size if clip.path.exists() else 0,
        note="stream-copied, no quality loss" if conform else "conformed at high quality",
    )


def audio_tracks(clip, ffprobe: Optional[str] = None) -> dict:
    """Return {kind: TrackMeta} for camera, wav and mix (availability flagged)."""
    out = {}
    st = clip.stream

    has_wav = clip.has_wav()
    cam_label = ("Camera audio (Bluetooth mic)" if has_wav
                 else "Camera audio (on-board mic)")
    cam_note = ("from the MP4 — kept lossless (stream copy)" if has_wav
                else "on-board camera mic (no WAV pairing) — kept lossless (stream copy)")

    out["camera"] = TrackMeta(
        kind="camera", label=cam_label,
        src_codec=_display_codec(st.audio_codec) if st else "—",
        out_codec=f"{_display_codec(st.audio_codec) if st else 'AAC'} (copy)",
        lossless=False,
        duration=clip.duration,
        bitrate=st.audio_bit_rate if st else 0,
        filesize=clip.path.stat().st_size if clip.path.exists() else 0,
        channels=st.audio_channels if st else 0,
        available=True,
        note=cam_note,
    )

    wav = TrackMeta(kind="wav", label="WAV backup (on-board mic)", available=has_wav)
    if has_wav:
        wpath = clip.wav_path
        winfo = probe(ffprobe, str(wpath)) if ffprobe else None
        wdur = (winfo.duration if winfo and winfo.duration else
                (clip.wav_duration or probe_duration(ffprobe, str(wpath)) if ffprobe else 0.0))
        wav.src_codec = _display_codec(winfo.audio_codec) if winfo else "PCM"
        wav.out_codec = "ALAC (lossless)"
        wav.lossless = True
        wav.duration = wdur
        wav.bitrate = winfo.audio_bit_rate if winfo else 0
        wav.channels = winfo.audio_channels if winfo else 0
        wav.filesize = wpath.stat().st_size if wpath and wpath.exists() else 0
        wav.note = "on-board mic — re-encoded losslessly to ALAC"
    out["wav"] = wav

    out["mix"] = TrackMeta(
        kind="mix", label="Combined mix (camera + WAV)",
        src_codec="—", out_codec="AAC 256k",
        lossless=False, duration=clip.duration,
        available=has_wav and clip.status == "ok",
        note="derived track — only on clips with a WAV that don't need transcoding",
    )
    return out
