"""thread_utils.py — QThread lifetime helpers.

Qt hard-aborts the whole process if a QThread object is destroyed while its
OS thread is still running. House rule: never drop the last Python reference
to a QThread that hasn't been wait()ed, and settle every worker on close.
"""

from typing import Optional

from PySide6.QtCore import QThread


def settle(thread: Optional[QThread], timeout_ms: int = 5000) -> bool:
    """Block until `thread` has finished. No-op (True) for None.

    Returns whether the thread actually finished — callers that track live
    workers in a list must check this before dropping the reference on a
    timeout, or they've reintroduced the same "destroy a running QThread"
    crash this helper exists to prevent.
    """
    if thread is None:
        return True
    try:
        return thread.wait(timeout_ms)
    except RuntimeError:
        return True   # underlying C++ object already destroyed
