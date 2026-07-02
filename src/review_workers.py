"""review_workers.py — background QThread workers for the Review tab.

Every worker follows the Phase-1 thread-lifetime discipline the rest of the
app uses: anything that can run for more than a moment checks a cancellation
flag (and kills its subprocess on cancel()), and the owner is expected to
keep a tracked set of live workers and settle() them all on shutdown() —
never drop the last reference to a thread that hasn't been wait()ed.

These are one-shot request/response jobs (mirroring ffmpeg_runner.py's
FramePreviewWorker), not long-running services. Debouncing — waiting for
the audio-tick-set or the visible viewport to stop changing before spending
an ffmpeg process on it — is the caller's job via a QTimer.

Exact-frame extraction and PNG snapshots deliberately never go through
QMediaPlayer/QVideoFrame: the v1.4 playback spike confirmed
QVideoFrame.toImage() silently converts genuine 10-bit frames to 8-bit
RGB. Only ffmpeg-extracted rgb48le / 16-bit PNG output is trustworthy for
the scopes panel's exact readings or for a snapshot.
"""

import subprocess
from pathlib import Path
from typing import Optional

import numpy as np
from PySide6.QtCore import QThread, Signal

from core.binaries import no_window
from core.audio_peaks import build_pcm_extract_cmd, pyramid_from_stream
from core.review_media import build_frame_extract_cmd, build_snapshot_cmd, build_review_mix_cmd
from core.spectrogram import spectrogram, to_rgb
from probe import probe, probe_audio_tracks, probe_chapters


def _run_cancelable(worker, cmd: list, timeout: float) -> tuple:
    """Run `cmd` via Popen (stored on `worker._proc` so `worker.cancel()`
    can terminate it early) instead of `subprocess.run(timeout=...)`, whose
    blocking wait can't be interrupted — a worker mid-flight on slow/cloud
    storage would otherwise force shutdown() to wait out its full timeout.

    Returns (stdout_bytes, error_message, was_cancelled); stdout is None on
    failure or cancellation.
    """
    if getattr(worker, "_cancelled", False):
        # cancel() can race ahead of run() actually starting (the caller may
        # set _cancelled the instant the worker object exists, well before
        # its QThread gets CPU time) — honour it here rather than spawning a
        # process nothing will be able to interrupt until it finishes on its
        # own.
        return None, "", True
    try:
        worker._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, **no_window())
    except Exception as e:
        return None, str(e), False
    try:
        stdout, stderr = worker._proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        worker._proc.kill()
        worker._proc.communicate()
        worker._proc = None
        return None, "timed out", getattr(worker, "_cancelled", False)
    returncode = worker._proc.returncode
    worker._proc = None
    if getattr(worker, "_cancelled", False):
        return None, "", True
    if returncode != 0:
        # Some callers (snapshot mode) write to a file and expect empty
        # stdout on success — only a non-zero returncode means failure here;
        # "did I get the bytes I actually needed" is the caller's own check.
        msg = stderr.decode(errors="ignore")[-200:] if stderr else "command failed"
        return None, msg, False
    return stdout, None, False


# ── Track scan ──────────────────────────────────────────────────────────────

class TrackScanWorker(QThread):
    """Probe a master's video stream, every audio track, and its chapters
    (masters carry per-clip chapters written at merge time — the Review
    tab's prev/next transport jumps to these)."""
    tracks_ready = Signal(object, list, list)   # StreamInfo, list[AudioTrackInfo], list[ChapterInfo]

    def __init__(self, ffprobe_bin: str, path: str, parent=None):
        super().__init__(parent)
        self._ffprobe = ffprobe_bin
        self._path = str(path)

    def run(self):
        video_info = probe(self._ffprobe, self._path)
        audio_tracks = probe_audio_tracks(self._ffprobe, self._path)
        chapters = probe_chapters(self._ffprobe, self._path)
        self.tracks_ready.emit(video_info, audio_tracks, chapters)


# ── Peak pyramids (one worker, tracks processed serially) ───────────────────

