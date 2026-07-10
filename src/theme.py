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
    # One-off banner surfaces
    banner_warn_bg: str = "#7A4018"
    banner_info_bg: str = "#1A2A3A"
    # Text on top of accent (and other saturated) fills
    on_accent_col: str = "#1A1206"
    # Is this a light palette?
    is_light: bool = False
    # Corner radii (px) — legacy defaults match the original look exactly; the
    # friendly palettes round them up for a softer, more approachable feel.
    radius: int = 5       # buttons / cards
    radius_sm: int = 4    # inputs, combos, tables, progress, tooltips

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
    banner_warn_bg="#EEDDBE", banner_info_bg="#DCE8F4",
    on_accent_col="#FFF6EC",
    is_light=True,
)


# ── Friendly palettes (task #12) — the "friendly & approachable" look used in the
# new (friendly) UI mode. Airier and warmer than the technical DARK/LIGHT above,
# which are kept for Legacy mode so the hidden toggle still shows a true "before".
# First-pass values, meant to be iterated on. ──────────────────────────────────

FRIENDLY_LIGHT = Palette(
    name="friendly-light",
    bg="#F4EEE3", panel="#ECE4D6", surface="#FFFFFF", surface2="#FFFFFF",
    alt_row="#F6F1E8", input_dk="#EFE8DB",
    border_dk="#EAE1D2", border="#E0D5C1", border_hi="#CDBBA0",
    accent="#D07C33", accent_hi="#B96826", gold="#D9A41E", blue="#3B86C9",
    text="#33291B", text_mute="#857053", text_dim="#9C8663",
    btn_bg="#EFE7D8", hover_bg="#E7DECC", press_bg="#DDD2BC",
    disabled_bg="#EFE8DA", disabled_fg="#BCAD93",
    ok="#2E9E57", warn="#CE9A22", danger="#C6473A",
    banner_warn_bg="#F1E1C4", banner_info_bg="#DEE9F3",
    on_accent_col="#FFF7EE",
    is_light=True, radius=9, radius_sm=7,
)

FRIENDLY_DARK = Palette(
    name="friendly-dark",
    # Cards/surfaces are lifted well clear of the page background so bright photo
    # thumbnails rest on a comfortable mid-dark frame rather than floating on near-
    # black (which read as harsh, over-contrasted "white cards on black").
    bg="#1B1610", panel="#171209", surface="#2A2115", surface2="#31281A",
    alt_row="#241C12", input_dk="#221A11",
    border_dk="#2A1F12", border="#3E2F1C", border_hi="#6A3A1C",
    accent="#E0954A", accent_hi="#ECA85C", gold="#E8B838", blue="#4A97D8",
    text="#F5EBDA", text_mute="#B08F63", text_dim="#CDAA80",
    btn_bg="#3E2614", hover_bg="#4E3018", press_bg="#2E1C0E",
    disabled_bg="#201408", disabled_fg="#74583A",
    ok="#43BD69", warn="#E0A33A", danger="#E67265",
    banner_warn_bg="#8A4A1E", banner_info_bg="#223349",
    on_accent_col="#201509",
    is_light=False, radius=9, radius_sm=7,
)


# ── QSS builder ───────────────────────────────────────────────────────────────

