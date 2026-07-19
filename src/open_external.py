"""open_external.py — reliably open web URLs and local files/folders in the
user's default handler, including from a frozen (PyInstaller) build.

The problem this solves:
    PyInstaller prepends its bundle directory to LD_LIBRARY_PATH so the frozen
    app finds its own bundled libraries. But QDesktopServices.openUrl() shells
    out to the desktop's URL/file handler (xdg-open -> kde-open5 / gio / ...),
    and that child process *inherits* our polluted LD_LIBRARY_PATH. On Linux
    those handlers are themselves Qt/GLib programs, so they load our bundled Qt
    instead of the system one, fail to find the "xcb" platform plugin, and
    crash — the link silently does nothing.

    PyInstaller saves the pre-launch value in LD_LIBRARY_PATH_ORIG. We restore
    it (or drop the override entirely) for the spawned helper so it loads the
    system's libraries and works normally.

Only the frozen-Linux path is special-cased; everywhere else we defer to Qt's
QDesktopServices, which behaves correctly.
"""

from __future__ import annotations

import os
import subprocess
import sys

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices


def _frozen_linux() -> bool:
    return sys.platform.startswith("linux") and getattr(sys, "frozen", False)


def _clean_env() -> dict:
    """A copy of the environment with PyInstaller's LD_LIBRARY_PATH override
    undone, so a spawned system helper loads system libraries, not our bundle."""
    env = dict(os.environ)
    orig = env.get("LD_LIBRARY_PATH_ORIG")
    if orig is not None:
        env["LD_LIBRARY_PATH"] = orig
    else:
        env.pop("LD_LIBRARY_PATH", None)
    return env


def _spawn_xdg_open(target: str) -> bool:
    """Launch xdg-open on `target` with a de-polluted environment, detached.
    Returns True if the process was started, False on failure (caller falls
    back to Qt)."""
    try:
        subprocess.Popen(
            ["xdg-open", target],
            env=_clean_env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except Exception:
        return False


def open_url(url: str) -> None:
    """Open a web URL in the user's default browser."""
    if _frozen_linux() and _spawn_xdg_open(url):
        return
    QDesktopServices.openUrl(QUrl(url))


def open_path(path) -> None:
    """Open a local file or folder in the user's default application / file
    manager."""
    target = str(path)
    if _frozen_linux() and _spawn_xdg_open(target):
        return
    QDesktopServices.openUrl(QUrl.fromLocalFile(target))
