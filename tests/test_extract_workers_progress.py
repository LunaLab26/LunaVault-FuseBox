"""Tests for ExtractWorker/GenericExtractWorker's byte-weighted progress/ETA
plumbing (_estimate_expected_total_bytes) — the Extract-tab half of the
conservative-ETA/GB-readout work (see core.eta for the shared algorithm)."""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication([])

from core.manifest import Manifest, ClipEntry
from core.extract import GenericRecoveryPlan, build_generic_recovery_plans
from extract_workers import ExtractWorker, GenericExtractWorker, _WAV_BYTES_PER_SEC
from probe import ChapterInfo


def _manifest_with_clip(size_bytes=100000, has_wav=False, wav_dur=10.0,
                        codec="aac", container="native"):
    entry = ClipEntry(source_filename="A.mp4", duration=10.0, conform_status="ok",
                      baseline_chapter_index=0, has_camera_audio=True, has_wav=has_wav,
                      size_bytes=size_bytes, original_audio_codec=codec)
    m = Manifest(master_filename="master.mov",
                baseline_audio_tracks={"camera": 0, "wav": 1} if has_wav else {"camera": 0},
                clips=[entry])
    return m, entry


def test_extract_worker_expected_total_is_size_bytes_without_wav():
    m, entry = _manifest_with_clip(size_bytes=123456, has_wav=False)
    w = ExtractWorker("ffmpeg", "master.mov", m, [entry], Path("out"))
    assert w._estimate_expected_total_bytes() == 123456


def test_extract_worker_expected_total_adds_wav_estimate():
    m, entry = _manifest_with_clip(size_bytes=100000, has_wav=True, wav_dur=10.0)
    w = ExtractWorker("ffmpeg", "master.mov", m, [entry], Path("out"))
    total = w._estimate_expected_total_bytes()
    # video bytes + ~10s of 48kHz/24-bit/stereo PCM
    assert total == 100000 + int(10.0 * _WAV_BYTES_PER_SEC)


def test_extract_worker_expected_total_adds_camera_audio_split_for_mp4_incompatible_codec():
    m, entry = _manifest_with_clip(size_bytes=100000, has_wav=False, codec="pcm_s24le")
    w = ExtractWorker("ffmpeg", "master.mov", m, [entry], Path("out"), container="mp4")
    total = w._estimate_expected_total_bytes()
    # PCM camera audio can't go in MP4 -> split out as its own WAV, adding a
    # second PCM-duration estimate on top of the base video bytes.
    assert total == 100000 + int(10.0 * _WAV_BYTES_PER_SEC)


def test_extract_worker_expected_total_never_zero():
    m = Manifest(master_filename="master.mov", baseline_audio_tracks={})
    w = ExtractWorker("ffmpeg", "master.mov", m, [], Path("out"))
    assert w._estimate_expected_total_bytes() >= 1


def test_run_cmd_tracks_real_output_file_into_produced_bytes_base():
    # End-to-end _run_cmd exercise with no ffmpeg dependency: any executable
    # works (see core.binaries.no_window), so use the Python interpreter
    # itself to write a real, sized file and confirm _run_cmd's poll loop
    # both succeeds and folds the file's real final size into
    # _produced_bytes_base — the same accumulation MergeWorker._metrics
    # relies on, exercised here via the actual subprocess path.
    m, entry = _manifest_with_clip()
    w = ExtractWorker("ffmpeg", "master.mov", m, [entry], Path("out"))
    w._expected_total_bytes = 1000
    with tempfile.TemporaryDirectory() as td:
        out_path = Path(td) / "made.bin"
        cmd = [sys.executable, "-c",
              f"open(r'{out_path}', 'wb').write(b'x' * 777)"]
        ok = w._run_cmd(cmd, out_path)
        assert ok is True
        assert out_path.stat().st_size == 777
        assert w._produced_bytes_base == 777

        # A subsequent failed command (a nonzero exit) must NOT add anything.
        fail_cmd = [sys.executable, "-c", "import sys; sys.exit(1)"]
        missing_out = Path(td) / "never_created.bin"
        ok2 = w._run_cmd(fail_cmd, missing_out)
        assert ok2 is False
        assert w._produced_bytes_base == 777   # unchanged


def test_generic_extract_worker_expected_total_proportional_to_duration():
    with tempfile.TemporaryDirectory() as td:
        master = Path(td) / "master.mov"
        master.write_bytes(b"m" * 10000)
        chapters_plans = build_generic_recovery_plans(
            [ChapterInfo(start=0.0, end=4.0, title="a"),
             ChapterInfo(start=4.0, end=10.0, title="b")],
            audio_track_indices=[0])
        w = GenericExtractWorker("ffmpeg", str(master), chapters_plans, Path(td) / "out")
        total = w._estimate_expected_total_bytes()
        # Plan "a" is 4/10 of total duration, "b" is 6/10 -> proportional split
        # of the master's own 10000-byte size.
        assert total == int(10000 * 0.4) + int(10000 * 0.6)


def test_generic_extract_worker_expected_total_adds_wav_role_estimate():
    plan_with_wav = GenericRecoveryPlan(title="a", index=0, start=0.0, duration=5.0,
                                        camera_id="", camera_label="",
                                        audio_stream=0, wav_stream=1)
    with tempfile.TemporaryDirectory() as td:
        master = Path(td) / "master.mov"
        master.write_bytes(b"m" * 5000)
        w = GenericExtractWorker("ffmpeg", str(master), [plan_with_wav], Path(td) / "out")
        total = w._estimate_expected_total_bytes()
        assert total == 5000 + int(5.0 * _WAV_BYTES_PER_SEC)


def test_generic_extract_worker_expected_total_falls_back_when_master_missing():
    w = GenericExtractWorker("ffmpeg", "does_not_exist.mov",
                             [GenericRecoveryPlan(title="a", index=0, start=0.0, duration=1.0,
                                                  camera_id="", camera_label="")],
                             Path("out"))
    assert w._estimate_expected_total_bytes() >= 1


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"\n{len(fns)} extract-workers-progress tests passed.")
    sys.stdout.flush()
    os._exit(0)