def build_qss(p: Palette) -> str:
    on_accent = p.on_accent()
    return (
        f"QWidget {{ background:{p.bg}; color:{p.text}; "
        "font-family:-apple-system,'Segoe UI',Arial,sans-serif; font-size:13px; }"
        f"QMainWindow {{ background:{p.bg}; }}"
        # Labels never paint their own background — same reasoning as the
        # QCheckBox/QRadioButton `background:transparent` further down: a label
        # inside a `p.surface` section card would otherwise paint a `p.bg`
        # rectangle a shade off from the card, reading as a bar. A label that
        # genuinely needs a fill (a badge/pill) sets it explicitly by
        # objectName and overrides this.
        "QLabel { background:transparent; }"
        # Flat, not a radial gradient: every plain child widget (QLabel,
        # QCheckBox, ...) paints its OWN flat `p.bg` rectangle per the QWidget
        # rule above — against a gradient pane, each of those rectangles
        # visibly clashes with whatever shade of the gradient sits behind it,
        # showing up as a distinct "bar" behind every row/checkbox/label.
        # Confirmed directly against a real screenshot: switching to one flat
        # color (matching what child widgets already paint) removes every
        # one of those bars, since there's no longer a second shade for them
        # to disagree with.
        f"QTabWidget::pane {{ background:{p.bg}; border:none; }}"
        "QScrollArea { background:transparent; }"
        "QScrollArea>QWidget>QWidget { background:transparent; }"
        f"QTabBar {{ background:{p.panel}; }}"
        f"QTabBar::tab {{ background:{p.panel}; color:{p.text_dim}; padding:9px 20px; "
        "font-size:13px; border:none; border-bottom:2px solid transparent; }"
        f"QTabBar::tab:selected {{ color:{p.text}; font-weight:bold; "
        f"border-bottom:2px solid {p.accent}; background:{p.bg}; }}"
        f"QTabBar::tab:hover:!selected {{ color:{p.accent}; background:{p.surface}; }}"
        f"QPushButton {{ background:{p.btn_bg}; color:{p.text}; border:1px solid {p.border_hi}; "
        f"border-radius:{p.radius}px; padding:5px 14px; }}"
        f"QPushButton:hover {{ background:{p.hover_bg}; border-color:{p.accent}; }}"
        f"QPushButton:pressed {{ background:{p.press_bg}; }}"
        f"QPushButton:disabled {{ background:{p.disabled_bg}; color:{p.disabled_fg}; border-color:{p.border_dk}; }}"
        f"QPushButton:checked {{ background:{p.surface2}; border-color:{p.accent}; color:{p.accent}; }}"
        f"QLineEdit {{ background:{p.surface}; color:{p.text}; border:1px solid {p.border}; "
        f"border-radius:{p.radius_sm}px; padding:4px 8px; "
        f"selection-background-color:{p.accent}; selection-color:{on_accent}; }}"
        f"QLineEdit:focus {{ border-color:{p.accent}; }}"
        f"QLineEdit:read-only {{ color:{p.text_mute}; border-color:{p.border_dk}; }}"
        f"QComboBox {{ background:{p.surface}; color:{p.text}; border:1px solid {p.border}; "
        f"border-radius:{p.radius_sm}px; padding:4px 8px; min-width:120px; }}"
        f"QComboBox:hover {{ border-color:{p.accent}; }}"
        "QComboBox::drop-down { border:none; width:22px; }"
        f"QComboBox QAbstractItemView {{ background:{p.surface2}; color:{p.text}; "
        f"selection-background-color:{p.accent}; selection-color:{on_accent}; "
        f"border:1px solid {p.border}; outline:none; }}"
        f"QTableWidget, QTreeWidget {{ background:{p.bg}; alternate-background-color:{p.alt_row}; "
        f"gridline-color:{p.border_dk}; border:1px solid {p.border_dk}; border-radius:{p.radius_sm}px; "
        f"selection-background-color:{p.btn_bg}; selection-color:{p.text}; outline:none; }}"
        f"QHeaderView::section {{ background:{p.surface}; color:{p.text_mute}; border:none; "
        f"border-bottom:1px solid {p.border}; border-right:1px solid {p.border_dk}; "
        "padding:4px 8px; font-size:11px; font-weight:bold; letter-spacing:0.5px; }"
        "QTableWidget::item, QTreeWidget::item { padding:3px 6px; }"
        f"QTableWidget::item:selected, QTreeWidget::item:selected {{ background:{p.btn_bg}; color:{p.text}; }}"
        f"QProgressBar {{ background:{p.disabled_bg}; border:1px solid {p.border}; border-radius:{p.radius_sm}px; "
        f"height:18px; text-align:center; color:{p.text}; font-size:11px; }}"
        "QProgressBar::chunk { background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
        f"stop:0 {p.accent},stop:1 {p.accent_hi}); border-radius:{p.radius_sm}px; }}"
        f"QScrollBar:vertical {{ background:{p.panel}; width:8px; border-radius:4px; }}"
        f"QScrollBar::handle:vertical {{ background:{p.btn_bg}; border-radius:4px; min-height:24px; }}"
        f"QScrollBar::handle:vertical:hover {{ background:{p.accent}; }}"
        "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }"
        f"QScrollBar:horizontal {{ background:{p.panel}; height:8px; border-radius:4px; }}"
        f"QScrollBar::handle:horizontal {{ background:{p.btn_bg}; border-radius:4px; min-width:24px; }}"
        f"QScrollBar::handle:horizontal:hover {{ background:{p.accent}; }}"
        "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width:0; }"
        # Leaf display widgets paint NO background of their own — they let
        # whatever card/section they sit inside show through. The blanket
        # `QWidget {{ background:p.bg }}` rule above otherwise makes every
        # checkbox/radio/label paint a `p.bg` rectangle, which is a DIFFERENT
        # shade from the `p.surface` section card behind it — so each row
        # showed up as a distinct full-width bar (confirmed directly against a
        # real screenshot, in both light and dark). Transparent here removes
        # the second shade entirely; anything that genuinely needs its own
        # fill (badges, the indicator box below, inputs) sets it explicitly
        # and still overrides this.
        f"QCheckBox {{ color:{p.text_mute}; spacing:6px; background:transparent; }}"
        f"QCheckBox::indicator {{ width:14px; height:14px; border:1px solid {p.border}; "
        f"border-radius:3px; background:{p.surface}; }}"
        f"QCheckBox::indicator:checked {{ background:{p.accent}; border-color:{p.accent}; }}"
        f"QCheckBox:disabled {{ color:{p.disabled_fg}; }}"
        f"QCheckBox::indicator:disabled {{ background:{p.disabled_bg}; border-color:{p.border_dk}; }}"
        f"QCheckBox::indicator:checked:disabled {{ background:{p.disabled_fg}; border-color:{p.disabled_fg}; }}"
        f"QRadioButton {{ color:{p.text_mute}; spacing:6px; background:transparent; }}"
        f"QRadioButton::indicator {{ width:14px; height:14px; border:1px solid {p.border}; "
        f"border-radius:7px; background:{p.surface}; }}"
        f"QRadioButton::indicator:checked {{ background:{p.accent}; border-color:{p.accent}; }}"
        f"QRadioButton:disabled {{ color:{p.disabled_fg}; }}"
        f"QRadioButton::indicator:disabled {{ background:{p.disabled_bg}; border-color:{p.border_dk}; }}"
        f"QStatusBar {{ background:{p.panel}; color:{p.text_dim}; "
        f"border-top:1px solid {p.border_dk}; font-size:11px; }}"
        f"QToolTip {{ background:{p.surface2}; color:{p.text}; border:1px solid {p.accent}; "
        f"border-radius:{p.radius_sm}px; padding:4px 8px; font-size:12px; }}"
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
        ui_mode = "friendly"
        if settings is not None:
            mode = settings.get("theme_mode", "system")
            ui_mode = settings.get("ui_mode", "friendly")
        self._mode = mode if mode in _MODES else "system"
        # friendly = the new "friendly & approachable" palettes; legacy = the
        # original DARK/LIGHT, so the hidden UI-mode toggle shows a true "before".
        self._ui_mode = ui_mode if ui_mode in ("friendly", "legacy") else "friendly"
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

    def set_ui_mode(self, ui_mode: str):
        """Switch the friendly/legacy palette family (the hidden UI-mode toggle
        in the window corner drives this alongside re-laying-out the tabs)."""
        if ui_mode not in ("friendly", "legacy") or ui_mode == self._ui_mode:
            return
        self._ui_mode = ui_mode
        self.apply()

    def apply(self):
        self._app.setStyleSheet(build_qss(self._resolve()))
        self.changed.emit()

    # -- internal ------------------------------------------------------------
    def _resolve(self) -> Palette:
        light = self._is_light()
        if self._ui_mode == "legacy":
            return LIGHT if light else DARK
        return FRIENDLY_LIGHT if light else FRIENDLY_DARK

    def _is_light(self) -> bool:
        """Whether the active theme is a light one (dark/light/system resolved)."""
        if self._mode == "light":
            return True
        if self._mode == "dark":
            return False
        hints = QGuiApplication.styleHints()
        scheme = getattr(hints, "colorScheme", lambda: None)()
        return scheme == Qt.ColorScheme.Light   # default to dark on Dark/Unknown

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
