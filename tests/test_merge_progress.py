"""Tests for MergeWorker's byte-weighted progress/ETA plumbing
(_estimate_expected_total_bytes, _metrics) — the Merge-tab half of the
conservative-ETA/GB-readout work (see core.eta for the shared algorithm)."""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication([])

from probe import StreamInfo
from clip_model import ClipInfo
from core.ffmpeg_cmd import OutputPlan, DEFAULT_CONFORM
from core.plan_report import analyze_merge
from core.eta import ConservativeEta
from ffmpeg_runner import MergeWorker


def _clip(path, status="ok", dur=10.0, wav=None, lrv=None,
         preserve_wav_full=False, preserve_lrv=False):
    c = ClipInfo(path=path, stream=StreamInfo(status=status, width=3840, height=2160,
                                              duration=dur, audio_codec="aac"))
    if wav is not None:
        c.wav_path = wav
        c.wav_duration = dur
    if lrv is not None:
        c.lrv_path = lrv
        c.lrv_width, c.lrv_height = 1280, 720
    c.preserve_wav_full = preserve_wav_full
    c.preserve_lrv = preserve_lrv
    return c


def _worker(clips, plan=None, archival=False):
    return MergeWorker(clips, Path("out.mov"), plan or OutputPlan(), "crop",
                       archival=archival, conform=DEFAULT_CONFORM)


def test_expected_total_is_two_passes_without_archival():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "a.mp4"
        p.write_bytes(b"x" * 1000)
        clips = [_clip(p)]
        plan = OutputPlan()
        report = analyze_merge(clips, plan)
        w = _worker(clips, plan)
        total = w._estimate_expected_total_bytes(clips)
        assert total == report.total_bytes * 2


def test_expected_total_adds_archival_pass_and_odd_spec_originals():
    with tempfile.TemporaryDirectory() as td:
        conform_p = Path(td) / "conform.mp4"
        conform_p.write_bytes(b"x" * 1000)
        odd_p = Path(td) / "odd.mp4"
        odd_p.write_bytes(b"y" * 5000)   # a real, sized "odd-spec" original

        clips = [_clip(conform_p, status="ok"), _clip(odd_p, status="transcode")]
        plan = OutputPlan()
        report = analyze_merge(clips, plan)
        w = _worker(clips, plan, archival=True)
        total = w._estimate_expected_total_bytes(clips)
        # pass1 + pass2 + pass3(archival re-mux) + the odd clip's own real bytes
        assert total == report.total_bytes * 3 + 5000


def test_expected_total_adds_preserved_wav_and_lrv_bytes():
    with tempfile.TemporaryDirectory() as td:
        vid = Path(td) / "a.mp4"
        vid.write_bytes(b"x" * 1000)
        wav = Path(td) / "a.wav"
        wav.write_bytes(b"w" * 2000)
        lrv = Path(td) / "a.lrv"
        lrv.write_bytes(b"l" * 300)

        clips = [_clip(vid, wav=wav, lrv=lrv, preserve_wav_full=True, preserve_lrv=True)]
        plan = OutputPlan()
        report = analyze_merge(clips, plan)
        w = _worker(clips, plan)
        total = w._estimate_expected_total_bytes(clips)
        assert total == report.total_bytes * 2 + 2000 + 300


def test_expected_total_never_zero_even_for_empty_clip_list():
    w = _worker([])
    assert w._estimate_expected_total_bytes([]) >= 1


def test_metrics_reports_cumulative_produced_bytes():
    # _eta/_expected_total_bytes/_produced_bytes_base are normally set inside
    # run() — set them directly here to exercise _metrics() in isolation.
    w = _worker([])
    w._eta = ConservativeEta()
    w._expected_total_bytes = 1000
    w._produced_bytes_base = 400
    m = w._metrics(size=100)
    assert m["produced_bytes"] == 500
    assert m["expected_total_bytes"] == 1000
    assert 0 < m["byte_pct"] < 100
    assert "eta_secs" in m and "elapsed_secs" in m


def test_metrics_ignores_negative_size():
    w = _worker([])
    w._eta = ConservativeEta()
    w._expected_total_bytes = 1000
    w._produced_bytes_base = 250
    m = w._metrics(size=-50)
    assert m["produced_bytes"] == 250   # max(0, -50) contributes nothing


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"\n{len(fns)} merge-progress tests passed.")
    sys.stdout.flush()
    os._exit(0)
