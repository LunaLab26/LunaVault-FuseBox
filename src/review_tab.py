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
from PySide6.QtCore import Qt, QObject, QTimer, QRectF, QPointF, QSize, Signal
from PySide6.QtGui import QImage, QShortcut, QKeySequence, QIcon, QPixmap, QPainter, QPen, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSlider, QFrame, QFileDialog,
    QCheckBox, QStyle, QScrollArea, QComboBox,
)

import theme
from thread_utils import settle
from ffmpeg_runner import get_ffmpeg, get_app_dir
from probe import StreamInfo, pix_fmt_info
from review_playback import make_engine, is_risky_hw_decode_profile, HybridPlaybackEngine
from review_workers import (
    TrackScanWorker, PeakScanWorker, SpectrogramWorker, MixRenderWorker, FrameFetchWorker,
    ThumbnailStripWorker, ProxyRenderWorker,
)
from core.scopes import rescale_to_bit_depth
from core.review_media import mix_cache_key, snapshot_filename, proxy_cache_path
from widgets.video_view import ZoomableVideoView
from widgets.jog_wheel import JogWheel
from widgets.scopes_panel import ScopesPanel
from widgets.audio_lanes import AudioLaneStack
from widgets.trackbar import OverviewTrackbar
from widgets.timeline import secs_to_tc
from widgets.spinner import LoadingSpinner

_MIX_DEBOUNCE_MS = 300
_SPEC_DEBOUNCE_MS = 250
_ZOOM_FRAME_DEBOUNCE_MS = 200
_APPROX_SCOPE_THROTTLE_S = 0.2

_SOFTWARE_DECODE_TOOLTIP = (
    "Play video without GPU hardware acceleration. Turn this on if the app has\n"
    "crashed or the screen has gone blank while playing footage in this tab —\n"
    "some GPUs can't reliably hardware-decode 4K 10-bit video and this avoids\n"
    "that path entirely, at the cost of smoother playback. Applies immediately.")
_SOFTWARE_DECODE_FORCED_TOOLTIP = (
    "Disabled for this master — its 4K+ 10-bit HEVC video is a confirmed-dangerous\n"
    "combination: hardware decode of this exact content class has caused the native\n"
    "decoder to consume 14+ GB of memory and made the whole system unresponsive enough\n"
    "to drop an active remote-desktop session. Software decode is forced for this file\n"
    "to keep the app — and the rest of your system — stable.")
_SOFTWARE_DECODE_RISKY_OVERRIDE_TOOLTIP = (
    "⚠ You have turned off the automatic safety net (Developer options) for this\n"
    "master's 4K+ 10-bit HEVC video. Unchecking this box hands that video to the GPU\n"
    "decoder — a combination that has hard-crashed the WHOLE computer (not just this\n"
    "app) on multiple different machines. Leave this checked unless you specifically\n"
    "mean to test GPU decode and have saved your work.")
_APPROX_SCOPE_MAX_DIM = 640   # shrink via Qt before touching numpy at all — see _update_approx_scope
_SPEC_TILE_CACHE_MAX = 16
_PROXY_HEIGHT = 480
_FAST_PREVIEW_TOOLTIP = (
    "Play a small, pre-rendered 480p copy instead of the full master — plain 8-bit\n"
    "H.264 that any GPU decodes instantly, unlike the master's own resolution/codec/\n"
    "bit-depth. Makes scrubbing and playback much smoother, especially on 4K or 10-bit\n"
    "footage. Built once per master in the background (see the spinner while it's not\n"
    "yet available); the exact-frame scopes reading, snapshots and the finished export\n"
    "are completely unaffected — this only changes what plays back on screen.")
_THUMBNAIL_COUNT = 24   # filmstrip slots across the OverviewTrackbar


