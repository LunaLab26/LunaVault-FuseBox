"""about_tab.py — About tab for LunaVault FuseBox v1.4.

Carries the approved Story / How-it-works copy, an inline-expanding "Support the
project" section (donations folded in from the old tab), credits, and an
Open-source licenses button. Theme-aware via the palette controller.
"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QApplication,
    QPushButton, QScrollArea, QFrame,
)
from PySide6.QtSvgWidgets import QSvgWidget

from logo_widget import make_logo_widget
from open_external import open_url, open_path
from core.binaries import get_app_dir
from dev_history import HISTORY, LAST_UPDATED
import theme

_ASSETS = Path(__file__).parent / "assets"

FEEDBACK_URL = "https://forms.gle/AGpUvFczRqKUKycz8"
COFFEE_URL   = "https://www.buymeacoffee.com/LunaVault"
_COFFEE_QR   = _ASSETS / "qr_coffee.png"

STORY_TEXT = (
    "Hello — I'm John.\n\n"
    "I want my toddler to grow up remembering our family time in high-quality video. "
    "My goal was a streamlined way to bundle several video and audio clips from each day "
    "or occasion into a single master file — good enough to upload to YouTube and archive "
    "to the cloud or a local drive for safekeeping. I absolutely love my Luna Ultra camera "
    "for capturing family memories, and LunaVault FuseBox was born from that passion.\n\n"
    "I originally built this app with the help of Claude AI by Anthropic, as a way to achieve "
    "lossless video archiving. But it isn't tied to one camera — I wanted it to be flexible "
    "across mobile phones, action cameras, DSLRs, drones, and gimbal cameras.\n\n"
    "This app is free for anyone who shares that passion. If it saves you time or helps "
    "preserve something precious, that's everything.\n\n"
    "If you'd like to support the project, there's a link further down this page. Thank you "
    "in advance — and I wish you the best capturing those special moments with the people you love.\n\n"
    "— John"
)

HOW_IT_WORKS = (
    "LunaVault FuseBox is built for anyone who shoots with one or more cameras and wants a "
    "single, properly archived master file at the end of each session.\n\n"
    "Here's the situation it was made for: when you record with a wireless Bluetooth microphone, "
    "your camera saves that wireless mic as the audio inside the MP4 — great for clear, close-up "
    "voice — while a separate WAV file preserves the camera's own on-board microphone. Each "
    "captures something the other doesn't: the wireless mic gets the voice up close, and the "
    "on-board mic holds the fuller room sound and acts as a safety net if the wireless signal "
    "ever drops or distorts. The trouble is you're left with the video and that backup audio in "
    "separate files.\n\n"
    "The Merge clips tab solves that. It scans your source folder, automatically pairs each video "
    "clip with its matching WAV backup, and orders them chronologically. Your clips are joined into "
    "one MOV master — and because matching footage is stream-copied rather than re-encoded, there's "
    "no quality loss; any clips that differ in resolution or frame rate are conformed at high quality "
    "so everything plays back as one seamless file. The on-board WAV audio is carefully aligned to "
    "the video and stored losslessly alongside the original wireless track, so every file keeps both "
    "microphones as clean, separate streams — nothing is thrown away, and you can choose whichever "
    "sounds best when you edit. You can also add an optional combined track that carries both mics "
    "together.\n\n"
    "The Extract and Recover tab covers the other direction, in two halves. Share lets you take any "
    "video, trim a moment, optionally apply a cinematic colour grade from the built-in library, and "
    "export a compressed MP4 ready to share on WhatsApp or social media — all in a few clicks. Extract "
    "recovers the original camera clips back out of a master you've already merged — bit-exact when "
    "the master has an archival track, and still correct by filename and camera grouping even for an "
    "older master or one merged without \"Archival master\" ticked.\n\n"
    "Your master files go wherever you point them: a cloud backup, an external drive, or a local "
    "folder. The app handles the technical side, so you can focus on capturing the moments that matter."
)

_COFFEE_TEXT = (
    "There's nothing quite like a good coffee on the road with my wife and toddler. "
    "LunaVault FuseBox is free for anyone who benefits from it — because preserving those "
    "special moments is its own reward. If you'd like to buy me a coffee along the way, it "
    "means the world and keeps this project going.\n\nThank you for your support.\n\n— John"
)

_CRYPTO = [
    {"label": "Lightning", "address": "jdm525@strike.me",
     "color": "#F7931A", "note": "Instant · zero fees · via Strike", "qr_file": "qr_lightning.svg"},
    {"label": "Bitcoin", "address": "bc1qzdl6wmqxjchxz0drrmymh0qxhq86z0sq2lcc95",
     "color": "#F7931A", "note": "BTC on-chain", "qr_file": "qr_bitcoin.svg"},
    {"label": "Ethereum", "address": "0x04e5c9567581d6e82ee96656c46328460314fa4c",
     "color": "#627EEA", "note": "ETH / ERC-20 tokens", "qr_file": "qr_ethereum.svg"},
]

CREDITS = [
    ("FFmpeg",       "The multimedia engine powering all video operations (GPL).", "https://ffmpeg.org"),
    ("PySide6 / Qt", "The cross-platform UI framework (LGPL).",                     "https://www.qt.io"),
    ("NumPy",        "Audio sync and drift analysis.",                              "https://numpy.org"),
    ("Claude AI",    "Anthropic's AI — design and development partner.",            "https://claude.ai"),
    ("Python",       "The language this app is written in.",                        "https://python.org"),
]


class AboutTab(QWidget):
    def __init__(self):
        super().__init__()
        self._cards: list[QFrame] = []
        self._dividers: list[QFrame] = []
        self._setup_ui()
        self._restyle()
        ctrl = theme.controller()
        if ctrl is not None:
            ctrl.changed.connect(self._restyle)

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)
        root = QVBoxLayout(content)
        root.setSpacing(20)
        root.setContentsMargins(40, 28, 40, 40)

        # ── Header ────────────────────────────────────────────────────────────
        header = QHBoxLayout()
        header.setSpacing(24)
        left_col = QVBoxLayout()
        left_col.setSpacing(8)
        left_col.addStretch()
        self._tagline = QLabel("Preserve every moment. Losslessly.")
        left_col.addWidget(self._tagline)
        ver = QApplication.instance().applicationVersion() if QApplication.instance() else "1.4.004"
        self._version_pill = QLabel(f"Version {ver or '1.4.004'}")
        self._version_pill.setFixedWidth(110)
        left_col.addWidget(self._version_pill)
        left_col.addStretch()
        header.addLayout(left_col, 1)
        header.addWidget(make_logo_widget(height=72), 0,
                         Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        root.addWidget(self._divider())
        root.addWidget(self._text_card(STORY_TEXT))

        # ── Feedback button ───────────────────────────────────────────────────
        fb_row = QHBoxLayout()
        fb_row.addStretch()
        self._fb_btn = QPushButton("  Send feedback or a question  →")
        self._fb_btn.setFixedHeight(36)
        self._fb_btn.clicked.connect(lambda: open_url(FEEDBACK_URL))
        fb_row.addWidget(self._fb_btn)
        fb_row.addStretch()
        root.addLayout(fb_row)

        root.addWidget(self._divider())

        self._how_title = QLabel("How it works")
        root.addWidget(self._how_title)
        root.addWidget(self._text_card(HOW_IT_WORKS))

        root.addWidget(self._divider())

        # ── Support (inline expand) ───────────────────────────────────────────
        self._support_btn = QPushButton()
        self._support_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._support_btn.clicked.connect(self._toggle_support)
        root.addWidget(self._support_btn)

        self._support_body = self._build_support_body()
        self._support_body.setVisible(False)
        root.addWidget(self._support_body)
        self._support_open = False
        self._update_support_btn()

        root.addWidget(self._divider())

        # ── Credits ───────────────────────────────────────────────────────────
        self._credits_title = QLabel("Built on the shoulders of giants")
        root.addWidget(self._credits_title)

        self._credit_rows = []
        for name, desc, url in CREDITS:
            row = QHBoxLayout()
            name_lbl = QLabel(name); name_lbl.setFixedWidth(120)
            desc_lbl = QLabel(desc); desc_lbl.setWordWrap(True)
            link_btn = QPushButton(url)
            link_btn.setObjectName("linkBtn")
            link_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            link_btn.setFixedWidth(200)
            link_btn.clicked.connect(lambda checked, u=url: open_url(u))
            row.addWidget(name_lbl)
            row.addWidget(desc_lbl, 1)
            row.addWidget(link_btn)
            root.addLayout(row)
            self._credit_rows.append((name_lbl, desc_lbl, link_btn))

        lic_row = QHBoxLayout()
        self._lic_btn = QPushButton("Open-source licenses")
        self._lic_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._lic_btn.clicked.connect(self._open_licenses)
        lic_row.addWidget(self._lic_btn)
        lic_row.addStretch()
        root.addLayout(lic_row)

        root.addWidget(self._divider())

        # ── Development history ──────────────────────────────────────────────
        self._history_title = QLabel("Development history")
        root.addWidget(self._history_title)
        self._history_sub = QLabel(
            f"What's changed in LunaVault FuseBox, newest first.   ·   Last updated {LAST_UPDATED}")
        root.addWidget(self._history_sub)

        self._history_rows: list = []
        for entry in HISTORY:
            root.addWidget(self._build_history_entry(entry))

        root.addStretch()

    # ── Support section ─────────────────────────────────────────────────────────

    def _build_support_body(self) -> QFrame:
        card = QFrame()
        self._cards.append(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 16, 20, 18)
        lay.setSpacing(14)
        self._coffee_lbl = QLabel(_COFFEE_TEXT)
        self._coffee_lbl.setWordWrap(True)
        lay.addWidget(self._coffee_lbl)

        # ── Primary: Buy me a coffee (one click for everyone) ─────────────────
        self._bmc_btn = QPushButton("☕  Buy me a coffee")
        self._bmc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._bmc_btn.setFixedHeight(46)
        self._bmc_btn.clicked.connect(lambda: open_url(COFFEE_URL))
        bmc_row = QHBoxLayout()
        bmc_row.addStretch(); bmc_row.addWidget(self._bmc_btn); bmc_row.addStretch()
        lay.addLayout(bmc_row)

        if _COFFEE_QR.exists():
            qr = QLabel()
            qr.setPixmap(QPixmap(str(_COFFEE_QR)).scaled(
                122, 122, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
            qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._coffee_qr_cap = QLabel("or scan to buy me a coffee")
            self._coffee_qr_cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
            qcol = QVBoxLayout(); qcol.setSpacing(4)
            qcol.addWidget(qr); qcol.addWidget(self._coffee_qr_cap)
            qwrap = QHBoxLayout(); qwrap.addStretch(); qwrap.addLayout(qcol); qwrap.addStretch()
            lay.addLayout(qwrap)

        # ── Secondary: crypto, tucked behind a reveal ─────────────────────────
        self._crypto_toggle = QPushButton()
        self._crypto_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._crypto_toggle.clicked.connect(self._toggle_crypto)
        lay.addWidget(self._crypto_toggle)

        self._crypto_box = QWidget()
        cbox = QVBoxLayout(self._crypto_box)
        cbox.setContentsMargins(0, 4, 0, 0)
        cbox.setSpacing(10)
        cards_row = QHBoxLayout()
        cards_row.setSpacing(14)
        for crypto in _CRYPTO:
            cards_row.addWidget(self._make_crypto_card(crypto))
        cbox.addLayout(cards_row)
        self._support_footer = QLabel("Crypto addresses shown are the developer's own wallets.")
        self._support_footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cbox.addWidget(self._support_footer)
        lay.addWidget(self._crypto_box)
        self._crypto_box.setVisible(False)
        self._crypto_open = False
        self._update_crypto_toggle()
        return card

    def _toggle_crypto(self):
        self._crypto_open = not self._crypto_open
        self._crypto_box.setVisible(self._crypto_open)
        self._update_crypto_toggle()

    def _update_crypto_toggle(self):
        chevron = "▾" if self._crypto_open else "▸"
        self._crypto_toggle.setText(f"Prefer crypto?   {chevron}")

    def _make_crypto_card(self, crypto: dict) -> QFrame:
        color = crypto["color"]
        card = QFrame()
        card.setStyleSheet(f"QFrame {{ background:transparent; border:1px solid {color}55; border-radius:10px; }}")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(8)

        lbl = QLabel(crypto["label"])
        lbl.setStyleSheet(f"font-size:15px; font-weight:bold; color:{color}; border:none;")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl)

        note = QLabel(crypto["note"])
        note.setStyleSheet("font-size:11px; border:none;")
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(note)

        qr_path = _ASSETS / crypto.get("qr_file", "")
        qr = QSvgWidget(str(qr_path)) if qr_path.exists() else QSvgWidget()
        qr.setFixedSize(150, 150)
        qr.setStyleSheet("background:transparent; border:none;")
        qr_row = QHBoxLayout(); qr_row.addStretch(); qr_row.addWidget(qr); qr_row.addStretch()
        layout.addLayout(qr_row)

        addr = crypto["address"]
        disp = addr if len(addr) <= 30 else addr[:14] + "…" + addr[-10:]
        addr_lbl = QLabel(disp)
        addr_lbl.setStyleSheet("font-size:10px; font-family:monospace; border:none;")
        addr_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(addr_lbl)

        copy_btn = QPushButton("Copy address")
        copy_btn.setStyleSheet(
            f"QPushButton {{ background:transparent; color:{color}; border:1px solid {color}55; "
            "border-radius:5px; font-size:12px; padding:5px 0; }"
            f"QPushButton:hover {{ background:{color}22; border-color:{color}; }}")
        copy_btn.clicked.connect(lambda _, a=addr, b=copy_btn: self._copy(a, b))
        layout.addWidget(copy_btn)
        return card

    @staticmethod
    def _copy(address: str, btn: QPushButton):
        from PySide6.QtCore import QTimer
        QApplication.clipboard().setText(address)
        original = btn.text()
        btn.setText("✓  Copied!")
        QTimer.singleShot(2000, lambda: btn.setText(original))

    def _toggle_support(self):
        self._support_open = not self._support_open
        self._support_body.setVisible(self._support_open)
        self._update_support_btn()

    def _update_support_btn(self):
        chevron = "▾" if self._support_open else "▸"
        self._support_btn.setText(f"  ♥  Support the project — keep it free   {chevron}")

    def _build_history_entry(self, entry) -> QFrame:
        card = QFrame()
        self._cards.append(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 14, 20, 14)
        lay.setSpacing(6)

        header = QHBoxLayout()
        date_lbl = QLabel(entry.date)
        date_lbl.setObjectName("historyDate")
        title_lbl = QLabel(entry.title)
        title_lbl.setObjectName("historyTitle")
        header.addWidget(date_lbl)
        header.addWidget(title_lbl, 1)
        lay.addLayout(header)

        summary_lbl = QLabel(entry.summary)
        summary_lbl.setWordWrap(True)
        summary_lbl.setObjectName("historySummary")
        lay.addWidget(summary_lbl)

        details_lbl = QLabel("\n".join(f"•  {d}" for d in entry.details))
        details_lbl.setWordWrap(True)
        details_lbl.setObjectName("historyDetails")
        details_lbl.setVisible(False)
        lay.addWidget(details_lbl)

        toggle_btn = QPushButton()
        toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        toggle_btn.setObjectName("historyToggle")
        toggle_btn.clicked.connect(lambda _, d=details_lbl, b=toggle_btn: self._toggle_history_entry(d, b))
        lay.addWidget(toggle_btn)
        self._update_history_toggle(toggle_btn, details_lbl)

        self._history_rows.append((card, date_lbl, title_lbl, summary_lbl, details_lbl, toggle_btn))
        return card

    def _toggle_history_entry(self, details_lbl: QLabel, toggle_btn: QPushButton):
        details_lbl.setVisible(not details_lbl.isVisible())
        self._update_history_toggle(toggle_btn, details_lbl)

    @staticmethod
    def _update_history_toggle(toggle_btn: QPushButton, details_lbl: QLabel):
        chevron = "▾" if details_lbl.isVisible() else "▸"
        toggle_btn.setText(f"{'Hide' if details_lbl.isVisible() else 'Show'} details   {chevron}")

    def _open_licenses(self):
        target = get_app_dir() / "licenses" / "THIRD-PARTY-LICENSES.md"
        if not target.exists():
            target = get_app_dir() / "licenses"
        open_path(target)

    # ── Builders / theming ──────────────────────────────────────────────────────

    def _text_card(self, text: str) -> QFrame:
        card = QFrame()
        self._cards.append(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(24, 18, 24, 18)
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setObjectName("cardText")
        lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        lay.addWidget(lbl)
        return card

    def _divider(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        self._dividers.append(line)
        return line

    def _restyle(self):
        p = theme.active_palette()
        self._tagline.setStyleSheet(f"font-size:18px; font-style:italic; color:{p.accent}; font-weight:500;")
        self._version_pill.setStyleSheet(
            f"background:{p.btn_bg}; color:{p.accent}; border-radius:10px; "
            "padding:3px 12px; font-size:11px; font-weight:bold;")
        for c in self._cards:
            c.setStyleSheet(f"QFrame {{ background:{p.surface2}; border:1px solid {p.border}; border-radius:8px; }}"
                            f"QLabel#cardText {{ color:{p.text}; font-size:13px; background:transparent; border:none; }}"
                            f"QLabel {{ color:{p.text}; background:transparent; border:none; }}")
        for d in self._dividers:
            d.setStyleSheet(f"border:none; border-top:1px solid {p.border}; margin:4px 0;")
        self._fb_btn.setStyleSheet(
            f"QPushButton {{ background:{p.accent}; color:{p.on_accent()}; border-radius:6px; "
            "font-size:13px; padding:0 20px; border:none; }"
            f"QPushButton:hover {{ background:{p.accent_hi}; }}")
        for t in (self._how_title, self._credits_title):
            t.setStyleSheet(f"font-size:15px; font-weight:bold; color:{p.accent};")
        self._support_btn.setStyleSheet(
            f"QPushButton {{ background:{p.surface2}; color:{p.text}; border:1px solid {p.border}; "
            "border-radius:8px; padding:10px 14px; text-align:left; font-size:13px; }"
            f"QPushButton:hover {{ border-color:{p.accent}; }}")
        self._coffee_lbl.setStyleSheet(f"font-size:13px; color:{p.text}; border:none;")
        self._support_footer.setStyleSheet(f"font-size:11px; color:{p.text_dim}; font-style:italic; border:none;")
        # Buy Me a Coffee brand button — keep its recognisable yellow in both themes
        self._bmc_btn.setStyleSheet(
            "QPushButton { background:#FFDD00; color:#000000; border:1px solid #000000; "
            "border-radius:8px; font-size:15px; font-weight:bold; padding:0 26px; }"
            "QPushButton:hover { background:#FFE84D; }")
        self._crypto_toggle.setStyleSheet(
            f"QPushButton {{ background:transparent; color:{p.text_mute}; border:none; "
            "font-size:12px; text-align:left; padding:2px 0; }"
            f"QPushButton:hover {{ color:{p.accent}; }}")
        if hasattr(self, "_coffee_qr_cap"):
            self._coffee_qr_cap.setStyleSheet(f"color:{p.text_dim}; font-size:11px; border:none;")
        for name_lbl, desc_lbl, link_btn in self._credit_rows:
            name_lbl.setStyleSheet(f"font-size:13px; font-weight:bold; color:{p.text};")
            desc_lbl.setStyleSheet(f"font-size:12px; color:{p.text_mute};")
            link_btn.setStyleSheet(
                f"QPushButton#linkBtn {{ background:transparent; color:{p.blue}; border:none; "
                "font-size:12px; padding:0; text-align:right; }"
                f"QPushButton#linkBtn:hover {{ color:{p.accent}; }}")
        self._lic_btn.setStyleSheet(
            f"QPushButton {{ background:{p.surface2}; color:{p.text}; border:1px solid {p.border}; "
            "border-radius:5px; padding:6px 12px; font-size:12px; }"
            f"QPushButton:hover {{ border-color:{p.accent}; }}")
        self._history_title.setStyleSheet(f"font-size:15px; font-weight:bold; color:{p.accent};")
        self._history_sub.setStyleSheet(f"font-size:12px; color:{p.text_mute};")
        for card, date_lbl, title_lbl, summary_lbl, details_lbl, toggle_btn in self._history_rows:
            date_lbl.setStyleSheet(f"font-size:11px; color:{p.text_dim}; font-family:monospace;")
            title_lbl.setStyleSheet(f"font-size:13px; font-weight:bold; color:{p.text};")
            summary_lbl.setStyleSheet(f"font-size:12px; color:{p.text_mute};")
            details_lbl.setStyleSheet(f"font-size:12px; color:{p.text}; padding-top:4px;")
            toggle_btn.setStyleSheet(
                f"QPushButton {{ background:transparent; color:{p.text_mute}; border:none; "
                "font-size:11px; text-align:left; padding:2px 0; }"
                f"QPushButton:hover {{ color:{p.accent}; }}")
