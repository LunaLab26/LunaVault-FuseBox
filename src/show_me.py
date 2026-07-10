"""show_me.py — the "Show me" animation: what a merge will do, played as a film.

A friendly, animated explainer for the Merge tab (a 12-year-old should get it):
the user's actual clips appear as little film-strip cards, each is judged
against the chosen baseline ("matches → copied exactly" / "different → converted
to fit"), then flies onto the movie reel inside a keepsake-box MOV container —
with camera-sound and wireless-tape shelves, and a vault below for the exact
originals when Archival master is on. Everything adapts to the user's real
selection and parameters.

Split for testability:
  - `build_story(...)` — PURE: turns clips + parameters into a Story (what goes
    where and why), no Qt.
  - `ShowMeCanvas` — QPainter animation of a Story on a phase timeline; time is
    injectable (`set_time`) so tests can scrub without a timer.
  - `ShowMeDialog` — hosts the canvas, a narration line, Replay/Close.

All colours come from theme.active_palette() (theme discipline: no literals).
"""

import time
from dataclasses import dataclass, field
from typing import Optional

from PySide6.QtCore import Qt, QTimer, QRectF, QPointF, Signal
from PySide6.QtGui import QPainter, QColor, QPen, QFont
from PySide6.QtWidgets import QDialog, QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout

import theme

MAX_CARDS = 8   # beyond this the rest fold into one "+N more" card


# ── The story (pure) ───────────────────────────────────────────────────────────

@dataclass
class StoryClip:
    name: str
    camera: str = ""
    has_wav: bool = False
    convert: bool = False        # False = stream-copied exactly; True = conformed
    reason: str = ""             # the (first) spec difference driving a convert
    vault: Optional[str] = None  # None | "own" (its own vault box) | a shared group label
    count: int = 1               # >1 only for the folded "+N more" card


@dataclass
class Story:
    clips: list = field(default_factory=list)     # list[StoryClip]
    audio_tracks: list = field(default_factory=list)   # subset of ["camera","wav","mix"], plan order
    archival: str = "off"        # "off" | "per-clip" | "grouped"
    compat_baseline: bool = False
    optimize: bool = False
    output_name: str = "master.mov"

    @property
    def any_wav(self) -> bool:
        return any(c.has_wav for c in self.clips)

    @property
    def any_convert(self) -> bool:
        return any(c.convert for c in self.clips)


def _short(stem: str, limit: int = 16) -> str:
    return stem if len(stem) <= limit else stem[:limit - 1] + "…"


def _spec_label(clip) -> str:
    """A tiny human label for an odd-spec clip's group (its first conflict)."""
    st = getattr(clip, "stream", None)
    conflicts = list(getattr(st, "conflicts", []) or []) if st else []
    return conflicts[0] if conflicts else "different spec"


def build_story(clips: list, *, archival: bool, per_clip_archival: bool,
                optimize_baseline: bool, compat_baseline: bool,
                audio_tracks: list, output_name: str = "master.mov") -> Story:
    """Turn the user's REAL selection + parameters into the animation's script.
    Mirrors the merge's actual decision logic (probe status / optimize /
    archival mode) without touching ffmpeg."""
    story = Story(audio_tracks=[k for k in audio_tracks if k in ("camera", "wav", "mix")],
                  compat_baseline=bool(compat_baseline), optimize=bool(optimize_baseline),
                  output_name=output_name or "master.mov")
    if archival and per_clip_archival:
        story.archival = "per-clip"
    elif archival:
        story.archival = "grouped"

    for clip in clips[:MAX_CARDS]:
        conform_ok = getattr(clip, "status", "unknown") == "ok"
        convert = (not conform_ok) or bool(optimize_baseline)
        if optimize_baseline and conform_ok:
            reason = "optimized for delivery"
        else:
            reason = "" if conform_ok else _spec_label(clip)
        if story.archival == "per-clip":
            vault = "own"
        elif story.archival == "grouped":
            # conforming clips need no vault slot — the reel itself is their
            # lossless copy; odd-spec ones share a group track per spec
            vault = None if (conform_ok and not optimize_baseline) else _spec_label(clip)
        else:
            vault = None
        story.clips.append(StoryClip(
            name=_short(getattr(clip, "stem", "clip")),
            camera=getattr(clip, "camera_label", "") or "",
            has_wav=bool(clip.has_wav() if hasattr(clip, "has_wav") else False),
            convert=convert, reason=reason, vault=vault,
        ))
    extra = len(clips) - MAX_CARDS
    if extra > 0:
        any_wav = any(getattr(c, "has_wav", lambda: False)() for c in clips[MAX_CARDS:])
        story.clips.append(StoryClip(name=f"+{extra} more", count=extra, has_wav=any_wav,
                                     vault=("own" if story.archival == "per-clip" else None)))
    return story