def _camera_icon(color: str, size: int = 20) -> QIcon:
    """A flat outline camera glyph in `color` — the snapshot button's icon,
    drawn (rather than a Qt standard pixmap) so it matches the theme accent.
    Re-generated on theme change from _restyle so it re-tints."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), max(1.4, size * 0.08))
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    s = size
    p.drawRoundedRect(QRectF(s * 0.12, s * 0.32, s * 0.76, s * 0.50), s * 0.08, s * 0.08)  # body
    p.drawRoundedRect(QRectF(s * 0.34, s * 0.20, s * 0.22, s * 0.13), s * 0.04, s * 0.04)  # viewfinder bump
    p.drawEllipse(QPointF(s * 0.50, s * 0.58), s * 0.15, s * 0.15)                          # lens
    p.end()
    return QIcon(pm)


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


class _LoadingIndicator(QWidget):
    """Spinner + caption pair for a section header's `right` slot — see
    ReviewTab._loading_indicator. The whole pair shows/hides together (a
    lone caption with no spinning arc would just read as a stuck message)."""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self._spinner = LoadingSpinner()
        self._label = QLabel(text)
        lay.addWidget(self._spinner)
        lay.addWidget(self._label)
        self.setVisible(False)

    def start(self):
        self._spinner.start()
        self.setVisible(True)

    def stop(self):
        self._spinner.stop()
        self.setVisible(False)

    def restyle(self, palette):
        self._label.setStyleSheet(f"color:{palette.text_mute}; font-size:11px;")


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
        self._engine = self._new_engine(use_software)
        self._path: str = ""
        self._video_info: Optional[StreamInfo] = None
        self._auto_forced_software = False   # True while a risky-content override is active
        self._chapters: list = []
        self._manifest = None             # Optional[core.manifest.Manifest] for the loaded master
        self._clip_window_end: Optional[float] = None   # auto-pause point while viewing one
                                                          # archival clip's original (see
                                                          # _on_video_source_changed); None on the
                                                          # baseline (play the whole master normally)
        self._track_labels: dict = {}
        self._workers: list = []          # tracked-set — settle()d on shutdown()
        self._current_mix_worker = None   # at most one full-file mix render in flight
        self._thumb_worker = None         # at most one thumbnail-strip extraction in flight
        self._proxy_worker = None         # at most one 480p proxy render in flight
        self._proxy_path: Optional[str] = None   # ready proxy for the CURRENTLY loaded master, or None
        self._using_proxy = False         # True while the engine is playing the proxy, not self._path
        self._spec_cache: dict = {}       # (track_idx, t0, t1) -> QImage, capped LRU
        self._spec_cache_order: list = []
        self._pyramids: dict = {}         # track_idx -> PeakPyramid, kept so lanes can re-crop to the viewport
        self._last_approx_scope_t = 0.0
        self._sections: list = []         # section frames — restyled + shown/hidden together
        self._section_titles: list = []

        self._setup_ui()
        self._wire_engine()
        self._wire_session()
        self._wire_widgets()
        self._setup_shortcuts()
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

        self._zoom_frame_timer = QTimer(self)
        self._zoom_frame_timer.setSingleShot(True)
        self._zoom_frame_timer.setInterval(_ZOOM_FRAME_DEBOUNCE_MS)
        self._zoom_frame_timer.timeout.connect(self._request_exact_scope)

    # ── UI construction ───────────────────────────────────────────────────────

    def _section(self, title: str, right: Optional[QWidget] = None):
        """A titled, bordered group card — mirrors merge_tab._section so the
        Review tab reads as the same app. Returns (frame, body_layout); add
        content to body_layout. Tracked for restyle + show/hide together."""
        frame = QFrame()
        frame.setObjectName("review_section")
        v = QVBoxLayout(frame)
        v.setContentsMargins(12, 9, 12, 11)
        v.setSpacing(8)
        hdr = QHBoxLayout()
        lbl = QLabel(title)
        lbl.setObjectName("review_section_title")
        hdr.addWidget(lbl)
        if right is not None:
            hdr.addStretch()
            hdr.addWidget(right)
        v.addLayout(hdr)
        body = QVBoxLayout()
        body.setSpacing(7)
        v.addLayout(body)
        self._sections.append(frame)
        self._section_titles.append(lbl)
        return frame, body

    def _loading_indicator(self, text: str) -> "_LoadingIndicator":
        """Spinner + caption, meant for a section header's `right` slot.
        Hidden by default; call `.start()` when the section's background
        worker begins and `.stop()` when it's done, so a slow thumbnail/
        waveform extraction reads as "working", not stalled."""
        return _LoadingIndicator(text)

    def _make_vline(self) -> QFrame:
        """A thin vertical divider — groups the transport row by function
        (navigation / jog shuttle / snapshot action) instead of one flat row."""
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFixedWidth(1)
        self._transport_dividers.append(line)
        return line

    def _setup_ui(self):
        self.setAcceptDrops(True)
        st = self.style()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Everything lives in a scrollable content container — under 150% Windows
        # display scaling (or just a short window) this tab's stacked sections
        # (preview+scopes / audio / overview) can easily exceed the available
        # height; without this, Qt would just refuse to shrink below their
        # combined minimums and the window could clip off-screen with no way to
        # reach the rest. Mirrors merge_tab.py's identical fix.
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self._scroll)
        self._content = QWidget()
        self._scroll.setWidget(self._content)
        root = QVBoxLayout(self._content)
        root.setContentsMargins(14, 14, 16, 14)   # room for the scrollbar
        root.setSpacing(10)

        header_row = QHBoxLayout()
        self._browse_btn = QPushButton("Load master…")
        self._browse_btn.clicked.connect(self._browse_for_master)
        self._loaded_name_label = QLabel("No master loaded")
        header_row.addWidget(self._browse_btn)
        header_row.addWidget(self._loaded_name_label)
        header_row.addStretch()
        self._fast_preview_check = QCheckBox("Fast preview (480p)")
        self._fast_preview_check.setToolTip(_FAST_PREVIEW_TOOLTIP)
        if self._settings is not None:
            self._fast_preview_check.setChecked(
                bool(self._settings.get("review_fast_preview_480p", False)))
        self._fast_preview_check.setEnabled(False)   # enabled once a proxy is ready for THIS master
        self._fast_preview_check.toggled.connect(self._on_fast_preview_toggled)
        header_row.addWidget(self._fast_preview_check)
        self._software_decode_check = QCheckBox("Software decode")
        self._software_decode_check.setToolTip(_SOFTWARE_DECODE_TOOLTIP)
        if self._settings is not None:
            self._software_decode_check.setChecked(
                bool(self._settings.get("review_software_decode", False)))
        self._software_decode_check.toggled.connect(self._on_software_decode_toggled)
        header_row.addWidget(self._software_decode_check)
        root.addLayout(header_row)

        # ── Preview + scopes row ──────────────────────────────────────────────
        top_row = QHBoxLayout()
        top_row.setSpacing(12)

        # Fit/1:1 presets live in the Preview section header (right slot); the
        # continuous zoom control is a drag slider next to the preview itself
        # (see below) — a vertical strip you move up/down, not a numeric entry.
        self._zoom_label = QLabel("Zoom")
        self._zoom_fit_btn = QPushButton("Fit")
        self._zoom_fit_btn.setToolTip("Scale the frame to fit the preview")
        self._zoom_1to1_btn = QPushButton("1:1")
        self._zoom_1to1_btn.setToolTip("Show the frame at 100% — one screen pixel per video pixel")
        zoom_widget = QWidget()
        zoom_row = QHBoxLayout(zoom_widget)
        zoom_row.setContentsMargins(0, 0, 0, 0)
        zoom_row.setSpacing(6)
        zoom_row.addWidget(self._zoom_label)
        zoom_row.addWidget(self._zoom_fit_btn)
        zoom_row.addWidget(self._zoom_1to1_btn)

        preview_frame, preview_col = self._section("Preview", right=zoom_widget)
        self._preview_frame = preview_frame
        self._video_view = ZoomableVideoView()

        preview_body_row = QHBoxLayout()
        preview_body_row.setSpacing(8)
        preview_body_row.addWidget(self._video_view, 1)
        self._zoom_slider = QSlider(Qt.Orientation.Vertical)
        self._zoom_slider.setRange(10, 800)   # percent — matches ZoomableVideoView's own clamp
        self._zoom_slider.setValue(100)
        self._zoom_slider.setFixedWidth(22)
        self._zoom_slider.setToolTip("Drag to zoom (up = in, down = out)")
        preview_body_row.addWidget(self._zoom_slider)
        preview_col.addLayout(preview_body_row, 1)

        # ── Video source (baseline vs. an archival clip original) ──────────────
        # Hidden entirely for a master with no archival tracks — nothing to pick
        # between yet, and an always-visible one-item combo would just be noise.
        self._video_source_row = QWidget()
        video_source_lay = QHBoxLayout(self._video_source_row)
        video_source_lay.setContentsMargins(0, 0, 0, 0)
        video_source_lay.setSpacing(8)
        self._video_source_label = QLabel("Video source:")
        self._video_source_combo = QComboBox()
        self._video_source_combo.currentIndexChanged.connect(self._on_video_source_changed)
        self._video_source_readout = QLabel("")
        video_source_lay.addWidget(self._video_source_label)
        video_source_lay.addWidget(self._video_source_combo)
        video_source_lay.addWidget(self._video_source_readout)
        video_source_lay.addStretch()
        self._video_source_row.setVisible(False)
        preview_col.addWidget(self._video_source_row)

        transport_row = QHBoxLayout()
        # Native Qt media icons — guaranteed to render, unlike the exotic glyphs
        # that went invisible earlier this session (see commit 117bf62).
        self._prev_btn = QPushButton()
        self._prev_btn.setIcon(st.standardIcon(QStyle.StandardPixmap.SP_MediaSkipBackward))
        self._prev_btn.setToolTip("Jump to previous clip (PgUp)")
        self._step_back_btn = QPushButton()
        self._step_back_btn.setIcon(st.standardIcon(QStyle.StandardPixmap.SP_MediaSeekBackward))
        self._step_back_btn.setToolTip("Step one frame back (←)")
        self._play_btn = QPushButton()
        self._play_btn.setIcon(st.standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self._play_btn.setToolTip("Play / pause (Space)")
        self._step_fwd_btn = QPushButton()
        self._step_fwd_btn.setIcon(st.standardIcon(QStyle.StandardPixmap.SP_MediaSeekForward))
        self._step_fwd_btn.setToolTip("Step one frame forward (→)")
        self._next_btn = QPushButton()
        self._next_btn.setIcon(st.standardIcon(QStyle.StandardPixmap.SP_MediaSkipForward))
        self._next_btn.setToolTip("Jump to next clip (PgDn)")
        self._jog = JogWheel()
        self._snapshot_btn = QPushButton()
        self._snapshot_btn.setIconSize(QSize(18, 18))   # icon itself is set (theme-tinted) in _restyle
        self._snapshot_btn.setToolTip("Save a full-resolution PNG next to the master (S)")
        self._tc_label = QLabel("00:00:00:00")
        self._dur_label = QLabel("/ 00:00:00:00")
        self._icon_buttons = (self._prev_btn, self._step_back_btn, self._play_btn,
                              self._step_fwd_btn, self._next_btn, self._snapshot_btn)
        for b in self._icon_buttons:
            b.setFixedSize(30, 26)
        # Grouped by function, not evenly spaced: navigation (skip/step/play/
        # step/skip) | jog shuttle | the snapshot action — a divider between
        # each group signals they're different kinds of control, and keeps
        # the camera (an action) visually distinct from transport.
        self._transport_dividers: list = []
        transport_row.addWidget(self._prev_btn)
        transport_row.addWidget(self._step_back_btn)
        transport_row.addWidget(self._play_btn)
        transport_row.addWidget(self._step_fwd_btn)
        transport_row.addWidget(self._next_btn)
        transport_row.addWidget(self._make_vline())
        transport_row.addWidget(self._jog)
        transport_row.addWidget(self._make_vline())
        transport_row.addWidget(self._snapshot_btn)
        transport_row.addStretch()
        transport_row.addWidget(self._tc_label)
        transport_row.addWidget(self._dur_label)
        preview_col.addLayout(transport_row)

        self._status_label = QLabel("")
        preview_col.addWidget(self._status_label)

        top_row.addWidget(preview_frame, 3)

        self._scopes = ScopesPanel()
        scopes_frame, scopes_body = self._section("Colour scopes")
        self._scopes_frame = scopes_frame
        scopes_body.addWidget(self._scopes)
        top_row.addWidget(scopes_frame, 2)

        root.addLayout(top_row, 3)

        # ── Overview section ──────────────────────────────────────────────────
        # Sits ABOVE Audio tracks (not below) — it's the navigator both the
        # video and every audio lane below it are read against, so it reads
        # more naturally as the thing you look at first. OverviewTrackbar's
        # own track is offset by AudioLaneStack.LANE_LABEL_MARGIN (see
        # widgets/trackbar.py) so its video track lines up under the audio
        # lanes' waveforms rather than starting flush at the widget edge.
        overview_right = QWidget()
        overview_right_row = QHBoxLayout(overview_right)
        overview_right_row.setContentsMargins(0, 0, 0, 0)
        overview_right_row.setSpacing(8)
        self._overview_loading = self._loading_indicator("Loading thumbnails…")
        self._overview_hint = QLabel("Drag the box edges to zoom · drag inside to scroll")
        overview_right_row.addWidget(self._overview_loading)
        overview_right_row.addWidget(self._overview_hint)
        self._trackbar = OverviewTrackbar()
        overview_frame, overview_body = self._section("Overview", right=overview_right)
        self._overview_frame = overview_frame
        overview_body.addWidget(self._trackbar)
        root.addWidget(overview_frame)

        # ── Audio section ─────────────────────────────────────────────────────
        self._lanes = AudioLaneStack()
        self._audio_loading = self._loading_indicator("Loading waveforms…")
        audio_frame, audio_body = self._section("Audio tracks", right=self._audio_loading)
        self._audio_frame = audio_frame
        audio_body.addWidget(self._lanes)
        root.addWidget(audio_frame, 2)

        # ── Share a clip (embeds ExtractTab's Share panel widget — see
        # embed_share_panel(); collapsed by default since it's independent of
        # whatever master is loaded above) ─────────────────────────────────────
        self._share_section_collapsed = True
        share_frame = QFrame()
        share_frame.setObjectName("review_section")
        self._sections.append(share_frame)
        self._share_frame = share_frame
        share_v = QVBoxLayout(share_frame)
        share_v.setContentsMargins(12, 9, 12, 11)
        share_v.setSpacing(8)
        share_header = QWidget()
        share_header.setCursor(Qt.CursorShape.PointingHandCursor)
        share_hl = QHBoxLayout(share_header)
        share_hl.setContentsMargins(0, 0, 0, 0)
        self._share_title = QLabel("▸  SHARE A CLIP")
        self._share_title.setObjectName("review_section_title")
        self._section_titles.append(self._share_title)
        share_hl.addWidget(self._share_title)
        share_hl.addStretch()
        share_header.mousePressEvent = lambda e: self._toggle_share_section()
        share_v.addWidget(share_header)
        self._share_body = QWidget()
        self._share_body_layout = QVBoxLayout(self._share_body)
        self._share_body_layout.setContentsMargins(0, 4, 0, 0)
        self._share_body.setVisible(False)
        share_v.addWidget(self._share_body)
        root.addWidget(share_frame)

        self._empty_label = QLabel("Drop a .mov here, or click Load master… to review it.")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._empty_label)
        self._set_loaded_visible(False)

    def embed_share_panel(self, panel: QWidget):
        """Take the Share-a-clip widget (built and owned by ExtractTab —
        `ExtractTab.share_panel()`) and display it inside this section."""
        self._share_body_layout.addWidget(panel)

    def _toggle_share_section(self):
        self._share_section_collapsed = not self._share_section_collapsed
        self._share_body.setVisible(not self._share_section_collapsed)
        chevron = "▸" if self._share_section_collapsed else "▾"
        self._share_title.setText(f"{chevron}  SHARE A CLIP")

    def reveal_share_panel(self):
        """Expand the Share section — used when switching here via the
        Extract tab's "Share a clip" shortcut button."""
        if self._share_section_collapsed:
            self._toggle_share_section()

    def _set_loaded_visible(self, loaded: bool):
        for f in (self._preview_frame, self._scopes_frame, self._audio_frame,
                  self._overview_frame):
            f.setVisible(loaded)
        self._empty_label.setVisible(not loaded)

    # ── Loading ───────────────────────────────────────────────────────────────

    def _browse_for_master(self):
        start_dir = self._settings.get("last_review_source", "") if self._settings else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Load master to review", str(Path(start_dir).parent) if start_dir else "",
            "Video files (*.mov *.mp4);;All files (*)")
        if path:
            self.load_master(path)

    # ── Developer-panel experiments (review_tab playback) ──────────────────────
    def _review_frame_poll_ms(self) -> int:
        try:
            return int(self._settings.get("dev_review_frame_poll_ms", 300)) if self._settings else 300
        except Exception:
            return 300

    def _allow_risky_hw(self) -> bool:
        return bool(self._settings.get("dev_review_allow_risky_hw_decode", False)) if self._settings else False

    def _new_engine(self, use_software: bool):
        """make_engine, with the Developer-panel software-playback refresh rate
        applied — the single seam every engine gets built through."""
        return make_engine(self, use_software=use_software,
                           frame_poll_ms=self._review_frame_poll_ms())

    def reload_dev_settings(self):
        """Re-read the Developer-panel review options and apply what can be applied
        live: the software-playback refresh rate updates the current engine on the
        fly, and the overview filmstrip is regenerated with the current tile
        count/width. (The risky-HEVC override only affects the next master loaded.)"""
        if isinstance(self._engine, HybridPlaybackEngine):
            self._engine.set_frame_poll_ms(self._review_frame_poll_ms())
        if self._path and self._video_info is not None and self._video_info.duration > 0:
            self._start_thumbnail_strip(self._video_info.duration)

    def _on_software_decode_toggled(self, checked: bool):
        self._apply_decode_mode(checked, persist=True,
                                status=("Software decode " + ("on" if checked else "off")
                                       + " — using " + ("CPU" if checked else "GPU") + " video decoding."))

    def _apply_decode_mode(self, checked: bool, persist: bool, status: str = ""):
        """Swap the playback engine live — the whole point of this switch is
        recovering from a GPU decode crash, so making the user restart to
        escape it would be cruel. Tear the old engine down fully before
        dropping the reference (same QThread-lifetime discipline as
        shutdown()), build the requested one, re-wire it, and reload the
        current master at the same position.

        `persist` controls whether this becomes the user's own saved
        preference — False for `_maybe_force_safe_decode`'s per-file safety
        override, which must not overwrite what the user actually asked for.
        """
        if persist and self._settings is not None:
            self._settings.set("review_software_decode", checked)
            self._settings.save()
        pos = self._session.position
        was_playing = self._session.playing
        self._engine.shutdown()
        self._engine = self._new_engine(checked)
        self._wire_engine()
        if status:
            self._status_label.setText(status)
        if self._path and self._video_info is not None:
            self._engine.load(self._current_source_path(), self._session.tracks,
                              fps=self._video_info.fps_float or 29.97)
            self._reapply_audio_after_swap()
            self._engine.seek(pos)
            if was_playing:
                self._engine.play()

    def _current_source_path(self) -> str:
        """What the engine should actually be loading right now — the 480p
        proxy while Fast preview is active and ready, otherwise the real
        master. Scopes/snapshots/peaks/thumbnails always use `self._path`
        directly and are unaffected either way."""
        return self._proxy_path if (self._using_proxy and self._proxy_path) else self._path

    def _maybe_force_safe_decode(self):
        """Called once per freshly-loaded master, right after its video spec
        is known (before `_on_tracks_ready` does its own `engine.load()`,
        so this only needs to pick the right engine INSTANCE — not
        replicate `_apply_decode_mode`'s load/seek/play tail, which assumes
        a live mid-session swap with real position/track state to restore).

        Forces software decode for the confirmed-dangerous 4K+/10-bit/HEVC
        profile (see `is_risky_hw_decode_profile`'s docstring for the real
        14GB-memory-blowup/remote-desktop-disconnect evidence) — overriding
        the user's own hardware-decode preference for THIS file only; it is
        never persisted to settings, and a later, safer master restores
        whatever the user actually asked for.
        """
        # The Developer panel can override the automatic safety force so the user
        # can experiment with GPU decode on the very profile that was risky here.
        raw_risky = is_risky_hw_decode_profile(self._video_info)
        risky = raw_risky and not self._allow_risky_hw()
        currently_software = isinstance(self._engine, HybridPlaybackEngine)

        if risky and not currently_software:
            self._auto_forced_software = True
            self._software_decode_check.blockSignals(True)
            self._software_decode_check.setChecked(True)
            self._software_decode_check.blockSignals(False)
            self._software_decode_check.setEnabled(False)
            self._software_decode_check.setToolTip(_SOFTWARE_DECODE_FORCED_TOOLTIP)
            self._engine.shutdown()
            self._engine = self._new_engine(True)
            self._wire_engine()
            self._status_label.setText(
                "Software decode forced automatically — this master's 4K+ 10-bit HEVC "
                "video previously caused instability under hardware decode.")
        elif self._auto_forced_software and not risky:
            self._auto_forced_software = False
            self._software_decode_check.setEnabled(True)
            self._software_decode_check.setToolTip(_SOFTWARE_DECODE_TOOLTIP)
            saved_pref = bool(self._settings.get("review_software_decode", False)) if self._settings else False
            if isinstance(self._engine, HybridPlaybackEngine) != saved_pref:
                self._software_decode_check.blockSignals(True)
                self._software_decode_check.setChecked(saved_pref)
                self._software_decode_check.blockSignals(False)
                self._engine.shutdown()
                self._engine = self._new_engine(saved_pref)
                self._wire_engine()

        # Override active on a genuinely dangerous file: the checkbox is LEFT
        # live and enabled (that's the whole point of the Developer override),
        # but the tooltip must say plainly, right at the control the user would
        # click, that unchecking it here can hard-crash the whole system — the
        # dev-panel description warns when enabling the override; this warns
        # again at the point of action. (Only reached when the override is on,
        # since otherwise `risky` above already forced software.)
        if raw_risky and self._allow_risky_hw():
            self._software_decode_check.setToolTip(_SOFTWARE_DECODE_RISKY_OVERRIDE_TOOLTIP)

    def _reapply_audio_after_swap(self):
        """After a live engine swap the new engine has no audio set — re-issue
        the current tick-set through the normal debounced path so single-track
        native switches and multi-track renders both come back."""
        self._mix_timer.start()

    # ── Fast-preview (480p proxy) ────────────────────────────────────────────

    def _start_proxy_render(self):
        """Kick off (or cache-hit-skip) a background 480p proxy render for
        the currently loaded master. Runs unconditionally on every load —
        cheap to have ready even if Fast preview isn't checked, since
        checking it later should be instant rather than a fresh wait."""
        ff, fp = get_ffmpeg()
        cache_dir = get_app_dir() / "_temp" / "review_proxy"
        out_path = proxy_cache_path(cache_dir, self._path, height=_PROXY_HEIGHT)
        w = ProxyRenderWorker(ff, self._path, str(out_path), height=_PROXY_HEIGHT)
        w.proxy_ready.connect(self._on_proxy_ready)
        w.error.connect(self._on_proxy_error)
        self._proxy_worker = w
        self._track(w)
        w.start()

    def _on_proxy_ready(self, master_path: str, proxy_path: str):
        if master_path != self._path:
            return   # a later master already loaded — this result is stale
        self._proxy_worker = None
        self._proxy_path = proxy_path
        self._fast_preview_check.setEnabled(True)
        self._fast_preview_check.setToolTip(_FAST_PREVIEW_TOOLTIP)
        if self._fast_preview_check.isChecked():
            self._swap_playback_source(use_proxy=True)

    def _on_proxy_error(self, master_path: str, message: str):
        if master_path != self._path:
            return
        self._proxy_worker = None
        self._fast_preview_check.setToolTip(
            f"Couldn't build a fast-preview proxy for this master: {message}")

    def _on_fast_preview_toggled(self, checked: bool):
        if self._settings is not None:
            self._settings.set("review_fast_preview_480p", checked)
            self._settings.save()
        if checked and self._proxy_path is None:
            # Preference recorded; _on_proxy_ready applies it the moment the
            # in-flight render for this master finishes.
            self._status_label.setText("Fast preview will switch on once its 480p proxy is ready…")
            return
        self._swap_playback_source(use_proxy=checked)

    def _swap_playback_source(self, use_proxy: bool):
        """Live-swap the engine between the real master and its 480p proxy —
        same reload/reseek pattern as `_apply_decode_mode`'s GPU/software
        swap, just picking a different source path instead of a different
        engine instance."""
        if use_proxy == self._using_proxy or self._video_info is None:
            return
        self._using_proxy = use_proxy
        pos = self._session.position
        was_playing = self._session.playing
        self._engine.load(self._current_source_path(), self._session.tracks,
                          fps=self._video_info.fps_float or 29.97)
        self._reapply_audio_after_swap()
        self._engine.seek(pos)
        if was_playing:
            self._engine.play()

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

        if self._thumb_worker is not None:
            self._thumb_worker.cancel()
            self._thumb_worker = None
        self._trackbar.set_thumbnail_count(0)   # clear the previous master's filmstrip
        self._overview_loading.stop()
        self._audio_loading.stop()
        self._manifest = None
        self._clip_window_end = None
        self._video_source_row.setVisible(False)   # repopulated once the new manifest is known
        self._video_source_readout.setText("")

        # A new master invalidates any proxy in flight/ready for the OLD one —
        # the initial load always uses the real master (correct immediately,
        # no waiting on a render); Fast preview upgrades to the new proxy
        # once _on_proxy_ready confirms it's actually for THIS master.
        if self._proxy_worker is not None:
            self._proxy_worker.cancel()
            self._proxy_worker = None
        self._proxy_path = None
        self._using_proxy = False
        self._fast_preview_check.setEnabled(False)
        self._fast_preview_check.setToolTip("Preparing a 480p proxy for this master…")
        self._start_proxy_render()

        ff, fp = get_ffmpeg()
        w = TrackScanWorker(fp, self._path)
        w.tracks_ready.connect(self._on_tracks_ready)
        self._track(w)
        w.start()

    def _on_tracks_ready(self, video_info: StreamInfo, audio_tracks: list, chapters: list,
                         manifest):
        self._video_info = video_info
        self._chapters = chapters
        self._manifest = manifest
        self._track_labels = _label_tracks(audio_tracks)
        self._maybe_force_safe_decode()
        self._populate_video_sources(manifest)

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
        peak_w.finished.connect(self._audio_loading.stop)
        self._track(peak_w)
        if audio_tracks:
            self._audio_loading.start()
        peak_w.start()

        self._start_thumbnail_strip(video_info.duration)

    # ── Video source (baseline vs. an archival clip original) ──────────────────

    def _populate_video_sources(self, manifest):
        """Build the Video source combo from the master's manifest — one entry
        per ORIGINAL CLIP that has its own archival track (see
        core.manifest.ClipEntry.archival_track), not one per track: several
        clips sharing a track (grouped archival mode) each still get their own
        entry, since they're separately seekable via their own in_track_start.
        Row stays hidden for a master with nothing to offer (no manifest, or
        Archival master was off when it was built)."""
        self._video_source_combo.blockSignals(True)
        self._video_source_combo.clear()
        self._video_source_combo.addItem("Master (playable)", None)
        if manifest is not None:
            for c in manifest.clips:
                if c.archival_track is None:
                    continue
                spec = f"{(c.codec or '?').upper()} {c.width}x{c.height} {c.fps}fps"
                self._video_source_combo.addItem(f"{c.source_filename} — original", (
                    c.archival_track, c.in_track_start, c.in_track_duration, c.source_filename, spec))
        self._video_source_combo.setCurrentIndex(0)
        self._video_source_combo.blockSignals(False)
        self._video_source_row.setVisible(self._video_source_combo.count() > 1)
        self._on_video_source_changed(0)   # explicit reset — a fresh load's engine already
                                           # defaults to the baseline, but this also clears
                                           # any stale readout/clip-window from a prior master

    def _on_video_source_changed(self, index: int):
        if self._video_info is None:
            return
        data = self._video_source_combo.itemData(index)
        if data is None:
            self._clip_window_end = None
            self._video_source_readout.setText("")
            self._engine.set_video_track(0)
            self._engine.seek(0.0)
        else:
            track_idx, start, duration, name, spec = data
            self._clip_window_end = (start + duration) if duration > 0 else None
            self._video_source_readout.setText(f"Viewing original: {name}  ({spec})")
            self._engine.set_video_track(track_idx)
            self._engine.seek(start)

    def _start_thumbnail_strip(self, duration: float):
        """Sparse filmstrip thumbnails for the overview timeline — cheap
        individual-frame extractions directly from the master (no proxy
        track: cancelled in favour of this simpler on-demand approach).

        Tile count and width are Developer-panel experiments (default 24 / 160px)."""
        if duration <= 0:
            return
        if self._thumb_worker is not None:
            self._thumb_worker.cancel()   # a live re-run (dev panel) supersedes the old strip
            self._thumb_worker = None
        count = int(self._settings.get("dev_review_thumb_count", _THUMBNAIL_COUNT)) if self._settings else _THUMBNAIL_COUNT
        width = int(self._settings.get("dev_review_thumb_width", 160)) if self._settings else 160
        count = max(1, count)
        self._trackbar.set_thumbnail_count(count)
        timestamps = [duration * (i + 0.5) / count for i in range(count)]
        ff, fp = get_ffmpeg()
        out_dir = get_app_dir() / "_temp" / "review_thumbs"
        w = ThumbnailStripWorker(ff, self._path, timestamps, out_dir, width=width)
        w.thumbnail_ready.connect(self._trackbar.set_thumbnail)
        w.finished.connect(lambda w=w: self._on_thumb_worker_finished(w))
        self._thumb_worker = w
        self._track(w)
        self._overview_loading.start()
        w.start()

    def _on_thumb_worker_finished(self, w):
        if self._thumb_worker is w:
            self._thumb_worker = None
        self._overview_loading.stop()

    def _on_pyramid_ready(self, track_idx: int, pyramid):
        # Keep the pyramid so the lane can be re-cropped whenever the viewport
        # changes (zoom in on the overview → lanes show only that window).
        self._pyramids[track_idx] = pyramid
        self._refresh_lane_peaks(track_idx)
        # The overview envelope stays FULL-duration — it's the navigator.
        if pyramid.levels:
            coarsest = pyramid.levels[-1]
            envelope = np.maximum(np.abs(coarsest[:, 0]), np.abs(coarsest[:, 1]))
            self._trackbar.set_envelope(envelope)

    def _refresh_lane_peaks(self, only_track: Optional[int] = None):
        """Re-render each audio lane's waveform for the current viewport window,
        so zooming the overview crops the lanes to the selection. Cheap
        (peaks_for_view just min/max-reduces the pyramid), so no debounce."""
        t0, t1 = self._session.view_t0, self._session.view_t1
        if t1 <= t0:
            t0, t1 = 0.0, self._session.duration
        items = ([(only_track, self._pyramids[only_track])]
                 if only_track is not None and only_track in self._pyramids
                 else list(self._pyramids.items()))
        for idx, pyr in items:
            self._lanes.set_peaks(idx, pyr.peaks_for_view(t0, t1, 600))

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
        icon = QStyle.StandardPixmap.SP_MediaPause if playing else QStyle.StandardPixmap.SP_MediaPlay
        self._play_btn.setIcon(self.style().standardIcon(icon))
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
        if self._video_view.zoom_mode() != "fit":
            # Paused and zoomed past "fit" — swap in this same exact, full
            # native-resolution frame for detailed visual inspection. The
            # live/proxy frame the preview otherwise shows (especially under
            # the software-decode fallback's periodic low-res extraction) can
            # look soft once zoomed in; this reuses the frame already fetched
            # for the scopes panel rather than issuing a second ffmpeg call.
            img8 = (arr16 >> 8).astype(np.uint8)
            h, w = img8.shape[0], img8.shape[1]
            qimg = QImage(np.ascontiguousarray(img8).data, w, h, w * 3, QImage.Format.Format_RGB888)
            self._video_view.set_frame(qimg.copy())

    # ── Session wiring ────────────────────────────────────────────────────────

    def _wire_session(self):
        self._session.duration_changed.connect(self._on_session_duration)
        self._session.position_changed.connect(self._on_session_position)
        self._session.viewport_changed.connect(self._on_viewport_changed)
        self._session.tracks_changed.connect(lambda tracks: None)

    def _on_session_duration(self, secs: float, fps: float):
        self._trackbar.set_duration(secs)
        self._dur_label.setText(f"/ {secs_to_tc(secs, fps)}")

    def _on_session_position(self, secs: float):
        self._trackbar.set_position(secs)
        self._tc_label.setText(secs_to_tc(secs, self._session.fps))
        t0, t1 = self._session.view_t0, self._session.view_t1
        span = max(1e-6, t1 - t0)
        frac = (secs - t0) / span if t0 <= secs <= t1 else None
        self._lanes.set_playhead(frac)
        # Viewing one archival clip's original (see _on_video_source_changed):
        # its own track keeps going past this clip's window into whatever the
        # concat placed next — stop instead of spilling into a neighbour.
        if self._clip_window_end is not None and secs >= self._clip_window_end and self._session.playing:
            self._engine.pause()

    def _on_viewport_changed(self, t0: float, t1: float):
        self._trackbar.set_viewport(t0, t1)
        if self._lanes.current_mode() == "spec":
            self._spec_timer.start()
        else:
            self._refresh_lane_peaks()   # crop the waveform lanes to the new window

    # ── Widget wiring ─────────────────────────────────────────────────────────

    def _wire_widgets(self):
        self._zoom_fit_btn.clicked.connect(self._video_view.set_zoom_fit)
        self._zoom_1to1_btn.clicked.connect(self._on_zoom_1to1_clicked)
        self._zoom_slider.valueChanged.connect(
            lambda v: self._video_view.set_zoom_percent(float(v)))
        self._video_view.zoom_changed.connect(self._on_zoom_changed)

        # Wrapped in a lambda (not bound to self._engine directly) so a live
        # software-decode swap that replaces self._engine keeps working — see
        # _on_software_decode_toggled.
        self._play_btn.clicked.connect(lambda: self._engine.toggle())
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

    def _setup_shortcuts(self):
        """Thin keyboard wrappers over controls that already exist — a review
        tool lives or dies on Space/arrow scrubbing. All bound to this widget,
        so they only fire when the Review tab has focus."""
        def sc(seq, slot):
            s = QShortcut(QKeySequence(seq), self)
            s.activated.connect(slot)
            return s
        sc(Qt.Key.Key_Space, lambda: self._engine.toggle())
        sc(Qt.Key.Key_Left, lambda: self._step(-1))
        sc(Qt.Key.Key_Right, lambda: self._step(1))
        sc(Qt.Key.Key_PageUp, self._prev_chapter)
        sc(Qt.Key.Key_PageDown, self._next_chapter)
        sc(Qt.Key.Key_Home, lambda: self._seek(0.0))
        sc(Qt.Key.Key_End, lambda: self._seek(self._session.duration))
        sc(Qt.Key.Key_S, self._take_snapshot)

    def _on_zoom_1to1_clicked(self):
        self._video_view.set_zoom_1to1()
        self._zoom_slider.blockSignals(True)
        self._zoom_slider.setValue(100)
        self._zoom_slider.blockSignals(False)

    def _on_zoom_changed(self, frac: float):
        pct = round(frac * 100)
        if self._zoom_slider.value() != pct:
            self._zoom_slider.blockSignals(True)
            self._zoom_slider.setValue(pct)
            self._zoom_slider.blockSignals(False)
        if not self._session.playing:
            self._zoom_frame_timer.start()   # debounced full-res video-preview refresh

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
        w.snapshot_saved.connect(lambda p: self._flash_status_ok(f"Snapshot saved — {p}"))
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

    def _flash_status_ok(self, message: str):
        """A successful action (e.g. a snapshot save) deserves a beat more
        prominence than the shared muted status line normally gives — briefly
        tint it `ok` green, then fade back to the normal muted style."""
        self._status_label.setText(message)
        p = theme.active_palette()
        self._status_label.setStyleSheet(f"color:{p.ok}; font-size:11px; font-weight:bold;")
        QTimer.singleShot(1800, lambda m=message: self._unflash_status(m))

    def _unflash_status(self, expected_text: str):
        if self._status_label.text() != expected_text:
            return   # a newer status message has since replaced this one
        p = theme.active_palette()
        self._status_label.setStyleSheet(f"color:{p.text_mute}; font-size:11px;")

    # ── Theming ───────────────────────────────────────────────────────────────

    def _restyle(self):
        p = theme.active_palette()
        for f in self._sections:
            f.setStyleSheet(
                f"QFrame#review_section {{ background:{p.surface}; border:1px solid {p.border_dk}; "
                "border-radius:8px; }")
        for t in self._section_titles:
            # Muted, not accent — accent is reserved for interactive/active
            # states (toggles, the primary action, the playhead); a plain
            # section label isn't clickable and shouldn't wear that colour.
            t.setStyleSheet(f"color:{p.text_mute}; font-size:10px; font-weight:bold; letter-spacing:1px;")
        self._empty_label.setStyleSheet(f"color:{p.text_mute}; font-size:14px;")
        self._status_label.setStyleSheet(f"color:{p.text_mute}; font-size:11px;")
        self._loaded_name_label.setStyleSheet(f"color:{p.text_mute}; font-size:12px;")
        self._zoom_label.setStyleSheet(f"color:{p.text_mute}; font-size:11px;")
        self._overview_hint.setStyleSheet(f"color:{p.text_mute}; font-size:11px;")
        self._overview_loading.restyle(p)
        self._audio_loading.restyle(p)
        self._video_source_label.setStyleSheet(f"color:{p.text_mute}; font-size:11px;")
        self._video_source_readout.setStyleSheet(f"color:{p.accent}; font-size:11px; font-weight:bold;")
        self._tc_label.setStyleSheet(
            f"color:{p.text_dim}; font-size:12px; font-family:monospace;")
        self._dur_label.setStyleSheet(
            f"color:{p.text_mute}; font-size:12px; font-family:monospace;")
        self._snapshot_btn.setIcon(_camera_icon(p.accent))
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
        for line in self._transport_dividers:
            line.setStyleSheet(f"color:{p.border};")   # QFrame line colour follows the palette
