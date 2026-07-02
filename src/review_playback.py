"""review_playback.py — the Review tab's playback abstraction.

`PlaybackEngine` is the interface the Review tab codes against; `ReviewTab`
never touches QMediaPlayer directly. The v1.4 playback spike
(tools/spike_playback.py) found QtMultimedia plays this app's masters —
4K 10-bit HEVC, multiple AAC/ALAC audio tracks, including slow-motion
chapters — cleanly, so `QtPlaybackEngine` is the only implementation; a
`HybridPlaybackEngine` (ffmpeg-piped frames) was designed as a fallback but
isn't built, since nothing here needs it. The interface exists anyway so
that decision can be revisited without touching the tab.

Two things the spike confirmed shape this file:
  - `QVideoFrame.toImage()` silently converts genuine 10-bit frames to
    8-bit RGB — fine for on-screen playback, but `frame_ready` frames must
    never be used for the scopes panel's exact/paused readings or for
    snapshots (those go through ffmpeg extraction in review_workers.py).
  - Qt exposes no usable per-track audio metadata, so track identity for
    `set_audio_single`/`set_audio_mix_file` comes from the caller (built
    from `probe.probe_audio_tracks`), not from Qt.

A third thing found while wiring up the Review tab: a freshly loaded
QMediaPlayer that has never been played delivers no video frames at all
from a bare seek() — `PlaybackState.StoppedState` appears to gate frame
delivery regardless of position. A brief play()-then-pause() "prime" pulse
right after load moves it into PausedState, after which seeks reliably
deliver frames — see `_prime_first_frame()`.
"""

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal, QTimer, QUrl
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput, QVideoSink

# Resync the mix-audio player against the video player's clock at this
# interval, if they've drifted apart by more than the threshold.
_MIX_RESYNC_MS = 500
_MIX_DRIFT_THRESHOLD_S = 0.040

# UI-side position updates while playing (native positionChanged is coarser).
_POSITION_POLL_MS = 33


class PlaybackEngine(QObject):
    """Interface the Review tab codes against."""

    duration_known    = Signal(float, float)   # duration_secs, fps
    position_changed  = Signal(float)          # secs
    frame_ready        = Signal(QImage, float)  # frame, secs (approximate — see module docstring)
    state_changed      = Signal(bool)           # True while playing
    audio_mode_changed = Signal(str)            # human-readable, e.g. "Playing: Camera mic"
    error               = Signal(str)

    def load(self, path: str, tracks: list, fps: float = 29.97):
        raise NotImplementedError

    def play(self):
        raise NotImplementedError

    def pause(self):
        raise NotImplementedError

    def toggle(self):
        raise NotImplementedError

    def seek(self, secs: float):
        raise NotImplementedError

    def step_frames(self, n: int):
        raise NotImplementedError

    def set_audio_single(self, track_idx: int) -> bool:
        raise NotImplementedError

    def set_audio_mix_file(self, path: str):
        raise NotImplementedError

    def current_position(self) -> float:
        raise NotImplementedError

    def shutdown(self):
        raise NotImplementedError


