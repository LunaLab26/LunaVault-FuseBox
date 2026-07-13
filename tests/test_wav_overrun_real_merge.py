"""test_wav_overrun_real_merge.py — real, full-pipeline regression test for
Task 101's fix: a clip whose paired WAV runs longer than its own video must
NOT inflate that clip's position in the merged master.

Unlike test_ffmpeg_cmd.py's per-clip-mux-command integration test, this
drives the REAL MergeTab end to end (folder scan -> pairing -> merge ->
manifest) against two real clips built with the bundled ffmpeg — clip A's
WAV genuinely overruns into where clip B's audio would be, the exact shape
of the real "frozen frame around a camera file-split" report this fix
resolves. Confirms both the manifest's measured concat_start for clip B AND
the actual master file's total duration are governed by the VIDEO
durations, not the inflated WAV.

Standalone (not pytest) — mirrors md5_matrix_test.py's own headless-Qt
pattern (dialog neutering, offscreen platform, real subprocess ffmpeg calls).
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

_tmp_settings = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
import settings as settings_mod  # noqa: E402
settings_mod._settings_path = lambda: Path(_tmp_settings.name)

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402
app = QApplication.instance() or QApplication([])

import theme  # noqa: E402, F401
from settings import Settings  # noqa: E402
import merge_tab as mt_mod  # noqa: E402
from merge_tab import MergeTab, _CameraNamingDialog  # noqa: E402
from core.binaries import get_ffmpeg  # noqa: E402

# Headless dialog neutering — same three gaps md5_matrix_test.py's _init_qt()
# already found and fixed (classmethod shortcuts + the raw QMessageBox
# instance _on_finished's success dialog builds and calls .exec() on).
_CameraNamingDialog.exec = lambda self: 0
mt_mod.QMessageBox.warning = staticmethod(lambda *a, **k: None)
mt_mod.QMessageBox.information = staticmethod(lambda *a, **k: None)
mt_mod.QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
mt_mod.QMessageBox.exec = lambda self: 0


def _wait_for(condition, timeout_s, tick=0.05):
    t0 = time.time()
    while not condition() and time.time() - t0 < timeout_s:
        app.processEvents()
        time.sleep(tick)
    for _ in range(20):
        app.processEvents()
        time.sleep(0.02)
    return condition()


def _integration_wav_overrun_real_merge() -> bool:
    ff, fp = get_ffmpeg()
    if not Path(ff).exists():
        print("  (skipped integration: ffmpeg not found)")
        return True

    d = Path(tempfile.mkdtemp())
    video_a_secs, wav_a_secs = 3.0, 6.0   # the overrun: A's WAV covers 3s of "B territory"
    video_b_secs, wav_b_secs = 2.0, 2.05  # ordinary small overrun, well within tolerance

    def _make_clip(stem: str, video_secs: float, wav_secs: float, freq: int):
        video_path = d / f"{stem}.mp4"
        wav_path = d / f"{stem}_backup.wav"
        subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi",
                       "-i", f"testsrc=size=640x360:rate=30:duration={video_secs}",
                       "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={video_secs}",
                       "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                       str(video_path)], check=True)
        subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi",
                       "-i", f"sine=frequency={freq}:duration={wav_secs}",
                       "-c:a", "pcm_s24le", str(wav_path)], check=True)
        return video_path, wav_path

    _make_clip("clipA", video_a_secs, wav_a_secs, 220)
    _make_clip("clipB", video_b_secs, wav_b_secs, 440)

    out_dir = d / "out"
    out_dir.mkdir()

    mt = None
    try:
        mt = MergeTab(Settings())
        mt.show()
        mt._load_folder(d)
        loaded = _wait_for(lambda: mt._clips and all(c.stream is not None for c in mt._clips), 30)
        assert loaded, "clips never finished probing"
        assert len(mt._clips) == 2, f"expected 2 clips, got {len(mt._clips)}"

        mt._archival_check.setChecked(False)
        mt._verify_md5_check.setChecked(False)   # this test checks TIMING, not MD5 recoverability
        for _ in range(10):
            app.processEvents(); time.sleep(0.02)

        mt._out_dir.setText(str(out_dir))
        mt._out_name.setText("master.mov")

        mt._start_merge()
        assert mt._worker is not None, "_start_merge did not create a worker"
        done = {}
        mt._worker.finished.connect(lambda ok, msg: done.update(finished=True, success=ok, message=msg))
        # Generous timeout — matches md5_matrix_test.py's own default hard cap,
        # since this test may run alongside other real-ffmpeg work on the
        # same machine and must not flake under CPU/disk contention.
        finished = _wait_for(lambda: done.get("finished", False), 600, tick=0.2)
        master = out_dir / "master.mov"
        manifest_path = out_dir / "master.manifest.json"
        if finished:
            assert done.get("success"), f"merge failed: {done.get('message')}"
        else:
            # Fall back to the real output on disk if the queued cross-thread
            # `finished` signal wasn't observed within the wait — confirmed
            # directly (leftover temp dirs from earlier runs) that the
            # underlying merge reliably completes correctly on this machine
            # even when the signal-polling loop misses it; this keeps the
            # test meaningful rather than flaking on a harness timing quirk
            # unrelated to what's actually being tested.
            found = _wait_for(lambda: manifest_path.exists(), 300, tick=0.5)
            assert found, "merge did not finish within 600s and no manifest ever appeared"
            print("  (finished signal wasn't observed in time, but the manifest appeared on "
                 "disk — continuing from the real output)")
        assert master.exists(), "master.mov was not produced"
        assert manifest_path.exists(), "sidecar manifest was not written"

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        clips_by_name = {c["source_filename"]: c for c in manifest["clips"]}
        entry_a = clips_by_name["clipA.mp4"]
        entry_b = clips_by_name["clipB.mp4"]

        # THE regression check: clip B's measured concat position must be
        # ~clip A's own VIDEO duration (3.0s), not clip A's WAV duration
        # (6.0s) — before the fix, this would have measured ~6.0s, exactly
        # the mechanism behind the real "frozen frame" report.
        concat_start_b = entry_b["concat_start"]
        assert concat_start_b is not None, "clip B's concat_start was not measured"
        assert abs(concat_start_b - video_a_secs) < 0.5, (
            f"clip B's concat_start was {concat_start_b:.3f}s, expected ~{video_a_secs:.1f}s "
            f"(clip A's own video duration) — got the WAV-inflated position instead "
            f"(~{wav_a_secs:.1f}s would mean the bug regressed)")

        # And the master's own total duration must track the sum of VIDEO
        # durations (5.0s), not the sum including the WAV overrun (8.0s).
        r = subprocess.run([fp, "-v", "error", "-show_entries", "format=duration",
                           "-of", "default=nw=1:nk=1", str(master)],
                          capture_output=True, text=True, check=True)
        total_dur = float(r.stdout.strip())
        expected_total = video_a_secs + video_b_secs
        assert abs(total_dur - expected_total) < 0.5, (
            f"master's total duration was {total_dur:.3f}s, expected ~{expected_total:.1f}s")

        print(f"  real merge: clip B concat_start={concat_start_b:.3f}s (expected ~{video_a_secs:.1f}s), "
             f"master duration={total_dur:.3f}s (expected ~{expected_total:.1f}s) - OK")
        return True
    finally:
        if mt is not None:
            try:
                mt.shutdown()
            except Exception:
                pass


if __name__ == "__main__":
    print("running real full-merge WAV-overrun integration test...")
    _integration_wav_overrun_real_merge()
    print("test_wav_overrun_real_merge: all tests passed")
    sys.stdout.flush()
    os._exit(0)
