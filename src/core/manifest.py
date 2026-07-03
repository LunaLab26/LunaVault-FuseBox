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
    # ── Location (filled progressively; Phase 2 sets the archival fields) ──────
    baseline_chapter_index: Optional[int] = None   # index in the master's chapter list
    archival_track: Optional[int] = None           # 0-based video-stream index, or None
    in_track_start: float = 0.0                    # seconds offset within the archival track
    in_track_duration: float = 0.0


@dataclass
class Manifest:
    version: int = MANIFEST_VERSION
    master_filename: str = ""
    created_utc: str = field(default_factory=now_utc_iso)
    clips: list = field(default_factory=list)      # list[ClipEntry]


# ── Spec signature + grouping ──────────────────────────────────────────────────

def spec_signature(codec: str, width: int, height: int, fps: str, pix_fmt: str) -> str:
    """Stable grouping key: clips sharing a signature can be concat-copied onto
    one archival track. Groups by the params that must match for a stream-copy
    concat to stay valid (codec, resolution, frame rate, pixel format) — which
    in practice means "same camera/format"."""
    return "|".join((
        (codec or "?").lower(),
        f"{int(width)}x{int(height)}",
        fps or "?",
        (pix_fmt or "?").lower(),
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


# ── JSON (de)serialisation ─────────────────────────────────────────────────────

_CLIP_FIELDS = {f.name for f in fields(ClipEntry)}


def _manifest_to_dict(m: Manifest) -> dict:
    return {
        "version": m.version,
        "master_filename": m.master_filename,
        "created_utc": m.created_utc,
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