class QtPlaybackEngine(PlaybackEngine):
    """QMediaPlayer + QVideoSink + QAudioOutput, with a second (video-less)
    QMediaPlayer slaved to the first when playing a rendered tick-set mix."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duration: float = 0.0
        self._fps: float = 29.97
        self._tracks: list = []
        self._path: str = ""
        self._priming = False   # True during the post-load play/pause warm-up pulse
        self._prime_was_muted = False
        self._prime_timer = QTimer(self)
        self._prime_timer.setSingleShot(True)
        self._prime_timer.timeout.connect(self._end_prime)

        self._player = QMediaPlayer(self)
        self._audio_out = QAudioOutput(self)
        self._player.setAudioOutput(self._audio_out)
        self._sink = QVideoSink(self)
        self._player.setVideoSink(self._sink)

        self._sink.videoFrameChanged.connect(self._on_video_frame)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.playbackStateChanged.connect(self._on_playback_state_changed)
        self._player.errorOccurred.connect(self._on_error)
        self._player.positionChanged.connect(self._on_native_position_changed)

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(_POSITION_POLL_MS)
        self._poll_timer.timeout.connect(self._poll_position)

        # Mix-audio slave player (created lazily, torn down when not needed)
        self._mix_player: Optional[QMediaPlayer] = None
        self._mix_audio_out: Optional[QAudioOutput] = None
        self._mix_resync_timer: Optional[QTimer] = None

    # ── Loading ───────────────────────────────────────────────────────────────

    def load(self, path: str, tracks: list, fps: float = 29.97):
        if self._priming:
            self._prime_timer.stop()
            self._priming = False
        self._clear_mix()
        self._path = str(path)
        self._tracks = tracks
        self._fps = fps if fps > 0 else 29.97
        self._duration = 0.0
        self._audio_out.setMuted(False)
        self._player.setSource(QUrl.fromLocalFile(str(Path(path).resolve())))

    # ── Transport ─────────────────────────────────────────────────────────────

    def play(self):
        if self._priming:
            self._end_prime()
        self._player.play()

    def pause(self):
        if self._priming:
            self._end_prime()
        self._player.pause()

    def toggle(self):
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.pause()
        else:
            self.play()

    def seek(self, secs: float):
        if self._priming:
            self._end_prime()
        secs = max(0.0, min(secs, self._duration))
        self._player.setPosition(int(secs * 1000))
        if self._mix_player is not None:
            self._mix_player.setPosition(int(secs * 1000))

    def step_frames(self, n: int):
        if self._fps <= 0 or n == 0:
            return
        self.pause()
        target = self.current_position() + n / self._fps
        self.seek(target)

    def current_position(self) -> float:
        return self._player.position() / 1000.0

    # ── Audio track selection ────────────────────────────────────────────────

    def set_audio_single(self, track_idx: int) -> bool:
        self._clear_mix()
        self._audio_out.setMuted(False)
        self._player.setActiveAudioTrack(track_idx)
        ok = (self._player.activeAudioTrack() == track_idx)
        label = self._track_label(track_idx)
        self.audio_mode_changed.emit(f"Playing: {label}" if ok else "Playing: (track unavailable)")
        return ok

    def set_audio_mix_file(self, path: str):
        """Play a pre-rendered mix (from review_workers.MixRenderWorker),
        slaved to the master video's position/playback state; the master's
        own audio is muted so only the mix is heard."""
        self._teardown_mix_player()

        self._mix_player = QMediaPlayer(self)
        self._mix_audio_out = QAudioOutput(self)
        self._mix_player.setAudioOutput(self._mix_audio_out)
        self._mix_player.setSource(QUrl.fromLocalFile(str(Path(path).resolve())))

        self._audio_out.setMuted(True)
        self._mix_player.setPosition(int(self.current_position() * 1000))
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._mix_player.play()

        self._mix_resync_timer = QTimer(self)
        self._mix_resync_timer.setInterval(_MIX_RESYNC_MS)
        self._mix_resync_timer.timeout.connect(self._resync_mix)
        self._mix_resync_timer.start()

        self.audio_mode_changed.emit("Playing mix")

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def shutdown(self):
        self._poll_timer.stop()
        self._teardown_mix_player()
        self._player.stop()

    # ── Internal: video frames + duration + state ────────────────────────────

    def _on_video_frame(self, frame):
        if not frame.isValid():
            return
        secs = frame.startTime() / 1_000_000.0 if frame.startTime() >= 0 else self.current_position()
        self.frame_ready.emit(frame.toImage(), secs)

    def _on_duration_changed(self, duration_ms: int):
        self._duration = duration_ms / 1000.0
        if self._duration > 0:
            self.duration_known.emit(self._duration, self._fps)
            self._prime_first_frame()

    def _prime_first_frame(self):
        """A freshly loaded player in StoppedState delivers no frames from a
        bare seek() — nudge it into PausedState with a brief play/pause so
        the caller's first seek (typically right after load) actually shows
        something instead of a blank preview. Muted and flagged so this
        internal pulse doesn't emit a spurious state_changed(True) or make
        a sound.

        If a real seek() arrives before the pulse's own timer would have
        ended it, seek() ends it early instead — otherwise the delayed
        auto-pause can fire *after* the caller's seek and cut off the frame
        that seek was trying to show.
        """
        self._priming = True
        self._prime_was_muted = self._audio_out.isMuted()
        self._audio_out.setMuted(True)
        self._player.play()
        self._prime_timer.start(250)

    def _end_prime(self):
        if not self._priming:
            return
        self._prime_timer.stop()
        self._player.pause()
        self._audio_out.setMuted(self._prime_was_muted)
        self._priming = False

    def _on_playback_state_changed(self, state):
        if self._priming:
            return   # internal warm-up pulse — not a real state change to report
        playing = (state == QMediaPlayer.PlaybackState.PlayingState)
        if playing:
            self._poll_timer.start()
            if self._mix_player is not None:
                self._mix_player.play()
        else:
            self._poll_timer.stop()
            if self._mix_player is not None:
                self._mix_player.pause()
        self.state_changed.emit(playing)

    def _on_native_position_changed(self, position_ms: int):
        self.position_changed.emit(position_ms / 1000.0)

    def _poll_position(self):
        self.position_changed.emit(self.current_position())

    def _on_error(self, err, err_string: str):
        self.error.emit(err_string or str(err))

    # ── Internal: mix-audio slave player ─────────────────────────────────────

    def _resync_mix(self):
        if self._mix_player is None:
            return
        drift = abs(self._mix_player.position() - self._player.position()) / 1000.0
        if drift > _MIX_DRIFT_THRESHOLD_S:
            self._mix_player.setPosition(self._player.position())

    def _teardown_mix_player(self):
        if self._mix_resync_timer is not None:
            self._mix_resync_timer.stop()
            self._mix_resync_timer = None
        if self._mix_player is not None:
            self._mix_player.stop()
            self._mix_player.deleteLater()
            self._mix_player = None
        if self._mix_audio_out is not None:
            self._mix_audio_out.deleteLater()
            self._mix_audio_out = None

    def _clear_mix(self):
        if self._mix_player is not None:
            self._teardown_mix_player()
            self._audio_out.setMuted(False)

    def _track_label(self, track_idx: int) -> str:
        for t in self._tracks:
            if getattr(t, "audio_index", None) == track_idx:
                return t.title or f"track {track_idx}"
        return f"track {track_idx}"


def make_engine(parent=None) -> PlaybackEngine:
    """Factory: picks the playback implementation. Pure Qt passed the v1.4
    spike on this codec/track combination, so this always returns
    QtPlaybackEngine today; kept as a single seam if a future platform
    (e.g. Steam Deck) needs a different backend."""
    return QtPlaybackEngine(parent)
