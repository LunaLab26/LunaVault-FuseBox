"""core/manifest.py — the archival master's clip manifest.

A manifest records, per original clip that went into a master, everything the
"Extract" side needs to recover it losslessly later: its original filename and
container, its codec/resolution/fps/pixel-format, duration and byte size, whether
it was conformed into the baseline or embedded on an archival track, and — once
archival tracks exist (Phase 2) — exactly *where* it lives (which archival video
track + its in-track start/duration, or which baseline chapter).

Pure module: dataclasses + JSON (de)serialisation + the spec-signature and
in-track-offset helpers, mirroring core/review_media.py's "builders here, no
subprocess" split. The one impure convenience (`read_manifest`, which shells out
to ffprobe) sits at the bottom and reuses the pure parsers, the same way probe.py
pairs `parse_*` with `probe_*`.

Storage is deliberately belt-and-braces and additive — it never changes a
master's audio/video streams:
  - a sidecar `<master-stem>.manifest.json` next to the master, and
  - an embedded global metadata tag inside the master (MOV needs
    `-movflags use_metadata_tags` for an arbitrary key to survive the mux).
The reader tries the embedded copy first, then the sidecar.
"""

import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

MANIFEST_VERSION = 1
MANIFEST_METADATA_KEY = "lunavault_manifest"
SIDECAR_SUFFIX = ".manifest.json"


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class ClipEntry:
    """One original clip's provenance + location within the master."""
    source_filename: str = ""      # original name WITH extension, e.g. "VID_0001.mp4"
    container: str = ""            # lowercase extension, no dot, e.g. "mp4"
    codec: str = ""
    width: int = 0
    height: int = 0
    fps: str = ""                  # r_frame_rate string, e.g. "30000/1001"
    pix_fmt: str = ""
    bit_depth: int = 0
    duration: float = 0.0
    size_bytes: int = 0
    # "ok" = already stream-copied into the baseline (recoverable from it);
    # anything else ("transcode"/"hdr"/…) = an odd-spec original that needs an
    # archival track of its own.
    conform_status: str = ""
    spec_group: str = ""           # spec signature; "" for conforming clips
    # ── Audio (Phase 2) ────────────────────────────────────────────────────────
    has_camera_audio: bool = False     # did the source MP4 carry an audio stream
    original_audio_codec: str = ""
    audio_lossless: bool = True        # is this clip's camera audio preserved losslessly
    has_wav: bool = False              # a paired WAV backup exists (baseline ALAC track)
    # ── Restore recipe (Phase 4) — everything Extract needs to put a recovered
    # clip back exactly as it was, and everything a human reading the restore
    # log needs to understand what happened to it. Rotation/pix_fmt already ride
    # with the copied stream on recovery (so playback restores correctly on its
    # own), but recording them here is what makes the restore log meaningful. ──
    rotation: int = 0                  # display rotation in degrees (0/90/180/270)
    is_vfr: bool = False                # variable frame rate in the original
    color_space: str = ""
    camera_label: str = ""             # the detected/user-named camera (camera_id.identify_camera)
    creation_time: str = ""            # ISO-8601 UTC from the original file's own metadata
    # ── Location ───────────────────────────────────────────────────────────────
    # Conforming clips live in the baseline (video 0:v:0 + baseline audio tracks), cut at
    # their chapter. Odd-spec clips live on an archival track, cut at the in-track offset.
    baseline_chapter_index: Optional[int] = None   # index in the master's chapter list
    archival_track: Optional[int] = None           # 0-based master VIDEO stream, or None if baseline
    archival_audio_stream: Optional[int] = None    # 0-based master AUDIO stream for this clip's camera audio
    in_track_start: float = 0.0                    # seconds offset within the archival track
    in_track_duration: float = 0.0


@dataclass
class Manifest:
    version: int = MANIFEST_VERSION
    master_filename: str = ""
    created_utc: str = field(default_factory=now_utc_iso)
    # role -> 0-based master AUDIO stream index for the baseline's own audio tracks,
    # e.g. {"camera": 0, "wav": 1, "mix": 2} — how Extract finds a conforming clip's
    # camera audio and any clip's WAV backup.
    baseline_audio_tracks: dict = field(default_factory=dict)
    clips: list = field(default_factory=list)      # list[ClipEntry]


# ── Spec signature + grouping ──────────────────────────────────────────────────

