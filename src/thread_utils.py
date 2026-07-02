"""thread_utils.py — QThread lifetime helpers.

Qt hard-aborts the whole process if a QThread object is destroyed while its
OS thread is still running. House rule: never drop the last Python reference
to a QThread that hasn't been wait()ed, and settle every worker on close.
"""

from typing import Optional

from PySide6.QtCore import QThread


def settle(thread: Optional[QThread], timeout_ms: int = 5000) -> None:
    """Block until `thread` has finished. No-op for None or a finished thread."""
    if thread is None:
        return
    try:
        thread.wait(timeout_ms)
    except RuntimeError:
        pass   # underlying C++ object already destroyed
