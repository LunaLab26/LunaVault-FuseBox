"""Tests for core/manifest.py — the archival master's clip manifest.

Runs under pytest, and also standalone (`python tests/test_manifest.py`) since
pytest isn't always installed — same pattern as tests/test_theme.py. The
embed-then-reparse integration test needs the bundled ffmpeg/ffprobe and is
skipped if they're absent.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from core.manifest import (  # noqa: E402
    ClipEntry, Manifest, MANIFEST_METADATA_KEY,
    spec_signature, group_nonconforming_by_spec, assign_in_track_offsets,
    assign_archival_locations,
    to_json, from_json, sidecar_path, write_sidecar, metadata_embed_args,
    parse_from_format_tags, read_manifest,
)
from core.binaries import get_ffmpeg  # noqa: E402


def _sample_manifest() -> Manifest:
    return Manifest(
        master_filename="pool_day.mov",
        created_utc="2026-07-03T00:00:00+00:00",
        clips=[
            ClipEntry(source_filename="VID_0001.mp4", container="mp4", codec="hevc",
                      width=3840, height=2160, fps="30000/1001", pix_fmt="yuv420p10le",
                      bit_depth=10, duration=12.5, size_bytes=90_000_000,
                      conform_status="ok", baseline_chapter_index=0),
            ClipEntry(source_filename="MOBILE_9987.mp4", container="mp4", codec="h264",
                      width=1920, height=1080, fps="30/1", pix_fmt="yuv420p",
                      bit_depth=8, duration=8.0, size_bytes=20_000_000,
                      conform_status="transcode",
                      spec_group=spec_signature("h264", 1920, 1080, "30/1", "yuv420p")),
            ClipEntry(source_filename="MOBILE_9988.mp4", container="mp4", codec="h264",
                      width=1920, height=1080, fps="30/1", pix_fmt="yuv420p",
                      bit_depth=8, duration=5.0, size_bytes=13_000_000,
                      conform_status="transcode",
                      spec_group=spec_signature("h264", 1920, 1080, "30/1", "yuv420p")),
        ],
    )


def test_json_round_trip_preserves_every_field():
    m = _sample_manifest()
    back = from_json(to_json(m))
    assert back.version == m.version
    assert back.master_filename == m.master_filename
    assert back.created_utc == m.created_utc
    assert len(back.clips) == len(m.clips)
    for a, b in zip(m.clips, back.clips):
        assert a == b, f"clip mismatch:\n  {a}\n  {b}"


def test_compact_json_has_no_newlines_for_embedding():
    # The embedded metadata value must be a single line (MOV tags can't hold
    # arbitrary newlines reliably).
    compact = to_json(_sample_manifest(), indent=None)
    assert "\n" not in compact
    assert from_json(compact).clips[1].source_filename == "MOBILE_9987.mp4"


def test_spec_signature_stable_and_discriminating():
    a = spec_signature("h264", 1920, 1080, "30/1", "yuv420p")
    b = spec_signature("H264", 1920, 1080, "30/1", "yuv420p")   # case-insensitive
    c = spec_signature("hevc", 3840, 2160, "30000/1001", "yuv420p10le")
    assert a == b
    assert a != c


def test_group_nonconforming_skips_baseline_clips():
    groups = group_nonconforming_by_spec(_sample_manifest().clips)
    # Only the two 1080p transcodes group; the conforming 4K clip is excluded.
    assert len(groups) == 1
    (sig, entries), = groups.items()
    assert sig == spec_signature("h264", 1920, 1080, "30/1", "yuv420p")
    assert [e.source_filename for e in entries] == ["MOBILE_9987.mp4", "MOBILE_9988.mp4"]


def test_assign_in_track_offsets_is_cumulative():
    entries = [c for c in _sample_manifest().clips if c.conform_status != "ok"]
    assign_in_track_offsets(entries)
    assert entries[0].in_track_start == 0.0
    assert entries[0].in_track_duration == 8.0
    assert entries[1].in_track_start == 8.0          # starts where clip 0 ended
    assert entries[1].in_track_duration == 5.0


def test_assign_archival_locations_matches_mux_stream_order():
    # Two spec groups, both with audio; baseline holds 1 video + 2 audio (camera, wav).
    g1 = [ClipEntry(source_filename="B1.mp4", duration=8.0, has_camera_audio=True,
                    conform_status="transcode", spec_group="g1"),
          ClipEntry(source_filename="B2.mp4", duration=5.0, has_camera_audio=True,
                    conform_status="transcode", spec_group="g1")]
    g2 = [ClipEntry(source_filename="C1.mov", duration=4.0, has_camera_audio=True,
                    conform_status="transcode", spec_group="g2")]
    v, a = assign_archival_locations([g1, g2], base_video_count=1, base_audio_count=2)
    # group 1 -> video stream 1, audio stream 2 ; group 2 -> video 2, audio 3
    assert g1[0].archival_track == 1 and g1[0].archival_audio_stream == 2
    assert g1[1].archival_track == 1 and g1[1].archival_audio_stream == 2
    assert g1[1].in_track_start == 8.0               # concatenated after B1
    assert g2[0].archival_track == 2 and g2[0].archival_audio_stream == 3
    assert (v, a) == (3, 4)


def test_assign_archival_locations_skips_audio_index_for_silent_group():
    g1 = [ClipEntry(source_filename="B1.mp4", duration=3.0, has_camera_audio=False,
                    conform_status="transcode", spec_group="g1")]        # no audio
    g2 = [ClipEntry(source_filename="C1.mov", duration=3.0, has_camera_audio=True,
                    conform_status="transcode", spec_group="g2")]
    assign_archival_locations([g1, g2], base_video_count=1, base_audio_count=1)
    assert g1[0].archival_track == 1 and g1[0].archival_audio_stream is None
    assert g2[0].archival_track == 2 and g2[0].archival_audio_stream == 1   # first free audio idx


def test_baseline_audio_tracks_round_trips():
    m = _sample_manifest()
    m.baseline_audio_tracks = {"camera": 0, "wav": 1}
    back = from_json(to_json(m))
    assert back.baseline_audio_tracks == {"camera": 0, "wav": 1}


def test_sidecar_path_naming():
    assert sidecar_path("/a/b/pool_day.mov").name == "pool_day.manifest.json"


def test_parse_from_format_tags_variants():
    compact = to_json(_sample_manifest(), indent=None)
    assert parse_from_format_tags({MANIFEST_METADATA_KEY: compact}) is not None
    # MOV sometimes namespaces custom keys — match a suffixed key too.
    assert parse_from_format_tags({f"com.apple.quicktime.{MANIFEST_METADATA_KEY}": compact}) is not None
    assert parse_from_format_tags({}) is None
    assert parse_from_format_tags({"unrelated": "x"}) is None


def test_metadata_embed_args_shape():
    args = metadata_embed_args(_sample_manifest(), is_mov=True)
    assert "-movflags" in args and "use_metadata_tags" in args
    i = args.index("-metadata")
    assert args[i + 1].startswith(f"{MANIFEST_METADATA_KEY}=")
    # Non-MOV output shouldn't force the movflag.
    assert "-movflags" not in metadata_embed_args(_sample_manifest(), is_mov=False)


def test_sidecar_write_and_read(tmp_path=None):
    d = Path(tmp_path) if tmp_path else Path(tempfile.mkdtemp())
    master = d / "pool_day.mov"
    master.write_bytes(b"not a real mov")   # sidecar reader doesn't need a valid mov
    m = _sample_manifest()
    write_sidecar(m, master)
    # No embedded copy in this fake file → reader falls back to the sidecar.
    got = read_manifest("ffprobe-does-not-exist", str(master))
    assert got is not None and len(got.clips) == 3


def _integration_embed_roundtrip() -> bool:
    """Build a tiny real MOV, embed the manifest, read it back via ffprobe."""
    ff, fp = get_ffmpeg()
    if not Path(ff).exists() or not Path(fp).exists():
        print("  (skipped integration: ffmpeg/ffprobe not found)")
        return True
    d = Path(tempfile.mkdtemp())
    master = d / "embedded.mov"
    m = _sample_manifest()
    cmd = [ff, "-y", "-v", "error",
           "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=1",
           "-c:v", "libx264", "-pix_fmt", "yuv420p",
           *metadata_embed_args(m, is_mov=True), str(master)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, f"ffmpeg embed failed:\n{r.stderr[-600:]}"
    got = read_manifest(fp, str(master))
    assert got is not None, "manifest did not survive the MOV mux (embedded read failed)"
    assert len(got.clips) == 3 and got.clips[1].source_filename == "MOBILE_9987.mp4"
    print("  embedded round-trip through a real MOV: OK")
    return True


if __name__ == "__main__":
    test_json_round_trip_preserves_every_field()
    test_compact_json_has_no_newlines_for_embedding()
    test_spec_signature_stable_and_discriminating()
    test_group_nonconforming_skips_baseline_clips()
    test_assign_in_track_offsets_is_cumulative()
    test_assign_archival_locations_matches_mux_stream_order()
    test_assign_archival_locations_skips_audio_index_for_silent_group()
    test_baseline_audio_tracks_round_trips()
    test_sidecar_path_naming()
    test_parse_from_format_tags_variants()
    test_metadata_embed_args_shape()
    test_sidecar_write_and_read()
    _integration_embed_roundtrip()
    print("test_manifest: all tests passed")