def spec_signature(codec: str, width: int, height: int, fps: str, pix_fmt: str,
                   rotation: int = 0) -> str:
    """Stable grouping key: clips sharing a signature can be concat-copied onto
    one archival track. Groups by the params that must match for a stream-copy
    concat to stay valid (codec, resolution, frame rate, pixel format) plus
    ROTATION — differently-rotated clips must NOT share a track or their
    orientation is lost on recovery. In practice this means "same camera/format,
    same orientation"."""
    return "|".join((
        (codec or "?").lower(),
        f"{int(width)}x{int(height)}",
        fps or "?",
        (pix_fmt or "?").lower(),
        f"rot{int(rotation) % 360}",
    ))


def group_nonconforming_by_spec(clips: list) -> dict:
    """{spec_group: [ClipEntry, …]} for the clips that need an archival track
    (conform_status != 'ok'), preserving input order within each group."""
    groups: dict = {}
    for c in clips:
        if c.conform_status == "ok":
            continue
        groups.setdefault(c.spec_group, []).append(c)
    return groups


def assign_in_track_offsets(entries: list) -> None:
    """Set each clip's in_track_start/in_track_duration cumulatively — the
    layout of one archival track that concatenates these clips in order. The
    boundaries are keyframe-aligned because each original begins with a keyframe,
    so Extract can re-cut at in_track_start with a stream copy (see the spike)."""
    t = 0.0
    for c in entries:
        c.in_track_start = t
        c.in_track_duration = c.duration
        t += c.duration


def assign_archival_locations(groups_in_order: list, base_video_count: int = 1,
                              base_audio_count: int = 0) -> tuple:
    """Fill each odd-spec clip's archival stream indices + in-track offsets, given
    the archival tracks' build order and how many video/audio streams the baseline
    already occupies.

    Mirrors `build_final_archival_mux_cmd`'s output order: baseline streams first
    (video then audio), then each archival track's [video, optional audio]. So the
    Nth archival group's video is `base_video_count + N` and its audio (if the group
    carries audio) is the next free audio index after the baseline's. Returns the
    (video, audio) stream counts consumed, for callers that need them.
    """
    v = base_video_count
    a = base_audio_count
    for entries in groups_in_order:
        assign_in_track_offsets(entries)
        vstream = v
        v += 1
        group_has_audio = any(e.has_camera_audio for e in entries)
        astream = a if group_has_audio else None
        if group_has_audio:
            a += 1
        for e in entries:
            e.archival_track = vstream
            e.archival_audio_stream = astream if e.has_camera_audio else None
    return v, a


# ── JSON (de)serialisation ─────────────────────────────────────────────────────

_CLIP_FIELDS = {f.name for f in fields(ClipEntry)}


def _manifest_to_dict(m: Manifest) -> dict:
    return {
        "version": m.version,
        "master_filename": m.master_filename,
        "created_utc": m.created_utc,
        "baseline_audio_tracks": dict(m.baseline_audio_tracks),
        "clips": [asdict(c) for c in m.clips],
    }


def to_json(m: Manifest, indent: Optional[int] = 2) -> str:
    """Pretty JSON for the sidecar (indent=2); pass indent=None for the compact
    single-line form used in the embedded metadata tag (no newlines)."""
    separators = (",", ":") if indent is None else None
    return json.dumps(_manifest_to_dict(m), indent=indent,
                      separators=separators, ensure_ascii=False)


def from_json(s: str) -> Manifest:
    d = json.loads(s)
    clips = [ClipEntry(**{k: v for k, v in (c or {}).items() if k in _CLIP_FIELDS})
             for c in d.get("clips", [])]
    return Manifest(
        version=int(d.get("version", MANIFEST_VERSION)),
        master_filename=d.get("master_filename", "") or "",
        created_utc=d.get("created_utc", "") or "",
        baseline_audio_tracks=dict(d.get("baseline_audio_tracks", {}) or {}),
        clips=clips,
    )


# ── Storage: sidecar + embedded metadata ───────────────────────────────────────

def sidecar_path(master_path) -> Path:
    """`<master-stem>.manifest.json` beside the master."""
    p = Path(master_path)
    return p.with_name(p.stem + SIDECAR_SUFFIX)


def write_sidecar(m: Manifest, master_path) -> Path:
    path = sidecar_path(master_path)
    path.write_text(to_json(m, indent=2), encoding="utf-8")
    return path


RESTORE_LOG_SUFFIX = ".restore.log"


