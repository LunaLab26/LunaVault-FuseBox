"""Tests for probe.py's multi-track audio enumeration and pix_fmt badge helper
(the Review tab's per-track labels and colour-depth badges)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from probe import parse_audio_tracks, pix_fmt_info, AudioTrackInfo


def _raw_streams(streams):
    return {"streams": streams}


def test_parse_audio_tracks_indexes_audio_only_streams():
    raw = _raw_streams([
        {"codec_type": "video", "codec_name": "hevc"},
        {"codec_type": "audio", "codec_name": "aac", "channels": 2, "sample_rate": "48000",
         "tags": {"handler_name": "SoundHandler"}},
        {"codec_type": "data", "codec_name": "bin_data"},
        {"codec_type": "audio", "codec_name": "alac", "channels": 2, "sample_rate": "48000",
         "bits_per_raw_sample": "24", "tags": {"handler_name": "SoundHandler"}},
        {"codec_type": "audio", "codec_name": "aac", "channels": 2, "sample_rate": "48000"},
    ])
    tracks = parse_audio_tracks(raw)
    assert len(tracks) == 3
    # audio_index is 0-based among AUDIO streams only, skipping video/data
    assert [t.audio_index for t in tracks] == [0, 1, 2]
    assert tracks[0].codec == "aac"
    assert tracks[1].codec == "alac"
    assert tracks[1].bit_depth == 24
    assert tracks[2].codec == "aac"


def test_parse_audio_tracks_handles_no_audio_streams():
    raw = _raw_streams([{"codec_type": "video", "codec_name": "hevc"}])
    assert parse_audio_tracks(raw) == []


def test_parse_audio_tracks_falls_back_gracefully_on_missing_fields():
    raw = _raw_streams([{"codec_type": "audio"}])
    tracks = parse_audio_tracks(raw)
    assert len(tracks) == 1
    t = tracks[0]
    assert t.codec == "" and t.channels == 0 and t.sample_rate == 0 and t.bit_depth == 0
    assert t.title == "" and t.language == ""


def test_parse_audio_tracks_title_prefers_title_tag_over_handler_name():
    raw = _raw_streams([
        {"codec_type": "audio", "tags": {"title": "Camera mic", "handler_name": "SoundHandler"}},
    ])
    assert parse_audio_tracks(raw)[0].title == "Camera mic"


def test_pix_fmt_info_known_formats():
    assert pix_fmt_info("yuv420p10le") == (10, "4:2:0")
    assert pix_fmt_info("yuv420p") == (8, "4:2:0")
    assert pix_fmt_info("yuv444p10le") == (10, "4:4:4")


def test_pix_fmt_info_sniffs_unknown_formats():
    depth, label = pix_fmt_info("some_weird_fmt12le")
    assert depth == 12
    depth8, _ = pix_fmt_info("totally_unrecognized")
    assert depth8 == 8   # safe fallback


def test_audio_track_info_is_a_plain_dataclass():
    t = AudioTrackInfo(audio_index=0, codec="aac")
    assert t.channels == 0
    assert t.bit_depth == 0


if __name__ == "__main__":
    test_parse_audio_tracks_indexes_audio_only_streams()
    test_parse_audio_tracks_handles_no_audio_streams()
    test_parse_audio_tracks_falls_back_gracefully_on_missing_fields()
    test_parse_audio_tracks_title_prefers_title_tag_over_handler_name()
    test_pix_fmt_info_known_formats()
    test_pix_fmt_info_sniffs_unknown_formats()
    test_audio_track_info_is_a_plain_dataclass()
    print("test_probe_tracks: all tests passed")
