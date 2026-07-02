"""spike_playback.py — Phase 3 throwaway spike (never shipped).

Question: can QtMultimedia (QMediaPlayer + QVideoSink, the same render
path the Review tab will use — not QVideoWidget) open a real LunaVault
master (4K 10-bit HEVC + several audio tracks), switch between audio
tracks, and seek/pause accurately — including inside a slow-motion
segment, where the user has observed their own media player stall on a
static frame and suspects the audio tracks are the cause?

Usage:
    python tools/spike_playback.py "<master.mov>" [slowmo_start_s] [slowmo_end_s]

Writes tools/spike_results.txt next to this script and prints the same
to stdout. This will play brief (~2.5s) bursts of audio through the
system speakers while it cycles tracks.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer, QUrl
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput, QVideoSink, QMediaMetaData

from core.binaries import get_ffmpeg, no_window

LOG_LINES: list[str] = []


def log(msg: str) -> None:
    print(msg)
    LOG_LINES.append(msg)


def pump(app, ms: int) -> None:
    """Let Qt's event loop (and the async media pipeline) run for `ms`."""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_for(app, predicate, timeout_ms=8000, step_ms=50) -> bool:
    waited = 0
    while waited < timeout_ms:
        if predicate():
            return True
        pump(app, step_ms)
        waited += step_ms
    return False


def probe_master(path: str) -> tuple[list, list]:
    ff, fp = get_ffmpeg()
    r = subprocess.run(
        [fp, "-v", "quiet", "-print_format", "json",
         "-show_streams", "-show_chapters", str(path)],
        capture_output=True, timeout=30, **no_window(),
    )
    data = json.loads(r.stdout.decode(errors="ignore") or "{}")
    streams = data.get("streams", [])
    chapters = data.get("chapters", [])
    log(f"\n=== ffprobe: {len(streams)} streams, {len(chapters)} chapters ===")
    for s in streams:
        if s.get("codec_type") == "video":
            log(f"  video: {s.get('codec_name')} {s.get('width')}x{s.get('height')} "
                f"pix_fmt={s.get('pix_fmt')} fps={s.get('r_frame_rate')} "
                f"color_space={s.get('color_space')} color_transfer={s.get('color_transfer')} "
                f"duration={s.get('duration')}s")
        elif s.get("codec_type") == "audio":
            tags = s.get("tags", {})
            log(f"  audio[{s.get('index')}]: {s.get('codec_name')} ch={s.get('channels')} "
                f"rate={s.get('sample_rate')} title={tags.get('title') or tags.get('handler_name')}")
    for i, c in enumerate(chapters):
        start, end = float(c.get("start_time", 0)), float(c.get("end_time", 0))
        title = c.get("tags", {}).get("title", "")
        log(f"  chapter {i+1}: {start:8.2f}s - {end:8.2f}s  {title}")
    return streams, chapters


