"""test_ts_concat_e2e.py — end-to-end regression for the MPEG-TS concat route.

The builder-level tests in test_ffmpeg_cmd.py check argv shapes. This one
drives a REAL MergeWorker merge to completion and inspects the finished
master, because the defects this guards against are all invisible at the
argv level and all produce a file that ffmpeg was perfectly happy to write:

  * the concat PROTOCOL's backwards-PTS splice, which inflated a joined
    8.0s of AC-3 footage to 11.94s while ffmpeg exited 0;
  * a PCM/FLAC/ALAC audio track becoming a `bin_data` stream in the .ts and
    then vanishing entirely from the master, with ffmpeg exiting 0 at every
    stage — the TS route must detect that it cannot carry those streams and
    fall back to the plain stream-copy concat rather than silently ship a
    master with no sound.
"""

import os
import subprocess as sp
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402
_app = QApplication.instance() or QApplication([])

from probe import probe  # noqa: E402
from clip_model import ClipInfo  # noqa: E402
from core.binaries import get_ffmpeg  # noqa: E402
from core.ffmpeg_cmd import OutputPlan, DEFAULT_CONFORM, ConformSpec  # noqa: E402
from ffmpeg_runner import MergeWorker  # noqa: E402

FF, FP = get_ffmpeg()

# DEFAULT_CONFORM targets the app's real 4K/10-bit baseline. Our fixtures are
# deliberately tiny (320x240) so the ENCODE this test exercises stays cheap —
# using DEFAULT_CONFORM against them would force every clip through a real
# 4K/yuv420p10le/libx265-medium upscale conform on every run, which is slow
# and memory-heavy for no benefit: the TS-route logic under test operates on
# whatever the conform's OWN codec/pix_fmt ends up being, not on 4K-ness.
_SMALL_CONFORM = ConformSpec(width=320, height=240, fps="25", codec="hevc",
                             pix_fmt="yuv420p10le", color_space="bt709")


def _make_clip(path: Path, freq: int, keyint: int, acodec: str, dur: int = 3):
    """A segment with deliberately distinct encoder params, so two of them
    carry genuinely different HEVC parameter sets — the precondition for the
    corruption the TS route exists to fix."""
    sp.run([FF, "-y", "-v", "error",
            "-f", "lavfi", "-i", f"testsrc2=size=320x240:rate=25:duration={dur}",
            "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={dur}",
            "-c:v", "libx265", "-pix_fmt", "yuv420p", "-tag:v", "hvc1",
            "-x265-params", f"keyint={keyint}:log-level=none",
            "-c:a", acodec, str(path)], check=True, capture_output=True)
    return path


def _run_merge(clips, out_path, plan=None) -> tuple:
    """Drive MergeWorker synchronously; return (ok, message, notices)."""
    w = MergeWorker(clips, out_path, plan if plan is not None else OutputPlan(),
                    "crop", enable_preview=False, conform=_SMALL_CONFORM)
    result = {}
    w.finished.connect(lambda ok, msg: result.update(ok=ok, msg=msg))
    w.run()   # synchronous: run() directly, not start()
    return result.get("ok"), result.get("msg", ""), list(w._merge_notices)


def _inspect(path: Path) -> dict:
    dur = sp.run([FP, "-v", "error", "-show_entries", "format=duration",
                  "-of", "default=nw=1:nk=1", str(path)],
                 capture_output=True, text=True).stdout.strip()
    astreams = sp.run([FP, "-v", "error", "-select_streams", "a",
                       "-show_entries", "stream=codec_name", "-of", "csv=p=0",
                       str(path)], capture_output=True, text=True).stdout.split()
    errs = sp.run([FF, "-v", "error", "-i", str(path), "-f", "null", "-"],
                  capture_output=True, text=True).stderr.strip()
    return {"duration": float(dur or 0),
            "audio": astreams,
            "decode_errors": len([l for l in errs.splitlines() if l.strip()])}


def _clips_for(paths):
    out = []
    for p in paths:
        info = probe(FP, str(p))
        c = ClipInfo(path=p, stream=info)
        out.append(c)
    return out


def test_aac_clips_take_the_ts_route_and_keep_duration_and_audio():
    """Single-track output (camera audio only) — the pure "full" TS-route
    path, no split needed."""
    from core.ffmpeg_cmd import OutputTrack
    d = Path(tempfile.mkdtemp())
    a = _make_clip(d / "a.mp4", 440, 25, "aac")
    b = _make_clip(d / "b.mp4", 880, 10, "aac")
    out = d / "master.mov"
    single_track_plan = OutputPlan(tracks=[OutputTrack("camera")])
    ok, msg, notices = _run_merge(_clips_for([a, b]), out, plan=single_track_plan)
    assert ok, f"merge failed: {msg}"
    got = _inspect(out)
    assert abs(got["duration"] - 6.0) < 0.4, f"duration drifted: {got}"
    assert got["audio"], f"audio track lost: {got}"
    assert got["decode_errors"] == 0, f"master does not decode clean: {got}"
    assert not notices, f"AAC/HEVC should take the TS route cleanly, got {notices}"


def test_default_two_track_output_splits_the_join_for_the_alac_backup_track():
    """The app's DEFAULT output plan (camera + backup audio) is what most
    real merges actually produce — including every clip in the Cattle
    Country Farm Park footage, which has real per-clip _backup.wav files.
    With no wav here, the backup track falls back to a same-camera-audio
    duplicate encoded as ALAC, which MPEG-TS cannot carry at all. This must
    NOT silently fall back to the plain concat for the whole job (which
    would mean the parameter-set fix never applies to a default merge) — it
    must split: video+camera-audio via the TS route, the ALAC backup via a
    plain per-track join, recombined into one file with both tracks intact."""
    d = Path(tempfile.mkdtemp())
    a = _make_clip(d / "a.mp4", 440, 25, "aac")
    b = _make_clip(d / "b.mp4", 880, 10, "aac")
    out = d / "master.mov"
    ok, msg, notices = _run_merge(_clips_for([a, b]), out)   # default plan: camera + wav
    assert ok, f"merge failed: {msg}"
    got = _inspect(out)
    assert abs(got["duration"] - 6.0) < 0.4, f"duration drifted: {got}"
    assert got["audio"] == ["aac", "alac"], f"expected both tracks intact in order, got {got}"
    assert got["decode_errors"] == 0, f"master does not decode clean: {got}"
    assert any("Splitting the join" in n for n in notices), (
        f"expected the split path to engage and say so, got {notices}")
    assert not any("Falling back to a direct stream-copy join" in n for n in notices), (
        f"the split should succeed, not fall all the way back: {notices}")


def test_pcm_audio_falls_back_instead_of_silently_losing_the_track():
    """MPEG-TS has no stream type for PCM. Before the carriage gate, this
    merge produced a master with NO audio stream at all and reported
    success."""
    d = Path(tempfile.mkdtemp())
    a = _make_clip(d / "a.mov", 440, 25, "pcm_s16le")
    b = _make_clip(d / "b.mov", 880, 10, "pcm_s16le")
    src_audio = _inspect(a)["audio"]
    assert src_audio, "fixture itself has no audio — test is not meaningful"

    out = d / "master.mov"
    ok, msg, notices = _run_merge(_clips_for([a, b]), out)
    assert ok, f"merge failed: {msg}"
    got = _inspect(out)
    assert got["audio"], (
        f"audio track silently lost — the whole point of the gate. {got}")
    assert abs(got["duration"] - 6.0) < 0.4, f"duration drifted: {got}"
