"""Theme discipline regression tests.

Guards two things the v1.4 theme pass fixed:
  - no module outside theme.py/about_tab.py/scopes_panel.py hardcodes a
    colour instead of reading it from the active Palette. about_tab.py is
    exempt because its literals are third-party brand colours (Bitcoin,
    Ethereum, Buy Me a Coffee), not theme tokens. scopes_panel.py is exempt
    for the same reason in spirit: its red/green/blue channel colours in
    the histogram and waveform-parade scopes must stay TRUE red/green/blue
    to be readable as an RGB scope — recolouring them to the app's amber
    accent would defeat the point of the display;
  - `warn` is visually distinct from `accent` in both palettes, so a
    caution ("Will transcode") no longer reads as the brand colour.
"""

import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from theme import DARK, LIGHT

HEX_RE = re.compile(r"#[0-9A-Fa-f]{3,4}\b|#[0-9A-Fa-f]{6}\b|#[0-9A-Fa-f]{8}\b")
EXEMPT = {"theme.py", "about_tab.py", "scopes_panel.py"}


def _offenders() -> list[str]:
    hits = []
    for path in sorted(SRC.rglob("*.py")):
        if path.name in EXEMPT or "__pycache__" in path.parts:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for m in HEX_RE.finditer(line):
                hits.append(f"{path.relative_to(SRC)}:{lineno}: {m.group(0)}  ({line.strip()[:80]})")
    return hits


def test_no_hardcoded_colours_outside_theme():
    hits = _offenders()
    assert not hits, "hardcoded colour literals found (route through theme.active_palette()):\n" + "\n".join(hits)


def test_warn_is_not_accent():
    assert DARK.warn != DARK.accent, "DARK.warn must not equal DARK.accent"
    assert LIGHT.warn != LIGHT.accent, "LIGHT.warn must not equal LIGHT.accent"


def test_both_palettes_define_every_field():
    from dataclasses import fields
    for f in fields(DARK):
        if f.name == "is_light":
            continue
        assert getattr(DARK, f.name), f"DARK.{f.name} is falsy"
        assert getattr(LIGHT, f.name), f"LIGHT.{f.name} is falsy"


if __name__ == "__main__":
    test_no_hardcoded_colours_outside_theme()
    test_warn_is_not_accent()
    test_both_palettes_define_every_field()
    print("test_theme: all tests passed")