# ── Phase timeline ─────────────────────────────────────────────────────────────

INTRO_T = 1.6          # clips introduce themselves
FLY_T = 1.5            # one card's flight
STAGGER_T = 1.0        # gap between take-offs
SWEEP_T = 2.0          # compat "one smooth take" sweep
OUTRO_T = 2.5


@dataclass
class Phase:
    t0: float
    t1: float
    caption: str


def build_phases(story: Story) -> list:
    """The narration/timing script. Card i flies during phases[1+i]."""
    phases = [Phase(0.0, INTRO_T,
                    "These are the clips you picked — each little film strip is one video"
                    + (", and the teal reels are wireless-mic recordings" if story.any_wav else ""))]
    t = INTRO_T
    for c in story.clips:
        if c.convert:
            cap = (f"“{c.name}” doesn't match the movie's format ({c.reason}) — "
                   "it's converted on the way in so the whole film plays as one")
        else:
            cap = (f"“{c.name}” already matches — it's copied onto the reel exactly, "
                   "not a single pixel changed")
        if c.vault == "own":
            cap += " · its untouched original also goes in the vault"
        elif c.vault:
            cap += " · its exact original is stored in the vault too"
        phases.append(Phase(t, t + FLY_T, cap))
        t += STAGGER_T
    t = phases[-1].t1   # wait for the last flight to land
    if story.compat_baseline:
        phases.append(Phase(t, t + SWEEP_T,
                            "Then the whole reel is re-filmed as ONE smooth take, so it plays "
                            "perfectly on any phone, TV or website"))
        t += SWEEP_T
    outro = "Done — one file that plays anywhere"
    if story.archival != "off":
        outro += ", with every original kept safe in the vault, recoverable exactly as filmed"
    if "wav" in story.audio_tracks and story.any_wav:
        outro += ". The wireless sound rides along losslessly on its own tape"
    phases.append(Phase(t, t + OUTRO_T, outro))
    return phases


def _ease(u: float) -> float:
    u = max(0.0, min(1.0, u))
    return u * u * (3 - 2 * u)


# ── Canvas ─────────────────────────────────────────────────────────────────────

