"""Tests for open_external.py — the environment-sanitising URL/file opener.

The bug being guarded: in a frozen (PyInstaller) build, PyInstaller prepends its
bundle dir to LD_LIBRARY_PATH. When the app shells out to the desktop's URL
handler (kde-open5 / xdg-open), the child inherits that path, loads the app's
bundled Qt instead of the system's, and crashes — so every About-tab link does
nothing. The fix restores LD_LIBRARY_PATH from PyInstaller's LD_LIBRARY_PATH_ORIG
(or drops it) for the spawned helper. These tests pin that env logic.

Standalone-runnable (no pytest required).
"""

import os
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

import open_external as oe  # noqa: E402


def _with_env(env, fn):
    saved = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update(env)
        return fn()
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_clean_env_restores_original_ld_path():
    """When PyInstaller saved the pre-launch path, the child gets it back."""
    env = {"LD_LIBRARY_PATH": "/app/_internal:/usr/lib/x",
           "LD_LIBRARY_PATH_ORIG": "/usr/lib/x"}
    out = _with_env(env, oe._clean_env)
    assert out["LD_LIBRARY_PATH"] == "/usr/lib/x"


def test_clean_env_drops_ld_path_when_no_original():
    """A frozen build with nothing saved should drop the override entirely,
    not leave the bundle path in place for the child helper."""
    env = {"LD_LIBRARY_PATH": "/app/_internal"}
    out = _with_env(env, oe._clean_env)
    assert "LD_LIBRARY_PATH" not in out


def test_clean_env_leaves_normal_env_untouched():
    """No LD_LIBRARY_PATH at all (typical dev / non-frozen) -> unchanged."""
    env = {"PATH": "/usr/bin", "HOME": "/home/x"}
    out = _with_env(env, oe._clean_env)
    assert "LD_LIBRARY_PATH" not in out
    assert out["PATH"] == "/usr/bin"


def test_non_frozen_falls_back_to_qt(monkeypatch=None):
    """Outside a frozen Linux build we must defer to QDesktopServices and never
    spawn xdg-open (which may not exist / behave differently in dev)."""
    calls = {"qt": [], "spawn": []}

    _saved = (oe._frozen_linux, oe._spawn_xdg_open, oe.QDesktopServices.openUrl)
    try:
        oe._frozen_linux = lambda: False
        oe._spawn_xdg_open = lambda t: (calls["spawn"].append(t) or True)
        oe.QDesktopServices.openUrl = staticmethod(lambda u: calls["qt"].append(u))
        oe.open_url("https://example.com")
        assert calls["spawn"] == []          # never spawned
        assert len(calls["qt"]) == 1         # went through Qt
    finally:
        oe._frozen_linux, oe._spawn_xdg_open, oe.QDesktopServices.openUrl = _saved


def test_frozen_linux_prefers_spawn():
    """Inside a frozen Linux build we spawn the de-polluted helper and do NOT
    fall through to Qt when the spawn succeeds."""
    calls = {"qt": [], "spawn": []}
    _saved = (oe._frozen_linux, oe._spawn_xdg_open, oe.QDesktopServices.openUrl)
    try:
        oe._frozen_linux = lambda: True
        oe._spawn_xdg_open = lambda t: (calls["spawn"].append(t) or True)
        oe.QDesktopServices.openUrl = staticmethod(lambda u: calls["qt"].append(u))
        oe.open_url("https://example.com")
        assert calls["spawn"] == ["https://example.com"]
        assert calls["qt"] == []             # spawn succeeded -> no Qt fallback
    finally:
        oe._frozen_linux, oe._spawn_xdg_open, oe.QDesktopServices.openUrl = _saved


if __name__ == "__main__":
    test_clean_env_restores_original_ld_path()
    test_clean_env_drops_ld_path_when_no_original()
    test_clean_env_leaves_normal_env_untouched()
    test_non_frozen_falls_back_to_qt()
    test_frozen_linux_prefers_spawn()
    print("test_open_external: all tests passed")