def main():
    if len(sys.argv) < 2:
        print("usage: spike_playback.py <master.mov> [slowmo_start_s] [slowmo_end_s]")
        sys.exit(1)
    path = sys.argv[1]
    slowmo_start = float(sys.argv[2]) if len(sys.argv) > 2 else 2003.06
    slowmo_end   = float(sys.argv[3]) if len(sys.argv) > 3 else 2133.29

    log("=== LunaVault FuseBox v1.4 playback spike ===")
    log(f"master: {path}")
    log(f"slow-mo window under test: {slowmo_start:.2f}s - {slowmo_end:.2f}s")

    probe_master(path)

    app = QCoreApplication.instance() or QCoreApplication(sys.argv)

    player = QMediaPlayer()
    audio_out = QAudioOutput()
    player.setAudioOutput(audio_out)
    sink = QVideoSink()
    player.setVideoSink(sink)

    state = {"frames": 0, "last_frame_ts": None, "errors": []}

    def on_frame(frame):
        state["frames"] += 1
        state["last_frame_ts"] = frame.startTime()

    sink.videoFrameChanged.connect(on_frame)

    def on_error(err, err_string):
        state["errors"].append((str(err), err_string))
        log(f"  !! player error: {err} {err_string}")

    player.errorOccurred.connect(on_error)

    player.setSource(QUrl.fromLocalFile(str(Path(path).resolve())))

    log("\n=== load ===")
    loaded = wait_for(
        app,
        lambda: player.mediaStatus() in (
            QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia,
        ) or player.duration() > 0,
        timeout_ms=20000,
    )
    log(f"loaded={loaded} mediaStatus={player.mediaStatus()} duration_ms={player.duration()} "
        f"error={player.error()} errorString={player.errorString()!r}")

    tracks = player.audioTracks()
    log(f"\n=== audio tracks: {len(tracks)} ===")
    for i, md in enumerate(tracks):
        try:
            title = md.stringValue(QMediaMetaData.Key.Title)
            lang = md.value(QMediaMetaData.Key.Language)
        except Exception as e:
            title, lang = f"<error: {e}>", None
        log(f"  track[{i}]: title={title!r} language={lang!r}")
    log(f"activeAudioTrack (initial) = {player.activeAudioTrack()}")

    def try_playback(label: str, pos_s: float, hold_ms=2500, track_idx=None):
        state["frames"] = 0
        state["errors"] = []
        ok_track = None
        if track_idx is not None:
            player.setActiveAudioTrack(track_idx)
            pump(app, 150)
            ok_track = (player.activeAudioTrack() == track_idx)
        player.pause()
        player.setPosition(int(pos_s * 1000))
        wait_for(app, lambda: abs(player.position() - pos_s * 1000) < 500, timeout_ms=5000)
        player.play()
        pump(app, hold_ms)
        got_frames = state["frames"]
        end_pos = player.position()
        player.pause()
        log(f"  [{label}] track_set_ok={ok_track} seek_to={pos_s:.2f}s "
            f"end_pos={end_pos/1000:.2f}s frames_seen={got_frames} "
            f"playbackState={player.playbackState()} mediaStatus={player.mediaStatus()} "
            f"errors={state['errors']}")
        return got_frames

    log("\n=== control: normal-speed segment (60s before slow-mo) ===")
    control_frames = try_playback("control", max(0.0, slowmo_start - 60.0))

    log("\n=== slow-motion segment, default/current audio track ===")
    slowmo_frames_default = try_playback("slowmo-default", slowmo_start + 20.0)

    log("\n=== slow-motion segment, cycling every audio track ===")
    per_track_frames = []
    for i in range(len(tracks)):
        f = try_playback(f"slowmo-track{i}", slowmo_start + 20.0, track_idx=i)
        per_track_frames.append(f)

    log("\n=== frame capture at a paused position (control segment) ===")
    player.setActiveAudioTrack(0)
    player.pause()
    player.setPosition(int(max(0.0, slowmo_start - 60.0) * 1000))
    got = wait_for(app, lambda: state["frames"] > 0, timeout_ms=5000)
    if got:
        frame = sink.videoFrame()
        if frame.isValid():
            img = frame.toImage()
            log(f"  frame: pixelFormat={frame.pixelFormat()} size={frame.width()}x{frame.height()} "
                f"qimage_format={img.format()} qimage_size={img.width()}x{img.height()}")
        else:
            log("  frame: invalid QVideoFrame at pause")
    else:
        log("  no frame arrived for format inspection within timeout")

    log("\n=== summary ===")
    log(f"control segment frames in 2500ms: {control_frames}")
    log(f"slow-mo (unchanged track) frames in 2500ms: {slowmo_frames_default}")
    for i, f in enumerate(per_track_frames):
        log(f"slow-mo (track {i} active) frames in 2500ms: {f}")
    static_frame_reproduced = slowmo_frames_default == 0 or any(f == 0 for f in per_track_frames)
    if control_frames == 0:
        verdict = "INCONCLUSIVE — control segment itself produced no frames (decode/backend issue unrelated to slow-mo)"
    elif static_frame_reproduced:
        verdict = "REPRODUCED — QtMultimedia also stalls in the slow-mo segment (hybrid engine path needed)"
    else:
        verdict = "PASS — QtMultimedia plays the slow-mo segment cleanly on every track"
    log(f"verdict: {verdict}")

    out = ROOT / "tools" / "spike_results.txt"
    out.write_text("\n".join(LOG_LINES), encoding="utf-8")
    log(f"\nwritten: {out}")


if __name__ == "__main__":
    main()