class ShowMeCanvas(QWidget):
    """Draws the story at an injectable time `self._t` (seconds). The dialog
    owns the wall-clock timer; tests call set_time() directly."""
    caption_changed = Signal(str)

    def __init__(self, story: Story, parent=None):
        super().__init__(parent)
        self._story = story
        self._phases = build_phases(story)
        self._t = 0.0
        self._last_caption = ""
        self.setMinimumSize(860, 480)

    @property
    def total_duration(self) -> float:
        return self._phases[-1].t1

    def set_time(self, t: float):
        self._t = max(0.0, min(t, self.total_duration))
        cap = self.current_caption()
        if cap != self._last_caption:
            self._last_caption = cap
            self.caption_changed.emit(cap)
        self.update()

    def current_caption(self) -> str:
        for ph in reversed(self._phases):
            if self._t >= ph.t0:
                return ph.caption
        return self._phases[0].caption

    def flight_progress(self, i: int) -> float:
        """0 = card i still on the shelf, 1 = landed on the reel."""
        ph = self._phases[1 + i]
        if self._t <= ph.t0:
            return 0.0
        return _ease((self._t - ph.t0) / (ph.t1 - ph.t0))

    # ── Geometry (recomputed from the live size every frame) ──────────────────
    def _geometry(self):
        w, h = self.width(), self.height()
        n = len(self._story.clips)
        left_x, left_w = 24, 218
        card_h = min(46, max(30, (h - 140) // max(n, 1) - 8))
        cards = [QRectF(left_x, 64 + i * (card_h + 8), left_w, card_h) for i in range(n)]
        box = QRectF(320, 44, w - 320 - 24, h - 44 - 64)
        reel = QRectF(box.x() + 16, box.y() + 44, box.width() - 32, 52)
        shelf_y = reel.bottom() + 14
        shelves = {}
        for kind in self._story.audio_tracks:
            if kind == "wav" and not self._story.any_wav:
                continue
            shelves[kind] = QRectF(reel.x(), shelf_y, reel.width(), 18)
            shelf_y += 26
        vault = None
        if self._story.archival != "off":
            vault = QRectF(reel.x(), shelf_y + 10, reel.width(),
                           max(52, box.bottom() - shelf_y - 22))
        return cards, box, reel, shelves, vault

    # ── Painting ───────────────────────────────────────────────────────────────
    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = theme.active_palette()
        n = len(self._story.clips)
        cards, box, reel, shelves, vault = self._geometry()
        intro = _ease(self._t / INTRO_T)
        small = QFont(self.font());  small.setPointSizeF(8.5)
        tiny = QFont(self.font());   tiny.setPointSizeF(7.5)
        title_f = QFont(self.font()); title_f.setPointSizeF(9.5); title_f.setBold(True)

        # ── The keepsake box (MOV container) ──────────────────────────────────
        p.setPen(QPen(QColor(pal.accent), 2))
        p.setBrush(QColor(pal.surface))
        p.drawRoundedRect(box, 14, 14)
        p.setFont(title_f)
        p.setPen(QColor(pal.text))
        p.drawText(QRectF(box.x(), box.y() + 6, box.width(), 20), Qt.AlignHCenter,
                   f"📦  {self._story.output_name}")
        p.setFont(tiny)
        p.setPen(QColor(pal.text_mute))
        p.drawText(QRectF(box.x(), box.y() + 24, box.width(), 14), Qt.AlignHCenter,
                   "one keepsake box (MOV) — everything travels together")

        # movie reel shelf
        p.setPen(QPen(QColor(pal.border_hi), 1))
        p.setBrush(QColor(pal.input_dk))
        p.drawRoundedRect(reel, 6, 6)
        p.setFont(tiny); p.setPen(QColor(pal.text_mute))
        p.drawText(QRectF(reel.x() + 6, reel.y() - 14, reel.width(), 12), Qt.AlignLeft,
                   "the movie reel — what everyone watches")

        # audio shelves
        shelf_names = {"camera": "camera sound (from each clip)",
                       "wav": "wireless-mic tape — lossless",
                       "mix": "blended mix (camera + wireless together)"}
        shelf_cols = {"camera": pal.gold, "wav": pal.blue, "mix": pal.accent_hi}
        for kind, r in shelves.items():
            col = QColor(shelf_cols[kind]); col.setAlpha(46)
            p.setPen(QPen(QColor(shelf_cols[kind]), 1))
            p.setBrush(col)
            p.drawRoundedRect(r, 4, 4)
            p.setPen(QColor(pal.text_mute))
            p.drawText(QRectF(r.x() + 6, r.y() + 2, r.width() - 10, r.height() - 3),
                       Qt.AlignLeft | Qt.AlignVCenter, shelf_names[kind])

        # the vault
        if vault is not None:
            p.setPen(QPen(QColor(pal.ok), 1.6, Qt.DashLine))
            v_bg = QColor(pal.ok); v_bg.setAlpha(18)
            p.setBrush(v_bg)
            p.drawRoundedRect(vault, 8, 8)
            p.setFont(tiny); p.setPen(QColor(pal.ok))
            label = ("🔒 the vault — every original kept exactly, byte for byte"
                     if self._story.archival == "per-clip" else
                     "🔒 the vault — odd-format originals kept exactly (grouped by format)")
            p.drawText(QRectF(vault.x() + 8, vault.y() + 3, vault.width() - 16, 13),
                       Qt.AlignLeft, label)

        # ── Landed reel segments + shelf segments + vault boxes ───────────────
        seg_w = (reel.width() - 12) / max(n, 1)
        vault_slots = self._vault_slots()
        vslot_w = (vault.width() - 16) / max(len(vault_slots), 1) if vault is not None and vault_slots else 0
        for i, c in enumerate(self._story.clips):
            f = self.flight_progress(i)
            if f >= 1.0:
                self._draw_reel_segment(p, pal, reel, seg_w, i, c, small)
                self._draw_shelf_segments(p, pal, shelves, seg_w, reel, i, c)
                if vault is not None and c.vault is not None:
                    self._draw_vault_mark(p, pal, vault, vault_slots, vslot_w, c, tiny)

        # compat sweep — the "one smooth take" polish
        if self._story.compat_baseline:
            ph = self._phases[1 + n]
            if ph.t0 < self._t:
                u = _ease(min(1.0, (self._t - ph.t0) / (ph.t1 - ph.t0)))
                sweep = QColor(pal.accent_hi); sweep.setAlpha(90)
                p.setPen(Qt.NoPen); p.setBrush(sweep)
                p.drawRoundedRect(QRectF(reel.x(), reel.y(), reel.width() * u, reel.height()), 6, 6)
                if u >= 1.0:
                    p.setFont(tiny); p.setPen(QColor(pal.accent))
                    p.drawText(QRectF(reel.x(), reel.y() + reel.height() - 14, reel.width() - 8, 12),
                               Qt.AlignRight, "✨ one smooth take")

        # ── Waiting cards on the left + in-flight cards ────────────────────────
        p.setFont(small); p.setPen(QColor(pal.text_mute))
        p.setOpacity(intro)
        p.drawText(QRectF(24, 40, 260, 16), Qt.AlignLeft, "your clips")
        for i, c in enumerate(self._story.clips):
            f = self.flight_progress(i)
            if f >= 1.0:
                continue
            src = cards[i]
            dst = QRectF(reel.x() + 6 + i * seg_w, reel.y() + 6, max(seg_w - 4, 22), reel.height() - 12)
            r = self._lerp_rect(src, dst, f)
            lift = -46 * (4 * f * (1 - f))          # gentle arc upward mid-flight
            r.translate(0, lift)
            self._draw_card(p, pal, r, c, f, small, tiny, intro if f == 0 else 1.0)
        p.setOpacity(1.0)

        # converter gate — only while a converting clip is mid-flight
        gate_on = any(0.15 < self.flight_progress(i) < 0.85 and c.convert
                      for i, c in enumerate(self._story.clips))
        if gate_on:
            gx = (cards[0].right() + reel.x()) / 2
            p.setPen(QPen(QColor(pal.warn), 2))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(gx, reel.center().y() - 20), 24, 30)
            p.setFont(tiny); p.setPen(QColor(pal.warn))
            p.drawText(QRectF(gx - 42, reel.center().y() + 16, 84, 12), Qt.AlignHCenter, "converter")

    # ── Small draw helpers ─────────────────────────────────────────────────────
    def _vault_slots(self) -> list:
        """Ordered unique vault slot labels ("own" slots stay per-clip)."""
        slots = []
        for c in self._story.clips:
            if c.vault is None:
                continue
            key = c.name if c.vault == "own" else c.vault
            if key not in slots:
                slots.append(key)
        return slots

    def _lerp_rect(self, a: QRectF, b: QRectF, u: float) -> QRectF:
        return QRectF(a.x() + (b.x() - a.x()) * u, a.y() + (b.y() - a.y()) * u,
                      a.width() + (b.width() - a.width()) * u,
                      a.height() + (b.height() - a.height()) * u)

    def _draw_card(self, p, pal, r, c: StoryClip, f: float, small, tiny, opacity: float):
        p.setOpacity(opacity)
        # mid-flight, a converting card cross-fades to the "converted" tint
        conv = c.convert and f > 0.45
        border = pal.warn if conv else (pal.ok if (c.convert is False and f > 0.05) else pal.border_hi)
        p.setPen(QPen(QColor(border), 1.6))
        p.setBrush(QColor(pal.surface2))
        p.drawRoundedRect(r, 5, 5)
        # film sprockets
        p.setBrush(QColor(pal.input_dk)); p.setPen(Qt.NoPen)
        step = 14
        x = r.x() + 6
        while x < r.right() - 8 and r.width() > 40:
            p.drawRect(QRectF(x, r.y() + 3, 6, 4))
            p.drawRect(QRectF(x, r.bottom() - 7, 6, 4))
            x += step
        if r.width() > 60:
            p.setFont(small); p.setPen(QColor(pal.text))
            p.drawText(QRectF(r.x() + 8, r.y() + 8, r.width() - 14, 14), Qt.AlignLeft, c.name)
            p.setFont(tiny); p.setPen(QColor(pal.text_mute))
            sub = c.camera + ("  ·  needs converting: " + c.reason if (c.convert and c.reason and f < 0.45)
                              else ("  ·  matches ✓" if not c.convert and f < 0.05 else ""))
            p.drawText(QRectF(r.x() + 8, r.y() + r.height() - 16, r.width() - 14, 12),
                       Qt.AlignLeft, sub.strip(" ·"))
        if c.has_wav and f < 0.98 and r.width() > 60:
            p.setPen(QPen(QColor(pal.blue), 1.4))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(r.right() - 12, r.center().y()), 7, 7)
            p.drawEllipse(QPointF(r.right() - 12, r.center().y()), 2.5, 2.5)
        p.setOpacity(1.0)

    def _draw_reel_segment(self, p, pal, reel, seg_w, i, c: StoryClip, small):
        r = QRectF(reel.x() + 6 + i * seg_w, reel.y() + 6, max(seg_w - 4, 22), reel.height() - 12)
        col = QColor(pal.warn if c.convert else pal.ok); col.setAlpha(60)
        p.setPen(QPen(QColor(pal.warn if c.convert else pal.ok), 1.2))
        p.setBrush(col)
        p.drawRoundedRect(r, 3, 3)
        if r.width() > 46:
            p.setFont(small); p.setPen(QColor(pal.text))
            p.drawText(r, Qt.AlignCenter, c.name if c.count == 1 else c.name)

    def _draw_shelf_segments(self, p, pal, shelves, seg_w, reel, i, c: StoryClip):
        cam = shelves.get("camera")
        if cam is not None:
            col = QColor(pal.gold); col.setAlpha(120)
            p.setPen(Qt.NoPen); p.setBrush(col)
            p.drawRect(QRectF(reel.x() + 6 + i * seg_w, cam.y() + 3, max(seg_w - 4, 22), cam.height() - 6))
        wav = shelves.get("wav")
        if wav is not None:
            x = reel.x() + 6 + i * seg_w
            r = QRectF(x, wav.y() + 3, max(seg_w - 4, 22), wav.height() - 6)
            if c.has_wav:
                col = QColor(pal.blue); col.setAlpha(120)
                p.setPen(Qt.NoPen); p.setBrush(col)
                p.drawRect(r)
            else:
                p.setPen(QPen(QColor(pal.border_hi), 1, Qt.DotLine))
                p.setBrush(Qt.NoBrush)
                p.drawRect(r)   # silence filler keeps the tapes in step

    def _draw_vault_mark(self, p, pal, vault, slots, vslot_w, c: StoryClip, tiny):
        key = c.name if c.vault == "own" else c.vault
        try:
            k = slots.index(key)
        except ValueError:
            return
        r = QRectF(vault.x() + 8 + k * vslot_w, vault.y() + 20, max(vslot_w - 6, 26),
                   min(30.0, vault.height() - 26))
        col = QColor(pal.ok); col.setAlpha(46)
        p.setPen(QPen(QColor(pal.ok), 1.2))
        p.setBrush(col)
        p.drawRoundedRect(r, 4, 4)
        if r.width() > 40:
            p.setFont(tiny); p.setPen(QColor(pal.text))
            p.drawText(r, Qt.AlignCenter, ("✓ " + key) if vslot_w > 70 else "✓")