class PeakScanWorker(QThread):
    """Build a peak pyramid for every audio track, one track at a time — a
    single ffmpeg process running at once keeps I/O predictable on slow or
    cloud-synced storage rather than N tracks racing each other."""
    progress      = Signal(int, float)    # track_idx, 0..1 estimated completion
    pyramid_ready = Signal(int, object)   # track_idx, PeakPyramid

    def __init__(self, ffmpeg_bin: str, path: str, track_indices: list,
                duration: float = 0.0, parent=None):
        super().__init__(parent)
        self._ffmpeg = ffmpeg_bin
        self._path = str(path)
        self._track_indices = list(track_indices)
        self._duration = max(0.0, duration)
        self._cancelled = False
        self._proc: Optional[subprocess.Popen] = None

    def cancel(self):
        self._cancelled = True
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass

    def run(self):
        # f32le mono @ 8kHz = 32000 bytes/sec of extracted audio — used only
        # to estimate progress; the real ffmpeg output length can vary a
        # little, so this is clamped and best-effort.
        expected_bytes = max(1, int(self._duration * 8000 * 4))
        for idx in self._track_indices:
            if self._cancelled:
                return
            cmd = build_pcm_extract_cmd(self._ffmpeg, self._path, idx)
            try:
                self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                              stderr=subprocess.DEVNULL, **no_window())
            except Exception:
                continue

            def _chunks(track_idx=idx):
                proc = self._proc
                read = 0
                while not self._cancelled:
                    chunk = proc.stdout.read(1 << 16)
                    if not chunk:
                        break
                    read += len(chunk)
                    self.progress.emit(track_idx, min(1.0, read / expected_bytes))
                    yield chunk

            pyramid = pyramid_from_stream(_chunks())
            self._proc.wait()
            self._proc = None
            if not self._cancelled:
                self.progress.emit(idx, 1.0)
                self.pyramid_ready.emit(idx, pyramid)


# ── Spectrogram tiles (one-shot per visible window) ──────────────────────────

class SpectrogramWorker(QThread):
    """Extract PCM for a visible time window and colourize it into a
    spectrogram image. The caller debounces viewport changes and keeps its
    own small LRU tile cache — this worker just answers one request.

    The output image's height is fixed by `n_fft` (513 rows for the
    default 1024) — a frequency-resolution choice, not a display size — so
    there's no `out_h` knob here; the caller scales the returned image to
    whatever pixel height it's painting, the same way thumbnail images are
    scaled elsewhere in the app.
    """
    image_ready = Signal(int, float, float, object)   # track_idx, t0, t1, uint8 (n_bins, n_frames, 3) ndarray
    error       = Signal(int, str)

    def __init__(self, ffmpeg_bin: str, path: str, track_idx: int,
                t0: float, t1: float, parent=None):
        super().__init__(parent)
        self._ffmpeg = ffmpeg_bin
        self._path = str(path)
        self._track_idx = track_idx
        self._t0 = max(0.0, t0)
        self._t1 = max(self._t0 + 0.05, t1)
        self._proc: Optional[subprocess.Popen] = None
        self._cancelled = False

    def cancel(self):
        self._cancelled = True
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass

    def run(self):
        rate = 8000
        dur = self._t1 - self._t0
        cmd = build_pcm_extract_cmd(self._ffmpeg, self._path, self._track_idx, rate=rate)
        # Bound extraction to the visible window (input-side -ss/-t, before
        # -i) — far cheaper than pulling the whole track for one tile.
        i_idx = cmd.index("-i")
        cmd = cmd[:i_idx] + ["-ss", f"{self._t0:.3f}", "-t", f"{dur:.3f}"] + cmd[i_idx:]
        stdout, stderr, cancelled = _run_cancelable(self, cmd, timeout=45)
        if cancelled:
            return
        if stdout is None:
            self.error.emit(self._track_idx, stderr or "extraction failed")
            return
        pcm = np.frombuffer(stdout, dtype=np.float32)
        spec = spectrogram(pcm, rate)
        img = to_rgb(spec)
        self.image_ready.emit(self._track_idx, self._t0, self._t1, img)


# ── Tick-set audio mix render ─────────────────────────────────────────────────

