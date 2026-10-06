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
    path for the video; audio joins directly as exact-length ALAC."""
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
    # Video takes the TS route; the audio is joined directly as exact-length
    # ALAC and encoded to AAC once afterwards (see final_audio_encode_args).
    assert notices and all("Splitting the join" in n for n in notices), notices
    assert got["audio"] == ["aac"], got


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


def _make_clip_rate(path: Path, freq: int, keyint: int, rate: int, channels: int, dur: int = 3):
    """Like _make_clip, but with real-camera audio: 48 kHz stereo AAC (what the
    Luna Ultra, GO 3S and Pixel 4K clips all record) or any other rate/layout."""
    sp.run([FF, "-y", "-v", "error",
            "-f", "lavfi", "-i", f"testsrc2=size=320x240:rate=25:duration={dur}",
            "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={dur}:sample_rate={rate}",
            "-c:v", "libx265", "-pix_fmt", "yuv420p", "-tag:v", "hvc1",
            "-x265-params", f"keyint={keyint}:log-level=none",
            "-c:a", "aac", "-ac", str(channels), str(path)], check=True, capture_output=True)
    return path


def test_48k_camera_audio_joins_frame_exact():
    """48 kHz AAC starts ~21 ms before the video inside MPEG-TS, so a join that
    advanced by container duration shifted every later clip by half a frame
    (a decode-time "non monotonically increasing dts" at each splice). The
    earlier tests only used 44.1 kHz fixtures, which hid this."""
    from core.ffmpeg_cmd import OutputTrack
    d = Path(tempfile.mkdtemp())
    a = _make_clip_rate(d / "a.mp4", 440, 25, 48000, 2)
    b = _make_clip_rate(d / "b.mp4", 880, 10, 48000, 2)
    c = _make_clip_rate(d / "c.mp4", 660, 12, 48000, 2)
    out = d / "master.mov"
    ok, msg, _ = _run_merge(_clips_for([a, b, c]), out,
                            plan=OutputPlan(tracks=[OutputTrack("camera")]))
    assert ok, f"merge failed: {msg}"
    got = _inspect(out)
    assert got["decode_errors"] == 0, f"joins are not frame-exact: {got}"
    assert abs(got["duration"] - 9.0) < 0.1, f"duration drifted: {got}"


def test_mixed_sample_rates_and_layouts_are_normalised_to_48k_stereo():
    """A 44.1 kHz mono clip between 48 kHz stereo clips must not be stream-
    copied into the shared AAC track (one track can only declare one rate)."""
    from core.ffmpeg_cmd import OutputTrack
    d = Path(tempfile.mkdtemp())
    a = _make_clip_rate(d / "a.mp4", 440, 25, 48000, 2)
    b = _make_clip_rate(d / "b.mp4", 880, 10, 44100, 1)
    out = d / "master.mov"
    ok, msg, _ = _run_merge(_clips_for([a, b]), out,
                            plan=OutputPlan(tracks=[OutputTrack("camera")]))
    assert ok, f"merge failed: {msg}"
    info = sp.run([FP, "-v", "error", "-select_streams", "a:0", "-show_entries",
                   "stream=sample_rate,channels", "-of", "csv=p=0", str(out)],
                  capture_output=True, text=True).stdout.strip()
    assert info == "48000,2", info
    assert _inspect(out)["decode_errors"] == 0
    # The declared format alone proves nothing (ffprobe reports segment 1's).
    # The real symptom was the 44.1 kHz clip playing 9% fast: its 880 Hz tone
    # came out at 958 Hz and the track ran 0.18 s short, drifting A/V sync.
    import numpy as np
    pcm = np.frombuffer(sp.run([FF, "-v", "error", "-i", str(out), "-map", "0:a:0",
                                "-ac", "1", "-ar", "48000", "-f", "f32le", "-"],
                               capture_output=True).stdout, np.float32)
    assert abs(len(pcm) / 48000 - 6.0) < 0.1, f"audio length {len(pcm) / 48000:.3f}s"
    seg = pcm[int(3.5 * 48000):int(5.5 * 48000)]
    freqs = np.fft.rfftfreq(len(seg), 1 / 48000)
    peak = freqs[np.argmax(np.abs(np.fft.rfft(seg)))]
    assert abs(peak - 880) < 5, f"second clip plays at {peak:.0f} Hz, expected 880"


def _make_overrun_clip(path: Path, video_s: float, audio_s: float, beep_at: float):
    """A clip whose audio track is longer/shorter than its picture (real cameras
    differ by ~10-60 ms) with a flash + 1 kHz beep at the same instant."""
    ev = f"between(t,{beep_at},{beep_at + 0.1})"
    sp.run([FF, "-y", "-v", "error",
            "-f", "lavfi", "-i", f"color=c=0x202020:s=320x240:r=25:d={video_s},"
                                 f"drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='{ev}'",
            "-f", "lavfi", "-i", f"aevalsrc='if({ev},0.7*sin(2*PI*1000*t),0)':s=48000:d={audio_s}",
            "-c:v", "libx265", "-pix_fmt", "yuv420p", "-tag:v", "hvc1",
            "-x265-params", "log-level=none", "-c:a", "aac", "-ac", "2", str(path)],
           check=True, capture_output=True)
    return path


def _onsets(path: Path, kind: str):
    if kind == "v":
        out = sp.run([FF, "-v", "error", "-i", str(path), "-map", "0:v:0", "-vf",
                      "format=yuv420p,signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-",
                      "-f", "null", "-"], capture_output=True, text=True).stdout
        t, hits, last = None, [], -9.0
        for line in out.splitlines():
            if line.startswith("frame:"):
                t = float(line.split("pts_time:")[1])
            elif "YAVG=" in line and float(line.split("=")[1]) > 180:
                if t - last > 0.5:
                    hits.append(t)
                last = t
        return hits
    import numpy as np
    raw = sp.run([FF, "-v", "error", "-i", str(path), "-map", "0:a:0", "-ac", "1", "-ar", "48000",
                  "-f", "f32le", "-"], capture_output=True).stdout
    a = np.abs(np.frombuffer(raw, dtype=np.float32))
    hits, last = [], -9.0
    for i in np.flatnonzero(a > 0.3):
        if i / 48000 - last > 0.5:
            hits.append(i / 48000)
        last = i / 48000
    return hits


def test_audio_stays_under_its_picture_at_every_join():
    """Each clip's audio here is 60 ms LONGER than its video (as some cameras
    record). Joins are pinned to the picture, so the audio used to slide
    later by the overrun (plus AAC priming) at every join: ~+80 ms per clip."""
    d = Path(tempfile.mkdtemp())
    paths = [_make_overrun_clip(d / f"c{i}.mp4", 2.0, 2.06, 1.0) for i in range(4)]
    out = d / "master.mov"
    from core.ffmpeg_cmd import OutputTrack
    ok, msg, _ = _run_merge(_clips_for(paths), out, plan=OutputPlan(tracks=[OutputTrack("camera")]))
    assert ok, msg
    v, a = _onsets(out, "v"), _onsets(out, "a")
    assert len(v) == 4 and len(a) == 4, (v, a)
    worst = max(abs(x - y) for x, y in zip(a, v))
    assert worst < 0.025, f"A/V offset per join (s): {[round(x - y, 3) for x, y in zip(a, v)]}"


def _make_late_audio_clip(path: Path, video_s: float, beep_at: float):
    """Pixel-like: 30 fps (so it converts to the 25 fps test baseline), and its
    audio track starts ~40 ms after the video and stops before it ends —
    muxed with an offset the way a phone writes it (an -itsoffset on a lavfi
    source gets normalised away)."""
    v, a = path.with_suffix(".v.mp4"), path.with_suffix(".a.m4a")
    sp.run([FF, "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=0x202020:s=320x240:r=30:d={video_s}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(v)], check=True, capture_output=True)
    sp.run([FF, "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=f=440:d={video_s - 0.07}:r=48000",
            "-c:a", "aac", "-ac", "2", str(a)], check=True, capture_output=True)
    sp.run([FF, "-y", "-v", "error", "-i", str(v), "-itsoffset", "0.043", "-i", str(a),
            "-map", "0:v", "-map", "1:a", "-c", "copy", str(path)], check=True, capture_output=True)
    return path


def test_converted_segment_with_late_audio_is_exactly_its_picture_length():
    """Real Pixel clips' audio starts 20-43 ms after their video. Left as a
    timestamp that gap vanished at the join, and -frames:v ended the segment
    before the audio was padded out: a real 5-clip merge drifted -183 ms."""
    from core.ffmpeg_cmd import OutputTrack, build_mux_cmd_plan
    d = Path(tempfile.mkdtemp())
    src = _make_late_audio_clip(d / "p.mp4", 2.0, 1.0)
    st = sp.run([FP, "-v", "error", "-select_streams", "a", "-show_entries", "stream=start_time",
                 "-of", "csv=p=0", str(src)], capture_output=True, text=True).stdout.strip()
    assert float(st) > 0.01, f"fixture should have late-starting audio, got {st}"
    clip = _clips_for([src])[0]
    clip.stream.status = "transcode"
    out = d / "seg.mov"
    cmd = build_mux_cmd_plan(FF, clip, out, d / "p.txt", OutputPlan(tracks=[OutputTrack("camera")]),
                             "crop", conform=_SMALL_CONFORM)
    sp.run(cmd, check=True, capture_output=True)
    rows = sp.run([FP, "-v", "error", "-show_entries", "stream=codec_type,start_time,duration",
                   "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout.split()
    got = {r.split(",")[0]: [float(x) for x in r.split(",")[1:3]] for r in rows}
    assert got["audio"][0] == 0.0, got
    assert abs(got["audio"][1] - got["video"][1]) < 0.001, got
