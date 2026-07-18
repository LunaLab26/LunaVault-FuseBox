"""Tests for preflight_dialog.py — the "what will the merge do?" breakdown.

Offscreen Qt. The embedded "Show me"-style diagram (Task 92-3) was tried and
then reverted per user feedback (not helpful, didn't look good) — these tests
now just cover the summary band + per-clip cards that remain.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from PySide6.QtWidgets import QApplication  # noqa: E402
app = QApplication.instance() or QApplication([])

import theme  # noqa: E402
from settings import Settings  # noqa: E402
theme.init_controller(app, Settings())

from core.plan_report import MergeReport, ClipReport, TrackPlan  # noqa: E402
from preflight_dialog import PreflightDialog  # noqa: E402


def _empty_report():
    return MergeReport(clips=[], total_bytes=0, best_secs=0, worst_secs=0,
                       n_transcode=0, n_slowmo=0, n_no_camera=0)


def _report_with_clips():
    return MergeReport(
        clips=[
            ClipReport(name="VID_001", duration=60.0, has_wav=True, video_included=True,
                      video_action="Stream copy", is_slowmo=False, slowmo_factor=1.0,
                      audio=[TrackPlan(label="Camera mic", out_codec="AAC", lossless=False,
                                       role="primary", est_bytes=1000)],
                      notes=[], est_bytes=50_000_000, best_secs=2.0, worst_secs=5.0),
            ClipReport(name="VID_002", duration=30.0, has_wav=False, video_included=True,
                      video_action="Transcode → 4K HEVC", is_slowmo=False, slowmo_factor=1.0,
                      audio=[], notes=["no camera audio"], est_bytes=80_000_000,
                      best_secs=10.0, worst_secs=30.0),
        ],
        total_bytes=130_000_000, best_secs=12.0, worst_secs=35.0,
        n_transcode=1, n_slowmo=0, n_no_camera=1)


def test_dialog_has_no_diagram_and_the_fixed_minimum_size():
    dlg = PreflightDialog(_empty_report())
    assert not hasattr(dlg, "_diagram")
    assert (dlg.minimumSize().width(), dlg.minimumSize().height()) == (560, 480)
    print("ok: test_dialog_has_no_diagram_and_the_fixed_minimum_size")


def test_dialog_builds_one_card_per_clip():
    dlg = PreflightDialog(_report_with_clips())
    # One QFrame-based clip card per report clip, found via the title label text.
    from PySide6.QtWidgets import QLabel
    titles = [w.text() for w in dlg.findChildren(QLabel) if w.text().startswith(("1.", "2."))]
    assert any(t.startswith("1.") and "VID_001" in t for t in titles)
    assert any(t.startswith("2.") and "VID_002" in t for t in titles)
    print("ok: test_dialog_builds_one_card_per_clip")


def test_disk_space_warning_shown_when_low():
    dlg = PreflightDialog(_report_with_clips(), free_bytes=1_000, need_bytes=260_000_000)
    from PySide6.QtWidgets import QLabel
    warn = [w.text() for w in dlg.findChildren(QLabel) if "may not fit" in w.text()]
    assert warn, "expected a low-disk-space warning label"
    print("ok: test_disk_space_warning_shown_when_low")


def _integration_run_diagnostics_through_the_dialog() -> bool:
    """Drives the actual dialog wiring (Run button -> DiagnosticsWorker QThread
    -> _on_diag_result -> per-clip label), not just DiagnosticsWorker directly
    (already covered in test_diagnostics.py's _integration_real_worker). This
    is the gap: nothing previously exercised _run_diagnostics/_on_diag_result/
    _on_diag_finished end to end against a real clip."""
    import subprocess
    import tempfile
    import time
    from core.binaries import get_ffmpeg

    ff, fp = get_ffmpeg()
    if not Path(ff).exists():
        print("  (skipped integration: ffmpeg not found)")
        return True

    d = Path(tempfile.mkdtemp())
    clip_path = d / "clip.mp4"
    subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi",
                   "-i", "testsrc=size=640x360:rate=30:duration=2",
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip_path)], check=True)

    from types import SimpleNamespace
    real_clip = SimpleNamespace(path=clip_path, duration=2.0, stem="clip")

    report = MergeReport(
        clips=[ClipReport(name="clip", duration=2.0, has_wav=False, video_included=True,
                          video_action="Stream copy", is_slowmo=False, slowmo_factor=1.0,
                          audio=[], notes=[], est_bytes=1_000_000, best_secs=1.0, worst_secs=2.0)],
        total_bytes=1_000_000, best_secs=1.0, worst_secs=2.0,
        n_transcode=0, n_slowmo=0, n_no_camera=0)

    dlg = PreflightDialog(report, clips=[real_clip])
    dlg.show()
    for cid, chk in dlg._diag_checks.items():
        chk.setChecked(cid in ("container", "timestamps", "streamcopy"))
    dlg._run_diagnostics()
    assert dlg._diag_worker is not None, "Run diagnostics did not start a worker"

    finished = {}
    dlg._diag_worker.finished_all.connect(lambda ok: finished.update(done=True, ok=ok))
    t0 = time.time()
    while not finished.get("done") and time.time() - t0 < 30:
        app.processEvents()
        time.sleep(0.05)
    for _ in range(20):
        app.processEvents()
        time.sleep(0.02)

    assert finished.get("done"), "diagnostics never finished within 30s"
    assert finished.get("ok") is True

    label = dlg._clip_diag_labels.get(0)
    assert label is not None, "clip 0 never got a diagnostics label"
    assert label.isVisible(), "diagnostics label was populated but left hidden"
    text = label.text()
    for check_id in ("container", "timestamps", "streamcopy"):
        assert check_id not in text  # labels show human labels, not check_ids
    assert "✓" in text or "clean" in text.lower() or "span" in text, (
        f"expected color-coded verdict spans in label text, got: {text!r}")
    assert dlg._diag_run_btn.isVisible() and not dlg._diag_cancel_btn.isVisible(), (
        "buttons did not reset to idle state after finishing")

    print("  real PreflightDialog diagnostics run (Run button -> worker -> "
         "per-clip label): populated with a color-coded verdict - OK")
    return True


def test_pipeline_section_absent_without_settings():
    # No settings passed → no pipeline controls (back-compat for any caller that
    # constructs the dialog report-only).
    dlg = PreflightDialog(_empty_report())
    assert not hasattr(dlg, "_pipe_recommended")
    print("ok: test_pipeline_section_absent_without_settings")


def test_pipeline_recommended_default_disables_custom_pickers():
    s = Settings()
    s.set("merge_pipeline_recommended", True)
    dlg = PreflightDialog(_empty_report(), settings=s, gpu_available=True)
    assert dlg._pipe_recommended.isChecked()
    assert not dlg._pipe_custom.isEnabled(), "custom pickers must be disabled in recommended mode"
    print("ok: test_pipeline_recommended_default_disables_custom_pickers")


def test_pipeline_custom_choices_persist_to_settings():
    s = Settings()
    s.set("merge_pipeline_recommended", True)
    dlg = PreflightDialog(_empty_report(), settings=s, gpu_available=True)
    # Unticking recommended enables the pickers and persists the mode.
    dlg._pipe_recommended.setChecked(False)
    assert s.get("merge_pipeline_recommended") is False
    assert dlg._pipe_custom.isEnabled()
    # Choose hardware decode + software encode.
    dlg._pipe_decode.setCurrentIndex(1)   # Hardware (GPU)
    dlg._pipe_encode.setCurrentIndex(0)   # Software (CPU)
    assert s.get("merge_decode_method") == "hardware"
    assert s.get("merge_encode_method") == "software"
    assert Settings().get("merge_decode_method") == "hardware", "must persist to disk"
    print("ok: test_pipeline_custom_choices_persist_to_settings")


def test_pipeline_hardware_disabled_when_no_gpu():
    s = Settings()
    s.set("merge_pipeline_recommended", False)
    s.set("merge_encode_method", "hardware")
    dlg = PreflightDialog(_empty_report(), settings=s, gpu_available=False)
    # The hardware item exists but is disabled, and the combo can't show it selected.
    hw_item = dlg._pipe_encode.model().item(1)
    assert not hw_item.isEnabled(), "hardware option must be disabled with no GPU"
    assert dlg._pipe_encode.currentData() == "software", "must fall back to software display"
    print("ok: test_pipeline_hardware_disabled_when_no_gpu")


if __name__ == "__main__":
    test_dialog_has_no_diagram_and_the_fixed_minimum_size()
    test_dialog_builds_one_card_per_clip()
    test_disk_space_warning_shown_when_low()
    test_pipeline_section_absent_without_settings()
    test_pipeline_recommended_default_disables_custom_pickers()
    test_pipeline_custom_choices_persist_to_settings()
    test_pipeline_hardware_disabled_when_no_gpu()
    print("test_preflight_dialog: all tests passed")
    print("running real ffmpeg integration...")
    _integration_run_diagnostics_through_the_dialog()
    print("test_preflight_dialog: integration test passed")