class MixRenderWorker(QThread):
    """Render a tick-set's audio tracks to one AAC file the playback engine
    can slave to. `out_path` should be named from mix_cache_key() so a
    repeated tick-set is a no-op — the render is skipped if it already
    exists."""
    mix_ready = Signal(str, str)   # cache_key, out_path
    error     = Signal(str, str)   # cache_key, message

    def __init__(self, ffmpeg_bin: str, path: str, track_indices: list,
                out_path: str, cache_key: str, parent=None):
        super().__init__(parent)
        self._ffmpeg = ffmpeg_bin
        self._path = str(path)
        self._track_indices = list(track_indices)
        self._out_path = str(out_path)
        self._cache_key = cache_key
        self._cancelled = False
        self._proc: Optional[subprocess.Popen] = None

    def cancel(self):
        self._cancelled = True
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass

    def run(self):
        if self._cancelled:
            # cancel() can race ahead of run() starting at all (see
            # _run_cancelable's docstring for the same race in the other
            # workers) — a full-file render is minutes long, so honouring
            # this early is the difference between an instant cancel and
            # one that silently runs to completion anyway.
            return
        if Path(self._out_path).exists():
            self.mix_ready.emit(self._cache_key, self._out_path)
            return
        cmd = build_review_mix_cmd(self._ffmpeg, self._path, self._track_indices, self._out_path)
        try:
            self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.PIPE, **no_window())
            _, stderr = self._proc.communicate()
        except Exception as e:
            self.error.emit(self._cache_key, str(e))
            return
        returncode = self._proc.returncode
        self._proc = None
        if self._cancelled:
            # ffmpeg was killed mid-write — remove the partial file so a later
            # cache-hit check doesn't mistake it for a complete render.
            try:
                Path(self._out_path).unlink(missing_ok=True)
            except Exception:
                pass
            return
        if returncode != 0 or not Path(self._out_path).exists():
            msg = stderr.decode(errors="ignore")[-200:] if stderr else "mix render failed"
            self.error.emit(self._cache_key, msg)
            return
        self.mix_ready.emit(self._cache_key, self._out_path)


# ── Exact frames + snapshots ──────────────────────────────────────────────────

class FrameFetchWorker(QThread):
    """Exact-frame extraction (rgb48le, for the scopes panel) or a full-res
    16-bit PNG snapshot — one worker, one request, chosen by `mode`."""
    exact_frame_ready = Signal(object, float)   # uint16 (H,W,3) ndarray, secs
    snapshot_saved    = Signal(str)             # written PNG path
    error             = Signal(str)

    def __init__(self, ffmpeg_bin: str, path: str, secs: float,
                mode: str = "frame", width: int = 0, height: int = 0,
                snapshot_out: str = "", parent=None):
        super().__init__(parent)
        self._ffmpeg = ffmpeg_bin
        self._path = str(path)
        self._secs = secs
        self._mode = mode   # "frame" | "snapshot"
        self._width = width
        self._height = height
        self._snapshot_out = snapshot_out
        self._proc: Optional[subprocess.Popen] = None
        self._cancelled = False

    def cancel(self):
        self._cancelled = True
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass

    def run(self):
        if self._mode == "snapshot":
            self._run_snapshot()
        else:
            self._run_frame()

    def _run_frame(self):
        if not (self._width and self._height):
            self.error.emit("frame extraction needs width/height")
            return
        cmd = build_frame_extract_cmd(self._ffmpeg, self._path, self._secs,
                                      width=self._width, height=self._height,
                                      pix_fmt="rgb48le")
        stdout, err, cancelled = _run_cancelable(self, cmd, timeout=45)
        if cancelled:
            return
        expected = self._width * self._height * 3 * 2
        if stdout is None or len(stdout) != expected:
            self.error.emit(err or "frame extraction failed")
            return
        arr = np.frombuffer(stdout, dtype="<u2").reshape(self._height, self._width, 3)
        self.exact_frame_ready.emit(arr, self._secs)

    def _run_snapshot(self):
        if not self._snapshot_out:
            self.error.emit("snapshot needs an output path")
            return
        cmd = build_snapshot_cmd(self._ffmpeg, self._path, self._secs, self._snapshot_out)
        _, err, cancelled = _run_cancelable(self, cmd, timeout=45)
        if cancelled:
            return
        if err is not None or not Path(self._snapshot_out).exists():
            self.error.emit(err or "snapshot failed")
            return
        self.snapshot_saved.emit(self._snapshot_out)
