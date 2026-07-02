"""crash_log.py — first-thing crash and unhandled-exception capture.

`install()` wires four capture layers into a single crash.log kept beside
settings.json, so the rare "app just closed itself" leaves evidence:

  - `faulthandler` — native faults and hard aborts (e.g. Qt destroying a live
    QThread), with tracebacks for all threads;
  - `sys.excepthook` / `threading.excepthook` — unhandled Python exceptions;
  - a Qt message handler — warnings/criticals, with anything mentioning
    "QThread" tagged [THREAD-LIFETIME].

The log is trimmed to its tail on startup so it never grows unbounded.
This module imports no Qt at module level; the pure helpers are unit-tested
without a QApplication.
"""

import faulthandler
import os
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

MAX_BYTES = 512 * 1024

_file = None                # kept open for the process lifetime — faulthandler needs a live fd
_prev_sys_hook = None
_prev_thread_hook = None
_prev_qt_handler = None


# ── Pure helpers (unit-tested) ────────────────────────────────────────────────

def trim_to_tail(path: Path, max_bytes: int = MAX_BYTES) -> None:
    """If the log exceeds max_bytes, keep only its last max_bytes/2 (whole lines)."""
    try:
        path = Path(path)
        if not path.exists() or path.stat().st_size <= max_bytes:
            return
        data = path.read_bytes()[-(max_bytes // 2):]
        nl = data.find(b"\n")
        if 0 <= nl < len(data) - 1:
            data = data[nl + 1:]
        path.write_bytes(data)
    except OSError:
        pass


def format_entry(kind: str, text: str, now: Optional[datetime] = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    return f"[{stamp}] {kind}\n{text.rstrip()}\n"


def tag_qt_message(message: str) -> str:
    return f"[THREAD-LIFETIME] {message}" if "QThread" in message else message


# ── Installation ──────────────────────────────────────────────────────────────

def _write(kind: str, text: str) -> None:
    if _file is None:
        return
    try:
        _file.write(format_entry(kind, text))
        _file.flush()
    except OSError:
        pass


def install(log_path: Path) -> Optional[Path]:
    """Enable all capture layers, writing to `log_path`. Safe to call once."""
    global _file, _prev_sys_hook, _prev_thread_hook
    if _file is not None:
        return log_path
    log_path = Path(log_path)
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        trim_to_tail(log_path)
        _file = open(log_path, "a", encoding="utf-8", errors="replace")
    except OSError:
        return None          # never let diagnostics stop the app from starting
    _write("SESSION", f"--- app start · pid {os.getpid()} · python {sys.version.split()[0]} ---")

    faulthandler.enable(file=_file, all_threads=True)

    _prev_sys_hook = sys.excepthook

    def _sys_hook(exc_type, exc, tb):
        _write("UNHANDLED EXCEPTION",
               "".join(traceback.format_exception(exc_type, exc, tb)))
        if _prev_sys_hook is not None:
            try:
                _prev_sys_hook(exc_type, exc, tb)
            except Exception:
                pass

    sys.excepthook = _sys_hook

    _prev_thread_hook = threading.excepthook

    def _thread_hook(args):
        name = args.thread.name if args.thread is not None else "?"
        _write(f"UNHANDLED EXCEPTION in thread '{name}'",
               "".join(traceback.format_exception(
                   args.exc_type, args.exc_value, args.exc_traceback)))
        if _prev_thread_hook is not None:
            try:
                _prev_thread_hook(args)
            except Exception:
                pass

    threading.excepthook = _thread_hook

    _install_qt_handler()
    return log_path


def _install_qt_handler() -> None:
    global _prev_qt_handler
    try:
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler
    except ImportError:
        return

    levels = {
        QtMsgType.QtWarningMsg:  "QT WARNING",
        QtMsgType.QtCriticalMsg: "QT CRITICAL",
        QtMsgType.QtFatalMsg:    "QT FATAL",
    }

    def _handler(mode, context, message):
        kind = levels.get(mode)
        if kind is not None:
            _write(kind, tag_qt_message(message))
        if _prev_qt_handler is not None:
            try:
                _prev_qt_handler(mode, context, message)
            except Exception:
                pass
        elif sys.stderr is not None:
            try:
                print(message, file=sys.stderr)
            except OSError:
                pass

    _prev_qt_handler = qInstallMessageHandler(_handler)
