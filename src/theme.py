"""theme.py — LunaVault FuseBox palette + QSS builder + Dark/Light/System controller.

The whole app is themed from a single `Palette` of named tokens. `build_qss()`
turns a palette into the global Qt stylesheet; `ThemeController` swaps palettes
live (Dark / Light / System) and emits `changed` so custom-painted widgets can
restyle. Colours come straight from the brand banner: amber primary, gold
"moon" highlight, blue lens accent.
"""

from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtGui import QGuiApplication


# ── Palette ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Palette:
    name: str
    # Surfaces (darkest → lightest in dark mode; inverted in light mode)
    bg: str            # window / page
    panel: str         # tab bar, status bar
    surface: str       # inputs, header sections
    surface2: str      # cards, menus, tooltips
    alt_row: str       # alternating table rows
    input_dk: str      # recessed control bg (res buttons etc.)
    # Borders
    border_dk: str
    border: str
    border_hi: str
    # Brand accents
    accent: str        # amber — primary
    accent_hi: str     # lighter amber — hover / progress
    gold: str          # crescent-moon highlight
    blue: str          # lens accent — interactive/selection
    # Text
    text: str
    text_mute: str
    text_dim: str
    # Control states
    btn_bg: str
    hover_bg: str
    press_bg: str
    disabled_bg: str
    disabled_fg: str
    # Semantic status (warm-tuned; warn must NEVER equal accent)
    ok: str
    warn: str
    danger: str
    # Pane radial gradient stops
    pane_0: str
    pane_1: str
    pane_2: str
    # One-off banner surfaces
    banner_warn_bg: str = "#7A4018"
    banner_info_bg: str = "#1A2A3A"
    # Text on top of accent (and other saturated) fills
    on_accent_col: str = "#1A1206"
    # Is this a light palette?
    is_light: bool = False

    def on_accent(self) -> str:
        """Readable text colour on top of an accent fill."""
        return self.on_accent_col


DARK = Palette(
    name="dark",
    bg="#0E0A06", panel="#100806", surface="#18110A", surface2="#1A1008",
    alt_row="#130A04", input_dk="#0A0703",
    border_dk="#1E1309", border="#26190E", border_hi="#5A3018",
    accent="#D4863C", accent_hi="#E0954A", gold="#E8B838", blue="#2D7FD0",
    text="#F6E9D6", text_mute="#A6845C", text_dim="#C6A279",
    btn_bg="#3A2010", hover_bg="#4A2A14", press_bg="#2A1808",
    disabled_bg="#1A0E06", disabled_fg="#6A5030",
    ok="#3FB765", warn="#E0A33A", danger="#E0685A",
    pane_0="#1D1207", pane_1="#140D06", pane_2="#0E0A06",
    banner_warn_bg="#7A4018", banner_info_bg="#1A2A3A",
    on_accent_col="#1A1206",
    is_light=False,
)

LIGHT = Palette(
    name="light",
    bg="#EFE7D9", panel="#E7DCCB", surface="#FBF6EC", surface2="#FFFFFF",
    alt_row="#E9DFCC", input_dk="#EADFCB",
    border_dk="#EBE0CE", border="#E4D7C2", border_hi="#C9B596",
    accent="#C0742E", accent_hi="#A9611F", gold="#D9A41E", blue="#2D7FD0",
    text="#2A1E12", text_mute="#7A6446", text_dim="#96805C",
    btn_bg="#E7DCC8", hover_bg="#DFD2BA", press_bg="#D6C7AC",
    disabled_bg="#EBE2D2", disabled_fg="#B8A98F",
    ok="#2E9E57", warn="#CE9A22", danger="#C6473A",
    pane_0="#FBF6EC", pane_1="#EFE7D9", pane_2="#E8DEC9",
    banner_warn_bg="#EEDDBE", banner_info_bg="#DCE8F4",
    on_accent_col="#FFF6EC",
    is_light=True,
)


# ── QSS builder ───────────────────────────────────────────────────────────────