# ── Dialog ─────────────────────────────────────────────────────────────────────

class ShowMeDialog(QDialog):
    """Plays the story once, with Replay. Modeless-feel modal (exec)."""

    def __init__(self, story: Story, parent=None):
        super().__init__(parent)
        pal = theme.active_palette()
        self.setWindowTitle("Show me — what this merge will do")
        self.resize(980, 620)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        self.canvas = ShowMeCanvas(story)
        lay.addWidget(self.canvas, 1)

        self._caption = QLabel("")
        self._caption.setWordWrap(True)
        self._caption.setMinimumHeight(44)
        self._caption.setStyleSheet(
            f"background:{pal.surface}; color:{pal.text}; border:1px solid {pal.border}; "
            f"border-radius:{pal.radius}px; padding:8px 12px; font-size:13px;")
        self.canvas.caption_changed.connect(self._caption.setText)
        lay.addWidget(self._caption)

        btns = QHBoxLayout()
        self._replay = QPushButton("↻  Replay")
        self._replay.clicked.connect(self.replay)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        btns.addWidget(self._replay)
        btns.addStretch(1)
        btns.addWidget(close)
        lay.addLayout(btns)

        self._t0 = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self.canvas.set_time(0.0)

    def replay(self):
        self._t0 = time.monotonic()
        if not self._timer.isActive():
            self._timer.start()
        self.canvas.set_time(0.0)

    def _tick(self):
        t = time.monotonic() - self._t0
        self.canvas.set_time(t)
        if t >= self.canvas.total_duration:
            self._timer.stop()

    def closeEvent(self, ev):
        self._timer.stop()
        super().closeEvent(ev)
