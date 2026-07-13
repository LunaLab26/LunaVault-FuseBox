"""Tests for core.diagnostics — pure command builders + result parsers for
the pre-flight diagnostic checks (container structure, packet timestamps,
stream-copy compatibility, decode-error scans)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core import diagnostics as diag


# ── Catalog ──────────────────────────────────────────────────────────────────

def test_checks_catalog_has_five_checks_three_default_on():
    ids = [c.check_id for c in diag.CHECKS]
    assert ids == ["container", "timestamps", "streamcopy", "quickdecode", "fulldecode"]
    default_on = [c.check_id for c in diag.CHECKS if c.default_on]
    assert set(default_on) == {"container", "timestamps", "streamcopy"}


# ── Container & stream structure ────────────────────────────────────────────

def test_container_probe_cmd_shape():
    cmd = diag.build_container_probe_cmd("ffprobe", "clip.mp4")
    assert cmd[0] == "ffprobe" and cmd[-1] == "clip.mp4"
    assert "-show_entries" in cmd


def test_container_result_clean():
    stdout = json.dumps({"streams": [
        {"codec_type": "video", "codec_name": "hevc", "width": 3840, "height": 2160, "pix_fmt": "yuv420p10le"},
    ]})
    r = diag.parse_container_result(0, stdout, "")
    assert r.verdict == "clean"
    assert "hevc" in r.detail and "3840x2160" in r.detail


def test_container_result_no_video_stream_is_a_problem():
    stdout = json.dumps({"streams": [{"codec_type": "audio", "codec_name": "aac"}]})
    r = diag.parse_container_result(0, stdout, "")
    assert r.verdict == "problem"
    assert "no video" in r.detail.lower()


def test_container_result_missing_fields_is_a_problem():
    stdout = json.dumps({"streams": [{"codec_type": "video", "codec_name": "", "width": 0, "height": 0, "pix_fmt": ""}]})
    r = diag.parse_container_result(0, stdout, "")
    assert r.verdict == "problem"


def test_container_result_probe_failure_is_an_error():
    r = diag.parse_container_result(1, "", "no such file")
    assert r.verdict == "error"


# ── Timestamp & keyframe integrity ──────────────────────────────────────────

def _pkt_csv(rows):
    return "\n".join(f"{pts},{flags}" for pts, flags in rows)


def test_timestamps_clean_even_keyframe_spacing():
    rows = [(i * 0.033, "K__" if i % 60 == 0 else "___") for i in range(180)]
    r = diag.parse_timestamp_result(0, _pkt_csv(rows), "")
    assert r.verdict == "clean"
    assert "3 keyframes" in r.detail


def test_timestamps_flags_out_of_order_dts():
    # DTS (decode order) must be non-decreasing regardless of B-frame PTS
    # reordering -- this shape (a value that drops back down) is a genuine
    # decode-order problem, not the normal B-frame PTS-vs-storage-order
    # reordering that a PTS-based check would have wrongly flagged here.
    rows = [(0.0, "K__"), (0.033, "___"), (0.02, "___"), (0.1, "___")]
    r = diag.parse_timestamp_result(0, _pkt_csv(rows), "")
    assert r.verdict == "warning"
    assert "out-of-order" in r.detail


def test_timestamps_clean_despite_bframe_style_pts_reordering():
    # Confirmed as a real bug this way: an earlier version of this check used
    # PTS (presentation order) instead of DTS (decode order) and flagged
    # every B-frame-containing clip as having "out-of-order timestamps" —
    # PTS legitimately differs from packet/storage order whenever a stream
    # has B-frames (the overwhelming majority of real H.264/HEVC footage).
    # DTS itself, however, is always monotonic here -- this dataset simulates
    # what a real libx264 encode's DTS column looks like: perfectly steady.
    rows = [(i * 0.033, "K__" if i % 60 == 0 else "___") for i in range(120)]
    r = diag.parse_timestamp_result(0, _pkt_csv(rows), "")
    assert r.verdict == "clean"


def test_timestamps_flags_no_keyframes():
    rows = [(i * 0.033, "___") for i in range(30)]
    r = diag.parse_timestamp_result(0, _pkt_csv(rows), "")
    assert r.verdict == "warning"
    assert "no keyframes" in r.detail


def test_timestamps_flags_irregular_keyframe_gaps():
    # keyframes at packet 0, 10, then a huge gap to 200 -> gap ratio > 2x
    flags = ["___"] * 201
    flags[0] = flags[10] = flags[200] = "K__"
    rows = [(i * 0.033, flags[i]) for i in range(201)]
    r = diag.parse_timestamp_result(0, _pkt_csv(rows), "")
    assert r.verdict == "warning"
    assert "irregular keyframe spacing" in r.detail


def test_timestamps_no_packets_is_an_error():
    r = diag.parse_timestamp_result(0, "", "")
    assert r.verdict == "error"


def test_timestamps_probe_failure_is_an_error():
    r = diag.parse_timestamp_result(1, "", "boom")
    assert r.verdict == "error"


# ── Stream-copy compatibility ────────────────────────────────────────────────

def test_annexb_cmd_none_for_unknown_codec():
    assert diag.build_annexb_test_cmd("ffmpeg", "c.mp4", "out.ts", 5.0, "vp9") is None


def test_annexb_cmd_uses_matching_bitstream_filter():
    cmd = diag.build_annexb_test_cmd("ffmpeg", "c.mp4", "out.ts", 5.0, "hevc")
    assert "hevc_mp4toannexb" in cmd
    cmd2 = diag.build_annexb_test_cmd("ffmpeg", "c.mp4", "out.ts", 5.0, "h264")
    assert "h264_mp4toannexb" in cmd2


def test_streamcopy_result_clean():
    r = diag.parse_streamcopy_result(True, "", True, "")
    assert r.verdict == "clean"


def test_streamcopy_result_copy_failure_is_a_problem():
    r = diag.parse_streamcopy_result(False, "Invalid data found", None, "")
    assert r.verdict == "problem"
    assert "Invalid data" in r.detail


def test_streamcopy_result_annexb_failure_is_a_warning_not_a_problem():
    # the plain copy worked -- only the bitstream-filter re-mux failed, a
    # lesser finding than the clip failing to stream-copy at all.
    r = diag.parse_streamcopy_result(True, "", False, "bsf error")
    assert r.verdict == "warning"


def test_streamcopy_result_no_annexb_filter_available_still_clean():
    # e.g. an unrecognised codec -- annexb_ok stays None (skipped, not failed).
    r = diag.parse_streamcopy_result(True, "", None, "")
    assert r.verdict == "clean"


# ── Decode scans ─────────────────────────────────────────────────────────────

def test_sample_windows_short_clip_collapses_overlap():
    windows = diag.sample_windows(3.0, window_secs=5.0)
    assert all(length <= 3.0 for _, length in windows)


def test_sample_windows_long_clip_gives_start_mid_end():
    windows = diag.sample_windows(300.0, window_secs=5.0)
    starts = [s for s, _ in windows]
    assert starts[0] == 0.0
    assert starts[-1] == 295.0   # duration - window length
    assert len(windows) == 3


def test_decode_scan_clean_when_no_stderr():
    r = diag.parse_decode_scan_results("quickdecode", [(0.0, ""), (150.0, ""), (295.0, "")])
    assert r.verdict == "clean"
    assert "3 sampled window(s)" in r.detail


def test_decode_scan_quick_reports_warning_not_problem():
    r = diag.parse_decode_scan_results("quickdecode", [(0.0, ""), (150.0, "corrupt macroblock at 150.2")])
    assert r.verdict == "warning"
    assert "@150s" in r.detail


def test_decode_scan_full_reports_problem_not_warning():
    r = diag.parse_decode_scan_results("fulldecode", [(None, "corrupt macroblock detected")])
    assert r.verdict == "problem"
    assert "the entire clip" not in r.detail   # findings path, not the clean-scope text


def test_decode_scan_full_clean_mentions_entire_clip():
    r = diag.parse_decode_scan_results("fulldecode", [(None, "")])
    assert r.verdict == "clean"
    assert "entire clip" in r.detail


# ── End-to-end worker integration (real ffmpeg, no Qt event loop needed —
# QThread.run() is just a plain method until .start() spins up a real thread) ─

def _integration_real_worker() -> bool:
    import os
    import subprocess
    import tempfile
    from types import SimpleNamespace

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])

    from core.binaries import get_ffmpeg
    from diagnostics_workers import DiagnosticsWorker

    ff, fp = get_ffmpeg()
    if not Path(ff).exists():
        print("  (skipped integration: ffmpeg not found)")
        return True

    d = Path(tempfile.mkdtemp())
    clip_path = d / "clip.mp4"
    subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi",
                   "-i", "testsrc=size=640x360:rate=30:duration=2",
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip_path)], check=True)

    clip = SimpleNamespace(path=clip_path, duration=2.0, stem="clip")
    worker = DiagnosticsWorker(ff, fp, [clip], ["container", "timestamps", "streamcopy"])

    results = {}
    progress_calls = []
    finished = {}
    worker.result_ready.connect(lambda i, r: results.setdefault(i, []).append(r))
    worker.progress.connect(lambda done, total, name: progress_calls.append((done, total, name)))
    worker.finished_all.connect(lambda ok: finished.update(ok=ok))

    worker.run()   # synchronous — no QThread.start()/event loop needed for this check

    assert finished.get("ok") is True
    assert progress_calls and progress_calls[0] == (0, 1, "clip")
    ids = {r.check_id for r in results[0]}
    assert ids == {"container", "timestamps", "streamcopy"}
    for r in results[0]:
        assert r.verdict == "clean", f"{r.check_id}: unexpected {r.verdict} — {r.detail}"
    print("  real DiagnosticsWorker run (3 fast checks on a clean synthetic clip): all clean — OK")
    return True


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"\n{len(fns)} diagnostics tests passed.")
    print("running real ffmpeg integration...")
    _integration_real_worker()
    print("test_diagnostics: all tests passed")