def build_qss(p: Palette) -> str:
    on_accent = p.on_accent()
    return (
        f"QWidget {{ background:{p.bg}; color:{p.text}; "
        "font-family:-apple-system,'Segoe UI',Arial,sans-serif; font-size:13px; }"
        f"QMainWindow {{ background:{p.bg}; }}"
        "QTabWidget::pane { background:qradialgradient("
        "cx:0.5, cy:0.35, radius:0.75, fx:0.5, fy:0.35, "
        f"stop:0 {p.pane_0}, stop:0.6 {p.pane_1}, stop:1 {p.pane_2}); border:none; }}"
        "QScrollArea { background:transparent; }"
        "QScrollArea>QWidget>QWidget { background:transparent; }"
        f"QTabBar {{ background:{p.panel}; }}"
        f"QTabBar::tab {{ background:{p.panel}; color:{p.text_dim}; padding:9px 20px; "
        "font-size:13px; border:none; border-bottom:2px solid transparent; }"
        f"QTabBar::tab:selected {{ color:{p.text}; font-weight:bold; "
        f"border-bottom:2px solid {p.accent}; background:{p.bg}; }}"
        f"QTabBar::tab:hover:!selected {{ color:{p.accent}; background:{p.surface}; }}"
        f"QPushButton {{ background:{p.btn_bg}; color:{p.text}; border:1px solid {p.border_hi}; "
        "border-radius:5px; padding:5px 14px; }"
        f"QPushButton:hover {{ background:{p.hover_bg}; border-color:{p.accent}; }}"
        f"QPushButton:pressed {{ background:{p.press_bg}; }}"
        f"QPushButton:disabled {{ background:{p.disabled_bg}; color:{p.disabled_fg}; border-color:{p.border_dk}; }}"
        f"QPushButton:checked {{ background:{p.surface2}; border-color:{p.accent}; color:{p.accent}; }}"
        f"QLineEdit {{ background:{p.surface}; color:{p.text}; border:1px solid {p.border}; "
        "border-radius:4px; padding:4px 8px; "
        f"selection-background-color:{p.accent}; selection-color:{on_accent}; }}"
        f"QLineEdit:focus {{ border-color:{p.accent}; }}"
        f"QLineEdit:read-only {{ color:{p.text_mute}; border-color:{p.border_dk}; }}"
        f"QComboBox {{ background:{p.surface}; color:{p.text}; border:1px solid {p.border}; "
        "border-radius:4px; padding:4px 8px; min-width:120px; }"
        f"QComboBox:hover {{ border-color:{p.accent}; }}"
        "QComboBox::drop-down { border:none; width:22px; }"
        f"QComboBox QAbstractItemView {{ background:{p.surface2}; color:{p.text}; "
        f"selection-background-color:{p.accent}; selection-color:{on_accent}; "
        f"border:1px solid {p.border}; outline:none; }}"
        f"QTableWidget {{ background:{p.bg}; alternate-background-color:{p.alt_row}; "
        f"gridline-color:{p.border_dk}; border:1px solid {p.border_dk}; border-radius:4px; "
        f"selection-background-color:{p.btn_bg}; selection-color:{p.text}; outline:none; }}"
        f"QHeaderView::section {{ background:{p.surface}; color:{p.text_mute}; border:none; "
        f"border-bottom:1px solid {p.border}; border-right:1px solid {p.border_dk}; "
        "padding:4px 8px; font-size:11px; font-weight:bold; letter-spacing:0.5px; }"
        "QTableWidget::item { padding:3px 6px; }"
        f"QTableWidget::item:selected {{ background:{p.btn_bg}; color:{p.text}; }}"
        f"QProgressBar {{ background:{p.disabled_bg}; border:1px solid {p.border}; border-radius:4px; "
        f"height:18px; text-align:center; color:{p.text}; font-size:11px; }}"
        "QProgressBar::chunk { background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
        f"stop:0 {p.accent},stop:1 {p.accent_hi}); border-radius:4px; }}"
        f"QScrollBar:vertical {{ background:{p.panel}; width:8px; border-radius:4px; }}"
        f"QScrollBar::handle:vertical {{ background:{p.btn_bg}; border-radius:4px; min-height:24px; }}"
        f"QScrollBar::handle:vertical:hover {{ background:{p.accent}; }}"
        "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }"
        f"QScrollBar:horizontal {{ background:{p.panel}; height:8px; border-radius:4px; }}"
        f"QScrollBar::handle:horizontal {{ background:{p.btn_bg}; border-radius:4px; min-width:24px; }}"
        f"QScrollBar::handle:horizontal:hover {{ background:{p.accent}; }}"
        "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width:0; }"
        f"QCheckBox {{ color:{p.text_mute}; spacing:6px; }}"
        f"QCheckBox::indicator {{ width:14px; height:14px; border:1px solid {p.border}; "
        f"border-radius:3px; background:{p.surface}; }}"
        f"QCheckBox::indicator:checked {{ background:{p.accent}; border-color:{p.accent}; }}"
        f"QStatusBar {{ background:{p.panel}; color:{p.text_dim}; "
        f"border-top:1px solid {p.border_dk}; font-size:11px; }}"
        f"QToolTip {{ background:{p.surface2}; color:{p.text}; border:1px solid {p.accent}; "
        "border-radius:4px; padding:4px 8px; font-size:12px; }"
        f"QMessageBox {{ background:{p.surface2}; }}"
        f"QMessageBox QLabel {{ color:{p.text}; }}"
    )


# ── Controller ────────────────────────────────────────────────────────────────

_MODES = ("dark", "light", "system")
_controller: Optional["ThemeController"] = None


class ThemeController(QObject):
    """Owns the active palette; swaps Dark/Light/System and notifies listeners."""

    changed = Signal()   # emitted after the palette changes

    def __init__(self, app, settings=None):
        super().__init__()
        self._app      = app
        self._settings = settings
        mode = "system"
        if settings is not None:
            mode = settings.get("theme_mode", "system")
        self._mode = mode if mode in _MODES else "system"
        # Follow the OS when in system mode
        hints = QGuiApplication.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(self._on_os_scheme_changed)

    # -- public --------------------------------------------------------------
    @property
    def mode(self) -> str:
        return self._mode

    @property
    def palette(self) -> Palette:
        return self._resolve()

    def set_mode(self, mode: str):
        if mode not in _MODES:
            return
        self._mode = mode
        if self._settings is not None:
            self._settings.set("theme_mode", mode)
        self.apply()

    def apply(self):
        self._app.setStyleSheet(build_qss(self._resolve()))
        self.changed.emit()

    # -- internal ------------------------------------------------------------
    def _resolve(self) -> Palette:
        if self._mode == "dark":
            return DARK
        if self._mode == "light":
            return LIGHT
        return self._os_palette()

    def _os_palette(self) -> Palette:
        hints = QGuiApplication.styleHints()
        scheme = getattr(hints, "colorScheme", lambda: None)()
        if scheme == Qt.ColorScheme.Light:
            return LIGHT
        return DARK   # default to dark on Dark/Unknown

    def _on_os_scheme_changed(self, _scheme):
        if self._mode == "system":
            self.apply()


def init_controller(app, settings=None) -> ThemeController:
    global _controller
    _controller = ThemeController(app, settings)
    return _controller


def controller() -> Optional[ThemeController]:
    return _controller


def active_palette() -> Palette:
    """The current palette — safe to call before a controller exists (→ DARK)."""
    return _controller.palette if _controller is not None else DARK
