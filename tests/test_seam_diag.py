"""Tests for core/seam_diag.py — framemd5 parsing + seam/rounding classification.
Pure, standalone."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.seam_diag import (  # noqa: E402
    parse_framemd5, classify_window, SeamVerdict,
    VERDICT_MATCH, VERDICT_OFFSET, VERDICT_SEAM, VERDICT_DIVERGENT, VERDICT_NO_DATA)


def _h(i):
    return f"hash{i:04d}"


def test_parse_framemd5_skips_comments_and_blank_lines():
    text = (
        "#format: frame checksums\n"
        "#version: 2\n"
        "#stream#, dts,        pts, duration,     size, hash\n"
        "0,          0,          0,        1,   518400, aaaa1111\n"
        "0,          1,          1,        1,   518400, bbbb2222\n"
        "\n"
        "0,          2,          2,        1,   518400, cccc3333\n")
    assert parse_framemd5(text) == ["aaaa1111", "bbbb2222", "cccc3333"]
    assert parse_framemd5("") == []
    assert parse_framemd5(None) == []


def test_exact_match_at_expected_offset():
    orig = [_h(i) for i in range(100)]
    master = [_h(1000 + i) for i in range(30)] + orig + [_h(2000)]
    v = classify_window(orig, master, expected_offset=30)
    assert v.verdict == VERDICT_MATCH
    assert v.offset == 30 and v.matched_frames == 100


def test_shifted_window_is_rounding_not_seam():
    orig = [_h(i) for i in range(100)]
    # the original appears 2 frames LATER than modelled → window rounding
    master = [_h(1000 + i) for i in range(32)] + orig + [_h(2000)]
    v = classify_window(orig, master, expected_offset=30)
    assert v.verdict == VERDICT_OFFSET
    assert v.shift_frames == 2


def test_damaged_head_with_intact_tail_is_seam_damage():
    orig = [_h(i) for i in range(100)]
    # 7 corrupted frames where the head should be, then a perfect tail
    master = ([_h(1000 + i) for i in range(30)]
              + [f"corrupt{i}" for i in range(7)] + orig[7:] + [_h(2000)])
    v = classify_window(orig, master, expected_offset=30)
    assert v.verdict == VERDICT_SEAM
    assert v.damaged_frames == 7 and v.matched_frames == 93
    assert v.offset == 30   # aligned at the modelled position, head damaged


def test_seam_damage_with_window_shift_reports_both():
    orig = [_h(i) for i in range(100)]
    master = ([_h(1000 + i) for i in range(29)]     # window lands 1 frame early
              + [f"corrupt{i}" for i in range(5)] + orig[5:])
    v = classify_window(orig, master, expected_offset=30)
    assert v.verdict == VERDICT_SEAM
    assert v.damaged_frames == 5
    assert v.offset == 29 and v.shift_frames == -1


def test_genuinely_different_content_is_divergent():
    orig = [_h(i) for i in range(100)]
    master = [f"other{i}" for i in range(160)]
    v = classify_window(orig, master, expected_offset=30)
    assert v.verdict == VERDICT_DIVERGENT


def test_mostly_damaged_is_divergent_not_over_explained():
    orig = [_h(i) for i in range(100)]
    # only the last 20 frames align (< min_tail_fraction 0.5) — do not call it seam damage
    master = [f"corrupt{i}" for i in range(80)] + orig[80:]
    v = classify_window(orig, master, expected_offset=0)
    assert v.verdict == VERDICT_DIVERGENT


def test_empty_sides_are_no_data():
    assert classify_window([], ["x"], 0).verdict == VERDICT_NO_DATA
    assert classify_window(["x"], [], 0).verdict == VERDICT_NO_DATA


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("test_seam_diag: all tests passed")
