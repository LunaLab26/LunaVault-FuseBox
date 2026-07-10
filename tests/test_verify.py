"""Tests for core.verify's pure prediction logic — predict_unverifiable.

Uses lightweight SimpleNamespace fakes for ClipEntry/RecoveryPlan (duck-typed,
only the fields predict_unverifiable actually reads). Standalone, no ffmpeg.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import core.verify as verify_mod  # noqa: E402
from core.verify import predict_unverifiable, _PREDICTED_PREFIX  # noqa: E402


def _entry(conform_status="ok", has_camera_audio=True):
    return SimpleNamespace(conform_status=conform_status, has_camera_audio=has_camera_audio)


def _plan(video_stream=0, audio_stream=1, video_start=0.0):
    return SimpleNamespace(video_stream=video_stream, audio_stream=audio_stream,
                           video_start=video_start)


def test_conforming_first_clip_predicts_nothing():
    predicted = predict_unverifiable(_entry(), _plan(video_start=0.0), False, False)
    assert predicted == {}
    print("ok: test_conforming_first_clip_predicts_nothing")


def test_transcoded_with_no_archival_track_predicts_video():
    entry = _entry(conform_status="transcode")
    plan = _plan(video_stream=0)   # baseline stream — no archival track backs this clip up
    predicted = predict_unverifiable(entry, plan, own_archival_track=False, safe_to_read_unbounded=False)
    assert "Video" in predicted
    assert "re-encoded straight into the shared baseline" in predicted["Video"]
    print("ok: test_transcoded_with_no_archival_track_predicts_video")


def test_transcoded_with_own_archival_track_does_not_predict_video():
    # video_stream != 0 means an archival track backs this clip — a real,
    # byte-exact copy exists to compare against, so it's worth attempting.
    entry = _entry(conform_status="transcode")
    plan = _plan(video_stream=2)
    predicted = predict_unverifiable(entry, plan, own_archival_track=True, safe_to_read_unbounded=True)
    assert "Video" not in predicted
    print("ok: test_transcoded_with_own_archival_track_does_not_predict_video")


def test_first_clip_camera_audio_not_predicted_even_on_shared_track():
    entry = _entry()
    plan = _plan(video_start=0.0)   # nothing precedes it on the track
    predicted = predict_unverifiable(entry, plan, own_archival_track=False, safe_to_read_unbounded=False)
    assert "Camera audio" not in predicted
    print("ok: test_first_clip_camera_audio_not_predicted_even_on_shared_track")


def test_non_first_clip_on_shared_track_predicts_camera_audio():
    entry = _entry()
    plan = _plan(video_start=123.4)
    predicted = predict_unverifiable(entry, plan, own_archival_track=False, safe_to_read_unbounded=False)
    assert "Camera audio" in predicted
    assert "mid-way in a shared archival track" in predicted["Camera audio"]
    print("ok: test_non_first_clip_on_shared_track_predicts_camera_audio")


def test_non_first_clip_safe_to_read_unbounded_not_predicted():
    # Its own bit-exact archival track — genuinely verifiable, not doomed.
    entry = _entry()
    plan = _plan(video_start=123.4)
    predicted = predict_unverifiable(entry, plan, own_archival_track=True, safe_to_read_unbounded=True)
    assert "Camera audio" not in predicted
    print("ok: test_non_first_clip_safe_to_read_unbounded_not_predicted")


def test_no_camera_audio_never_predicted():
    entry = _entry(has_camera_audio=False)
    plan = _plan(video_start=123.4)
    predicted = predict_unverifiable(entry, plan, own_archival_track=False, safe_to_read_unbounded=False)
    assert "Camera audio" not in predicted
    print("ok: test_no_camera_audio_never_predicted")


def test_no_audio_stream_never_predicted():
    entry = _entry(has_camera_audio=True)
    plan = _plan(video_start=123.4, audio_stream=None)
    predicted = predict_unverifiable(entry, plan, own_archival_track=False, safe_to_read_unbounded=False)
    assert "Camera audio" not in predicted
    print("ok: test_no_audio_stream_never_predicted")


def test_predicted_prefix_is_a_stable_string():
    # skip_note/log-counting logic elsewhere matches on this exact prefix.
    assert _PREDICTED_PREFIX == "predicted unverifiable"
    print("ok: test_predicted_prefix_is_a_stable_string")


# ── quick_video_rounding_check / quick_wav_rounding_check ────────────────────
# These call ffmpeg indirectly through module-level _run_framemd5/decoded_md5;
# tests monkeypatch those module globals (restored in `finally`) rather than
# invoking real ffmpeg, matching test_seam_diag.py's hash-list fixtures.

def test_quick_video_rounding_check_confirms_benign_window_rounding():
    fps, lead_s = 30.0, 2.0
    expected = int(round(lead_s * fps))
    orig_head = [f"h{i}" for i in range(20)]
    mast_head = [f"pre{i}" for i in range(expected + 1)] + orig_head + ["pad"]  # +1 frame shift
    orig_tail = [f"t{i}" for i in range(20)]
    tail_expected = int(round(2.0 * fps))
    mast_tail = [f"pre{i}" for i in range(tail_expected)] + orig_tail + ["pad"]  # exact match

    seq = iter([orig_head, mast_head, orig_tail, mast_tail])
    orig_fn = verify_mod._run_framemd5
    verify_mod._run_framemd5 = lambda *a, **k: next(seq)
    try:
        benign, detail = verify_mod.quick_video_rounding_check(
            "ffmpeg", "src.mp4", "master.mov", clip_start=100.0, clip_duration=10.0,
            fps=fps, video_stream=0, window_s=3.0, lead_s=lead_s)
    finally:
        verify_mod._run_framemd5 = orig_fn
    assert benign is True
    assert "head" in detail and "tail" in detail
    print("ok: test_quick_video_rounding_check_confirms_benign_window_rounding")


def test_quick_video_rounding_check_rejects_genuine_divergence():
    orig_head = [f"h{i}" for i in range(20)]
    mast_head = [f"other{i}" for i in range(40)]   # no alignment anywhere
    seq = iter([orig_head, mast_head])
    orig_fn = verify_mod._run_framemd5
    verify_mod._run_framemd5 = lambda *a, **k: next(seq)
    try:
        # clip_duration <= window_s + 1 → tail check skipped, only 2 calls needed
        benign, detail = verify_mod.quick_video_rounding_check(
            "ffmpeg", "src.mp4", "master.mov", clip_start=100.0, clip_duration=3.0,
            fps=30.0, window_s=3.0, lead_s=2.0)
    finally:
        verify_mod._run_framemd5 = orig_fn
    assert benign is False
    print("ok: test_quick_video_rounding_check_rejects_genuine_divergence")


def test_quick_video_rounding_check_decode_error_is_not_benign():
    def boom(*a, **k):
        raise RuntimeError("decode failed")
    orig_fn = verify_mod._run_framemd5
    verify_mod._run_framemd5 = boom
    try:
        benign, detail = verify_mod.quick_video_rounding_check(
            "ffmpeg", "src.mp4", "master.mov", clip_start=100.0, clip_duration=3.0, fps=30.0)
    finally:
        verify_mod._run_framemd5 = orig_fn
    assert benign is False and "couldn't decode" in detail
    print("ok: test_quick_video_rounding_check_decode_error_is_not_benign")


def test_quick_wav_rounding_check_finds_shifted_match():
    calls = []

    def fake_decoded_md5(cmd, **kwargs):
        calls.append(cmd)
        return "srchash" if len(calls) in (1, 3) else "other"   # matches on the +15ms shift

    orig_fn = verify_mod.decoded_md5
    verify_mod.decoded_md5 = fake_decoded_md5
    try:
        benign, detail = verify_mod.quick_wav_rounding_check(
            "ffmpeg", "src.wav", "master.mov", wav_start=50.0, wav_stream=1,
            step_ms=15.0, max_shift_ms=60.0)
    finally:
        verify_mod.decoded_md5 = orig_fn
    assert benign is True
    assert "+15ms" in detail
    print("ok: test_quick_wav_rounding_check_finds_shifted_match")


def test_quick_wav_rounding_check_no_match_found():
    calls = []

    def fake_decoded_md5(cmd, **kwargs):
        calls.append(cmd)
        return "srchash" if len(calls) == 1 else "nomatch"   # every shift attempt disagrees

    orig_fn = verify_mod.decoded_md5
    verify_mod.decoded_md5 = fake_decoded_md5
    try:
        benign, detail = verify_mod.quick_wav_rounding_check(
            "ffmpeg", "src.wav", "master.mov", wav_start=50.0, wav_stream=1,
            step_ms=15.0, max_shift_ms=45.0)
    finally:
        verify_mod.decoded_md5 = orig_fn
    assert benign is False
    print("ok: test_quick_wav_rounding_check_no_match_found")


def test_quick_wav_rounding_check_source_decode_failure_is_not_benign():
    orig_fn = verify_mod.decoded_md5
    verify_mod.decoded_md5 = lambda cmd, **kwargs: ""
    try:
        benign, detail = verify_mod.quick_wav_rounding_check(
            "ffmpeg", "src.wav", "master.mov", wav_start=50.0, wav_stream=1)
    finally:
        verify_mod.decoded_md5 = orig_fn
    assert benign is False
    print("ok: test_quick_wav_rounding_check_source_decode_failure_is_not_benign")


# ── probe_audio_stream_count ─────────────────────────────────────────────────

def test_probe_audio_stream_count_counts_streams():
    import json as json_mod
    from types import SimpleNamespace

    def fake_run(cmd, **kwargs):
        return SimpleNamespace(stdout=json_mod.dumps(
            {"streams": [{"index": 1}, {"index": 2}, {"index": 4}]}))

    orig_fn = verify_mod.subprocess.run
    verify_mod.subprocess.run = fake_run
    try:
        n = verify_mod.probe_audio_stream_count("ffprobe", "master.mov")
    finally:
        verify_mod.subprocess.run = orig_fn
    assert n == 3
    print("ok: test_probe_audio_stream_count_counts_streams")


def test_probe_audio_stream_count_error_is_zero():
    def fake_run(cmd, **kwargs):
        raise OSError("no ffprobe")

    orig_fn = verify_mod.subprocess.run
    verify_mod.subprocess.run = fake_run
    try:
        n = verify_mod.probe_audio_stream_count("ffprobe", "master.mov")
    finally:
        verify_mod.subprocess.run = orig_fn
    assert n == 0
    print("ok: test_probe_audio_stream_count_error_is_zero")


# ── probe_video_stream_count ─────────────────────────────────────────────────

def test_probe_video_stream_count_counts_streams():
    import json as json_mod
    from types import SimpleNamespace

    def fake_run(cmd, **kwargs):
        return SimpleNamespace(stdout=json_mod.dumps(
            {"streams": [{"index": 0}, {"index": 3}]}))

    orig_fn = verify_mod.subprocess.run
    verify_mod.subprocess.run = fake_run
    try:
        n = verify_mod.probe_video_stream_count("ffprobe", "master.mov")
    finally:
        verify_mod.subprocess.run = orig_fn
    assert n == 2
    print("ok: test_probe_video_stream_count_counts_streams")


def test_probe_video_stream_count_error_is_zero():
    def fake_run(cmd, **kwargs):
        raise OSError("no ffprobe")

    orig_fn = verify_mod.subprocess.run
    verify_mod.subprocess.run = fake_run
    try:
        n = verify_mod.probe_video_stream_count("ffprobe", "master.mov")
    finally:
        verify_mod.subprocess.run = orig_fn
    assert n == 0
    print("ok: test_probe_video_stream_count_error_is_zero")


if __name__ == "__main__":
    test_conforming_first_clip_predicts_nothing()
    test_transcoded_with_no_archival_track_predicts_video()
    test_transcoded_with_own_archival_track_does_not_predict_video()
    test_first_clip_camera_audio_not_predicted_even_on_shared_track()
    test_non_first_clip_on_shared_track_predicts_camera_audio()
    test_non_first_clip_safe_to_read_unbounded_not_predicted()
    test_no_camera_audio_never_predicted()
    test_no_audio_stream_never_predicted()
    test_predicted_prefix_is_a_stable_string()
    test_quick_video_rounding_check_confirms_benign_window_rounding()
    test_quick_video_rounding_check_rejects_genuine_divergence()
    test_quick_video_rounding_check_decode_error_is_not_benign()
    test_quick_wav_rounding_check_finds_shifted_match()
    test_quick_wav_rounding_check_no_match_found()
    test_quick_wav_rounding_check_source_decode_failure_is_not_benign()
    test_probe_audio_stream_count_counts_streams()
    test_probe_audio_stream_count_error_is_zero()
    test_probe_video_stream_count_counts_streams()
    test_probe_video_stream_count_error_is_zero()
    print("test_verify: all tests passed")
