"""review_tab.py — the Review tab: ReviewSession (position authority) + ReviewTab.

Assembles everything built for v1.4: review_playback.PlaybackEngine for
video/audio, review_workers for background scans/renders/extractions,
core.scopes/core.audio_peaks/core.spectrogram for the data, and the five
widgets in widgets/ for the UI. ReviewSession is a plain state+signal
container (no engine reference) — ReviewTab is the single place that reads
engine/worker signals, updates the session, and issues commands back out,
so there's one funnel for every seek regardless of which control asked for it.

Loading happens via `load_master(path)` — called directly, from the "Load
master…" browse button, from a dropped `.mov`/`.mp4` file, or from the
merge tab's "Review" button (wired in main.py). The last-loaded path is
persisted in settings so re-opening the app can offer it again.
"""

import time
from pathlib import Path
from typing import Optional

import numpy as np
from PySide6.QtCore import Qt, QObject, QTimer, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSpinBox, QFrame, QFileDialog,
    QCheckBox,
)

import theme
from thread_utils import settle
from ffmpeg_runner import get_ffmpeg, get_app_dir
from probe import StreamInfo, pix_fmt_info
from review_playback import make_engine
from review_workers import (
    TrackScanWorker, PeakScanWorker, SpectrogramWorker, MixRenderWorker, FrameFetchWorker,
)
from core.scopes import rescale_to_bit_depth
from core.review_media import mix_cache_key, snapshot_filename
from widgets.video_view import ZoomableVideoView
from widgets.jog_wheel import JogWheel
from widgets.scopes_panel import ScopesPanel
from widgets.audio_lanes import AudioLaneStack
from widgets.trackbar import OverviewTrackbar
from widgets.timeline import secs_to_tc

_MIX_DEBOUNCE_MS = 300
_SPEC_DEBOUNCE_MS = 250
_APPROX_SCOPE_THROTTLE_S = 0.2
_APPROX_SCOPE_MAX_DIM = 640   # shrink via Qt before touching numpy at all — see _update_approx_scope
_SPEC_TILE_CACHE_MAX = 16


def _label_tracks(tracks: list) -> dict:
    """Best-effort human labels — masters don't carry descriptive per-track
    title metadata yet (see DEVELOPMENT.md's "Future ideas": metadata
    preservation). Heuristic, not read from the file: first ALAC track =
    WAV backup (the app's only lossless audio codec), first AAC = Camera
    mic, any further AAC = Mix."""
    labels = {}
    aac_seen = 0
    for t in tracks:
        codec = (t.codec or "").lower()
        if codec == "alac":
            label = "WAV backup"
        elif codec == "aac":
            aac_seen += 1
            label = "Camera mic" if aac_seen == 1 else "Mix"
        else:
            label = f"Track {t.audio_index + 1}"
        ch = "mono" if t.channels == 1 else "stereo" if t.channels == 2 else f"{t.channels}ch"
        sublabel = f"{(t.codec or '?').upper()} · {ch}"
        labels[t.audio_index] = (label, sublabel)
    return labels


class ReviewSession(QObject):
    """Single source of truth for what the UI displays — duration, fps,
    position, viewport, playing state, loaded tracks and the tick-set.
    Holds no engine/worker references; ReviewTab is what acts on this
    state's signals."""
    duration_changed = Signal(float, float)   # secs, fps
    position_changed = Signal(float)
    viewport_changed = Signal(float, float)
    playing_changed  = Signal(bool)
    tracks_changed   = Signal(list)           # list[AudioTrackInfo]
    ticked_changed   = Signal(list)           # list[int] audio_index, sorted

    def __init__(self, parent=None):
        super().__init__(parent)
        self.duration = 0.0
        self.fps = 29.97
        self.position = 0.0
        self.view_t0 = 0.0
        self.view_t1 = 0.0
        self.playing = False
        self.tracks: list = []
        self.ticked: set = set()
        self.scrubbing = False   # True while the user is dragging — suppresses engine echoes

    def set_duration(self, secs: float, fps: float):
        self.duration = secs
        self.fps = fps if fps > 0 else self.fps
        self.view_t0, self.view_t1 = 0.0, secs
        self.duration_changed.emit(secs, self.fps)
        self.viewport_changed.emit(self.view_t0, self.view_t1)

    def set_position(self, secs: float, from_engine: bool = False):
        if from_engine and self.scrubbing:
            return
        self.position = max(0.0, min(secs, self.duration))
        self.position_changed.emit(self.position)

    def set_viewport(self, t0: float, t1: float):
        self.view_t0, self.view_t1 = t0, t1
        self.viewport_changed.emit(t0, t1)

    def set_playing(self, playing: bool):
        self.playing = playing
        self.playing_changed.emit(playing)

    def set_tracks(self, tracks: list):
        self.tracks = tracks
        self.ticked = {t.audio_index for t in tracks}
        self.tracks_changed.emit(tracks)

    def toggle_track(self, idx: int, checked: bool):
        if checked:
            self.ticked.add(idx)
        else:
            self.ticked.discard(idx)
        self.ticked_changed.emit(sorted(self.ticked))