def restore_log_path(master_path) -> Path:
    """`<master-stem>.restore.log` beside the master — a plain-English
    companion to the machine-readable manifest."""
    p = Path(master_path)
    return p.with_name(p.stem + RESTORE_LOG_SUFFIX)


def _clip_restore_lines(c: ClipEntry) -> list:
    lines = [f"{c.source_filename}" + (f"  [{c.camera_label}]" if c.camera_label else "")]
    spec = f"{(c.codec or '?').upper()} {c.width}x{c.height} {c.bit_depth}-bit {c.fps}fps"
    if c.rotation:
        spec += f", rotated {c.rotation}°"
    if c.is_vfr:
        spec += ", VFR"
    lines.append(f"  spec: {spec}")
    if c.creation_time:
        lines.append(f"  recorded: {c.creation_time}")
    if c.conform_status == "ok":
        lines.append(f"  recovers from: baseline track, chapter {c.baseline_chapter_index}"
                     " (this clip conformed to the baseline — its video is already the original,"
                     " stream-copied)")
    else:
        loc = f"archival track {c.archival_track}"
        if c.in_track_duration and c.in_track_start > 0.0:
            loc += f", offset {c.in_track_start:.3f}s (concatenated with other same-spec clips —"
            loc += " recovery is content-complete but not guaranteed bit-exact at this boundary;"
            loc += " see DEVELOPMENT.md)"
        else:
            loc += " (this clip has the track to itself — recovery is bit-exact)"
        lines.append(f"  recovers from: {loc}")
    if c.has_camera_audio:
        note = "lossless" if c.audio_lossless else "re-encoded (lossy) in the baseline"
        lines.append(f"  camera audio: {c.original_audio_codec or '?'}, {note}"
                     + (f", archival audio stream {c.archival_audio_stream}"
                        if c.archival_audio_stream is not None else ""))
    else:
        lines.append("  camera audio: none in the original")
    if c.has_wav:
        lines.append("  WAV backup: recoverable losslessly from the baseline's ALAC track")
    return lines


def write_restore_log(m: Manifest, master_path) -> Path:
    """A plain-English companion to the sidecar/embedded manifest — for a human
    to read and understand how each clip can be recovered, without needing to
    parse the JSON. Not consumed by Extract; the manifest is authoritative."""
    lines = [
        f"Restore log for {m.master_filename}",
        f"Created {m.created_utc}",
        f"{len(m.clips)} original clip(s) archived",
        "",
    ]
    for c in m.clips:
        lines.extend(_clip_restore_lines(c))
        lines.append("")
    path = restore_log_path(master_path)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def metadata_embed_args(m: Manifest, is_mov: bool = True) -> list:
    """ffmpeg OUTPUT args that embed the manifest as a global metadata tag.
    Insert these immediately before the output filename. MOV/MP4 discards
    unknown metadata keys unless `-movflags use_metadata_tags` is set."""
    args = []
    if is_mov:
        args += ["-movflags", "use_metadata_tags"]
    args += ["-metadata", f"{MANIFEST_METADATA_KEY}={to_json(m, indent=None)}"]
    return args


def parse_from_format_tags(tags: dict) -> Optional[Manifest]:
    """Pure: pull a Manifest out of an ffprobe `format.tags` dict, if present.
    MOV may prefix/case-fold custom keys, so match case-insensitively."""
    if not tags:
        return None
    raw = tags.get(MANIFEST_METADATA_KEY)
    if raw is None:
        for k, v in tags.items():
            if k.lower().endswith(MANIFEST_METADATA_KEY):
                raw = v
                break
    if not raw:
        return None
    try:
        return from_json(raw)
    except Exception:
        return None


def read_manifest(ffprobe_bin: str, master_path: str) -> Optional[Manifest]:
    """Load a master's manifest — embedded copy first, sidecar fallback."""
    try:
        kw = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
        r = subprocess.run(
            [ffprobe_bin, "-v", "quiet", "-print_format", "json", "-show_format", str(master_path)],
            capture_output=True, text=True, timeout=30, **kw)
        if r.returncode == 0:
            tags = (json.loads(r.stdout).get("format", {}) or {}).get("tags", {}) or {}
            m = parse_from_format_tags(tags)
            if m is not None:
                return m
    except Exception:
        pass
    sc = sidecar_path(master_path)
    if sc.exists():
        try:
            return from_json(sc.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None