class ReviewTab(QWidget):
    def __init__(self, settings=None, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._session = ReviewSession(self)
        use_software = bool(settings.get("review_software_decode", False)) if settings else False
        self._engine = make_engine(self, use_software=use_software)
        self._path: str = ""
        self._video_info: Optional[StreamInfo] = None
        self._chapters: list = []
        self._track_labels: dict = {}
        self._workers: list = []          # tracked-set — settle()d on shutdown()
        self._current_mix_worker = None   # at most one full-file mix render in flight
        self._spec_cache: dict = {}       # (track_idx, t0, t1) -> QImage, capped LRU
        self._spec_cache_order: list = []
        self._last_approx_scope_t = 0.0

        self._setup_ui()
        self._wire_engine()
        self._wire_session()
        self._wire_widgets()
        self._restyle()
        ctrl = theme.controller()
        if ctrl is not None:
            ctrl.changed.connect(self._restyle)

        self._mix_timer = QTimer(self)
        self._mix_timer.setSingleShot(True)
        self._mix_timer.setInterval(_MIX_DEBOUNCE_MS)
        self._mix_timer.timeout.connect(self._apply_tick_set)

        self._spec_timer = QTimer(self)
        self._spec_timer.setSingleShot(True)
        self._spec_timer.setInterval(_SPEC_DEBOUNCE_MS)
        self._spec_timer.timeout.connect(self._refresh_spectrograms)

    # ── UI construction ───────────────────────────────────────────────────────

    def _setup_ui(self):
        self.setAcceptDrops(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        header_row = QHBoxLayout()
        self._browse_btn = QPushButton("Load master…")
        self._browse_btn.clicked.connect(self._browse_for_master)
        self._loaded_name_label = QLabel("No master loaded")
        header_row.addWidget(self._browse_btn)
        header_row.addWidget(self._loaded_name_label)
        header_row.addStretch()
        self._software_decode_check = QCheckBox("Software decode")
        self._software_decode_check.setToolTip(
            "Play video without GPU hardware acceleration. Turn this on if the app has\n"
            "crashed or the screen has gone blank while playing footage in this tab —\n"
            "some GPUs can't reliably hardware-decode 4K 10-bit video and this avoids\n"
            "that path entirely, at the cost of smoother playback. Takes effect after\n"
            "you restart the app.")
        if self._settings is not None:
            self._software_decode_check.setChecked(
                bool(self._settings.get("review_software_decode", False)))
        self._software_decode_check.toggled.connect(self._on_software_decode_toggled)
        header_row.addWidget(self._software_decode_check)
        root.addLayout(header_row)

        top_row = QHBoxLayout()
        top_row.setSpacing(12)

        preview_col = QVBoxLayout()
        self._video_view = ZoomableVideoView()
        preview_col.addWidget(self._video_view, 1)

        zoom_row = QHBoxLayout()
        self._zoom_fit_btn = QPushButton("Fit")
        self._zoom_1to1_btn = QPushButton("1:1")
        self._zoom_spin = QSpinBox()
        self._zoom_spin.setRange(10, 800)
        self._zoom_spin.setValue(100)
        self._zoom_spin.setSuffix("%")
        zoom_row.addWidget(self._zoom_fit_btn)
        zoom_row.addWidget(self._zoom_1to1_btn)
        zoom_row.addWidget(self._zoom_spin)
        zoom_row.addStretch()
        preview_col.addLayout(zoom_row)

        transport_row = QHBoxLayout()
        self._prev_btn = QPushButton("|◀")          # prev chapter
        self._step_back_btn = QPushButton("<")       # step back one frame
        self._play_btn = QPushButton("▶")            # play / pause (text swaps to "||")
        self._step_fwd_btn = QPushButton(">")         # step forward one frame
        self._next_btn = QPushButton("▶|")           # next chapter
        self._jog = JogWheel()
        self._snapshot_btn = QPushButton("●")         # snapshot (avoids emoji — see _restyle)
        self._tc_label = QLabel("00:00:00:00")
        self._icon_buttons = (self._prev_btn, self._step_back_btn, self._play_btn,
                              self._step_fwd_btn, self._next_btn, self._snapshot_btn)
        for b in self._icon_buttons:
            b.setFixedSize(30, 26)
        transport_row.addWidget(self._prev_btn)
        transport_row.addWidget(self._step_back_btn)
        transport_row.addWidget(self._play_btn)
        transport_row.addWidget(self._step_fwd_btn)
        transport_row.addWidget(self._next_btn)
        transport_row.addWidget(self._jog)
        transport_row.addWidget(self._snapshot_btn)
        transport_row.addStretch()
        transport_row.addWidget(self._tc_label)
        preview_col.addLayout(transport_row)

        self._status_label = QLabel("")
        preview_col.addWidget(self._status_label)

        top_row.addLayout(preview_col, 3)

        self._scopes = ScopesPanel()
        top_row.addWidget(self._scopes, 2)

        root.addLayout(top_row, 3)

        self._lanes = AudioLaneStack()
        root.addWidget(self._lanes, 2)

        self._trackbar = OverviewTrackbar()
        root.addWidget(self._trackbar)

        self._empty_label = QLabel("Load a master to review it here.")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._empty_label)
        self._set_loaded_visible(False)

    def _set_loaded_visible(self, loaded: bool):
        for w in (self._video_view, self._scopes, self._lanes, self._trackbar):
            w.setVisible(loaded)
        self._empty_label.setVisible(not loaded)

    # ── Loading ───────────────────────────────────────────────────────────────

    def _browse_for_master(self):
        start_dir = self._settings.get("last_review_source", "") if self._settings else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Load master to review", str(Path(start_dir).parent) if start_dir else "",
            "Video files (*.mov *.mp4);;All files (*)")
        if path:
            self.load_master(path)

    def _on_software_decode_toggled(self, checked: bool):
        if self._settings is not None:
            self._settings.set("review_software_decode", checked)
            self._settings.save()
        self._status_label.setText(
            "Software decode " + ("enabled" if checked else "disabled")
            + " — restart the app for this to take effect.")

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any(
            u.toLocalFile().lower().endswith((".mov", ".mp4")) for u in event.mimeData().urls()
        ):
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith((".mov", ".mp4")):
                self.load_master(path)
                break

    def load_master(self, path: str):
        self._path = str(path)
        self._set_loaded_visible(True)
        self._loaded_name_label.setText(Path(path).name)
        self._status_label.setText(f"Loading {Path(path).name}…")
        if self._settings is not None:
            self._settings.set("last_review_source", str(path))
            self._settings.save()

        ff, fp = get_ffmpeg()
        w = TrackScanWorker(fp, self._path)
        w.tracks_ready.connect(self._on_tracks_ready)
        self._track(w)
        w.start()

    def _on_tracks_ready(self, video_info: StreamInfo, audio_tracks: list, chapters: list):
        self._video_info = video_info
        self._chapters = chapters
        self._track_labels = _label_tracks(audio_tracks)

        bit_depth, subsampling = pix_fmt_info(video_info.pix_fmt)
        self._scopes.set_badges(
            codec=video_info.codec, bit_depth=bit_depth,
            color_space=video_info.color_space, subsampling=subsampling,
            is_hdr=video_info.is_hdr,
        )

        lane_rows = [(t.audio_index, *self._track_labels.get(t.audio_index, (f"Track {t.audio_index+1}", "")))
                    for t in audio_tracks]
        self._lanes.set_tracks(lane_rows)

        self._session.set_tracks(audio_tracks)
        self._engine.load(self._path, audio_tracks, fps=video_info.fps_float or 29.97)
        self._status_label.setText("")

        ff, fp = get_ffmpeg()
        peak_w = PeakScanWorker(ff, self._path, [t.audio_index for t in audio_tracks],
                                duration=video_info.duration)
        peak_w.pyramid_ready.connect(self._on_pyramid_ready)
        self._track(peak_w)
        peak_w.start()

    def _on_pyramid_ready(self, track_idx: int, pyramid):
        peaks = pyramid.peaks_for_view(0.0, pyramid.duration, 600)
        self._lanes.set_peaks(track_idx, peaks)
        if pyramid.levels:
            coarsest = pyramid.levels[-1]
            envelope = np.maximum(np.abs(coarsest[:, 0]), np.abs(coarsest[:, 1]))
            self._trackbar.set_envelope(envelope)

    # ── Engine wiring ─────────────────────────────────────────────────────────

    def _wire_engine(self):
        self._engine.duration_known.connect(self._session.set_duration)
        self._engine.position_changed.connect(
            lambda s: self._session.set_position(s, from_engine=True))
        self._engine.frame_ready.connect(self._on_frame_ready)
        self._engine.state_changed.connect(self._on_engine_state_changed)
        self._engine.audio_mode_changed.connect(self._lanes.set_readout)
        self._engine.error.connect(lambda msg: self._status_label.setText(f"Playback error: {msg}"))

    def _on_engine_state_changed(self, playing: bool):
        self._session.set_playing(playing)
        self._play_btn.setText("||" if playing else "▶")
        self._video_view.set_playing(playing)
        if not playing:
            self._request_exact_scope()

    def _on_frame_ready(self, image: QImage, secs: float):
        self._video_view.set_frame(image)
        if self._session.playing:
            now = time.monotonic()
            if now - self._last_approx_scope_t >= _APPROX_SCOPE_THROTTLE_S:
                self._last_approx_scope_t = now
                self._update_approx_scope(image)

    def _update_approx_scope(self, image: QImage):
        # Shrink via Qt (cheap, native) BEFORE copying anything into numpy —
        # a live histogram doesn't need a 4K source, and converting/copying
        # a full 3840x2160 buffer up to five times a second was heavy enough
        # to exhaust memory on modest hardware (see DEVELOPMENT.md's v1.4
        # progress notes for the crash this fixed). core.scopes also caps
        # the pixel count it'll process, as a second line of defence.
        if max(image.width(), image.height()) > _APPROX_SCOPE_MAX_DIM:
            image = image.scaled(_APPROX_SCOPE_MAX_DIM, _APPROX_SCOPE_MAX_DIM,
                                 Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.FastTransformation)
        img8 = image.convertToFormat(QImage.Format.Format_RGB888)
        w, h = img8.width(), img8.height()
        if w <= 0 or h <= 0:
            return
        ptr = img8.constBits()
        arr = np.frombuffer(bytes(ptr), dtype=np.uint8).reshape(h, img8.bytesPerLine())[:, : w * 3].reshape(h, w, 3)
        self._scopes.set_exact(False)
        self._scopes.set_frame(arr, bit_depth=8)

    def _request_exact_scope(self):
        if not self._path or self._video_info is None:
            return
        ff, fp = get_ffmpeg()
        w = FrameFetchWorker(ff, self._path, secs=self._session.position, mode="frame",
                             width=self._video_info.width, height=self._video_info.height)
        w.exact_frame_ready.connect(self._on_exact_frame)
        self._track(w)
        w.start()

    def _on_exact_frame(self, arr16, secs: float):
        arr10 = rescale_to_bit_depth(arr16, bit_depth=10, src_max=65535)
        self._scopes.set_exact(True)
        self._scopes.set_frame(arr10, bit_depth=10)

    # ── Session wiring ────────────────────────────────────────────────────────

    def _wire_session(self):
        self._session.duration_changed.connect(lambda secs, fps: self._trackbar.set_duration(secs))
        self._session.position_changed.connect(self._on_session_position)
        self._session.viewport_changed.connect(self._on_viewport_changed)
        self._session.tracks_changed.connect(lambda tracks: None)

    def _on_session_position(self, secs: float):
        self._trackbar.set_position(secs)
        self._tc_label.setText(secs_to_tc(secs, self._session.fps))
        t0, t1 = self._session.view_t0, self._session.view_t1
        span = max(1e-6, t1 - t0)
        frac = (secs - t0) / span if t0 <= secs <= t1 else None
        self._lanes.set_playhead(frac)

    def _on_viewport_changed(self, t0: float, t1: float):
        self._trackbar.set_viewport(t0, t1)
        if self._lanes.current_mode() == "spec":
            self._spec_timer.start()

    # ── Widget wiring ─────────────────────────────────────────────────────────

    def _wire_widgets(self):
        self._zoom_fit_btn.clicked.connect(self._video_view.set_zoom_fit)
        self._zoom_1to1_btn.clicked.connect(self._on_zoom_1to1_clicked)
        self._zoom_spin.valueChanged.connect(
            lambda v: self._video_view.set_zoom_percent(float(v)))
        self._video_view.zoom_changed.connect(self._on_zoom_changed)

        self._play_btn.clicked.connect(self._engine.toggle)
        self._step_back_btn.clicked.connect(lambda: self._step(-1))
        self._step_fwd_btn.clicked.connect(lambda: self._step(1))
        self._prev_btn.clicked.connect(self._prev_chapter)
        self._next_btn.clicked.connect(self._next_chapter)
        self._jog.frame_delta.connect(self._step)
        self._snapshot_btn.clicked.connect(self._take_snapshot)

        self._trackbar.position_changed.connect(self._seek)
        self._trackbar.viewport_changed.connect(self._session.set_viewport)

        self._lanes.track_toggled.connect(self._on_track_toggled)
        self._lanes.mode_changed.connect(self._on_lane_mode_changed)

    def _on_zoom_1to1_clicked(self):
        self._video_view.set_zoom_1to1()
        self._zoom_spin.blockSignals(True)
        self._zoom_spin.setValue(100)
        self._zoom_spin.blockSignals(False)

    def _on_zoom_changed(self, frac: float):
        pct = round(frac * 100)
        if self._zoom_spin.value() != pct:
            self._zoom_spin.blockSignals(True)
            self._zoom_spin.setValue(pct)
            self._zoom_spin.blockSignals(False)

    # ── Transport ─────────────────────────────────────────────────────────────

    def _seek(self, secs: float):
        self._session.set_position(secs)
        self._engine.seek(secs)
        if not self._session.playing:
            self._request_exact_scope()

    def _step(self, n: int):
        self._engine.step_frames(n)

    def _prev_chapter(self):
        pos = self._session.position
        target = 0.0
        for c in reversed(self._chapters):
            if c.start < pos - 0.5:
                target = c.start
                break
        self._seek(target)

    def _next_chapter(self):
        pos = self._session.position
        target = self._session.duration
        for c in self._chapters:
            if c.start > pos + 0.5:
                target = c.start
                break
        self._seek(target)

    def _take_snapshot(self):
        if not self._path or self._video_info is None:
            return
        secs = self._session.position
        self._video_view.flash_snapshot()
        frame_idx = round(secs * self._session.fps)
        out_path = snapshot_filename(self._path, frame_idx)
        ff, fp = get_ffmpeg()
        w = FrameFetchWorker(ff, self._path, secs=secs, mode="snapshot", snapshot_out=str(out_path))
        w.snapshot_saved.connect(lambda p: self._status_label.setText(f"Snapshot saved — {p}"))
        w.error.connect(lambda msg: self._status_label.setText(f"Snapshot failed: {msg}"))
        self._track(w)
        w.start()

    # ── Audio tick-set / mix ──────────────────────────────────────────────────

    def _on_track_toggled(self, idx: int, checked: bool):
        self._session.toggle_track(idx, checked)
        self._mix_timer.start()

    def _apply_tick_set(self):
        # A full-file render is long enough (minutes, on slow/cloud storage)
        # that a second tick change before it finishes must cancel the first
        # rather than let two ffmpeg processes contend for the same disk.
        if self._current_mix_worker is not None:
            self._current_mix_worker.cancel()
            self._current_mix_worker = None

        ticked = sorted(self._session.ticked)
        if not ticked:
            self._lanes.set_readout("No tracks ticked — silent")
            return
        # HybridPlaybackEngine has no single "master" player whose active
        # track can be flipped, so it always declines a native switch —
        # fall through to rendering a one-track file the same way a real
        # multi-track mix is handled, rather than assuming this succeeds.
        if len(ticked) == 1 and self._engine.set_audio_single(ticked[0]):
            label = self._track_labels.get(ticked[0], (f"track {ticked[0]}",))[0]
            self._lanes.set_readout(f"Playing: {label}")
            return

        self._lanes.set_readout("Rendering mix…" if len(ticked) > 1 else "Rendering…")
        key = mix_cache_key(ticked)
        out_path = get_app_dir() / "_temp" / f"review_mix_{key}.m4a"
        out_path.parent.mkdir(exist_ok=True)
        ff, fp = get_ffmpeg()
        w = MixRenderWorker(ff, self._path, ticked, str(out_path), cache_key=key)
        w.mix_ready.connect(self._on_mix_ready)
        w.error.connect(lambda k, msg: self._lanes.set_readout(f"Render failed: {msg}"))
        w.finished.connect(lambda w=w: self._on_mix_worker_finished(w))
        self._current_mix_worker = w
        self._track(w)
        w.start()

    def _on_mix_worker_finished(self, w):
        if self._current_mix_worker is w:
            self._current_mix_worker = None

    def _on_mix_ready(self, cache_key: str, out_path: str):
        if cache_key != mix_cache_key(sorted(self._session.ticked)):
            return   # tick-set changed again while this was rendering — stale result
        self._engine.set_audio_mix_file(out_path)
        names = [self._track_labels.get(i, (f"track {i}",))[0] for i in sorted(self._session.ticked)]
        if len(names) == 1:
            self._lanes.set_readout(f"Playing: {names[0]}")
        else:
            self._lanes.set_readout("Playing mix: " + " + ".join(names))

    # ── Spectral view ─────────────────────────────────────────────────────────

    def _on_lane_mode_changed(self, mode: str):
        if mode == "spec":
            self._spec_timer.start()

    def _refresh_spectrograms(self):
        if self._lanes.current_mode() != "spec" or not self._path:
            return
        t0, t1 = self._session.view_t0, self._session.view_t1
        ff, fp = get_ffmpeg()
        for idx in self._session.ticked:
            key = (idx, round(t0, 1), round(t1, 1))
            cached = self._spec_cache.get(key)
            if cached is not None:
                self._lanes.set_spectrogram(idx, cached)
                continue
            w = SpectrogramWorker(ff, self._path, idx, t0, t1)
            w.image_ready.connect(self._on_spectrogram_ready)
            self._track(w)
            w.start()

    def _on_spectrogram_ready(self, track_idx: int, t0: float, t1: float, arr):
        h, wdt = arr.shape[0], arr.shape[1]
        arr = np.ascontiguousarray(arr)
        img = QImage(arr.data, wdt, h, arr.strides[0], QImage.Format.Format_RGB888).copy()
        key = (track_idx, round(t0, 1), round(t1, 1))
        self._spec_cache[key] = img
        self._spec_cache_order.append(key)
        if len(self._spec_cache_order) > _SPEC_TILE_CACHE_MAX:
            stale = self._spec_cache_order.pop(0)
            self._spec_cache.pop(stale, None)
        self._lanes.set_spectrogram(track_idx, img)

    # ── Worker lifetime ───────────────────────────────────────────────────────

    def _track(self, worker):
        self._workers.append(worker)
        worker.finished.connect(lambda w=worker: self._untrack(w))

    def _untrack(self, worker):
        if worker in self._workers:
            self._workers.remove(worker)

    def shutdown(self):
        """Wait out all worker threads (called from MainWindow.closeEvent).

        Only drops a worker's reference once settle() confirms it actually
        finished — a worker still blocked on slow/contended I/O past the
        timeout stays tracked rather than being silently abandoned, which
        would reintroduce the exact "destroy a running QThread" crash the
        Phase-1 stability pass fixed elsewhere in the app.
        """
        if self._current_mix_worker is not None:
            self._current_mix_worker.cancel()
        for w in list(self._workers):
            if hasattr(w, "cancel"):
                w.cancel()
        self._workers = [w for w in self._workers if not settle(w, 10000)]
        self._engine.shutdown()

    # ── Theming ───────────────────────────────────────────────────────────────

    def _restyle(self):
        p = theme.active_palette()
        self._empty_label.setStyleSheet(f"color:{p.text_mute}; font-size:14px;")
        self._status_label.setStyleSheet(f"color:{p.text_mute}; font-size:11px;")
        self._loaded_name_label.setStyleSheet(f"color:{p.text_mute}; font-size:12px;")
        self._tc_label.setStyleSheet(
            f"color:{p.text_dim}; font-size:12px; font-family:monospace;")
        # The app's global QPushButton padding (14px each side) leaves no room
        # for a compact icon button's glyph at a small fixed size — override
        # it to 0 here, the same fix merge_tab.py's row-reorder buttons use.
        icon_style = (
            f"QPushButton {{ background:{p.btn_bg}; color:{p.text}; border:1px solid {p.border_hi}; "
            "border-radius:4px; padding:0px; font-size:13px; }"
            f"QPushButton:hover {{ background:{p.hover_bg}; border-color:{p.accent}; }}"
            f"QPushButton:pressed {{ background:{p.press_bg}; }}")
        for b in self._icon_buttons:
            b.setStyleSheet(icon_style)
