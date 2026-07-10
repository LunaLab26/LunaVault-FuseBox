"""Tests for core/cloudsync.py — provider-agnostic cloud-folder detection.

Standalone-runnable (`python tests/test_cloudsync.py`), same pattern as the rest.
"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from core import cloudsync  # noqa: E402


def test_detect_provider_from_path_names():
    assert cloudsync.detect_provider(r"C:/Users/me/Jottacloud/Memories/Pool day") == "jottacloud"
    assert cloudsync.detect_provider(r"C:/Users/me/Dropbox/Memories") == "dropbox"
    assert cloudsync.detect_provider(r"C:/Users/me/OneDrive - Personal/Vids") == "onedrive"
    assert cloudsync.detect_provider(r"C:/Users/me/Google Drive/x") == "googledrive"
    assert cloudsync.detect_provider(r"C:/Users/me/iCloudDrive/x") == "icloud"
    assert cloudsync.detect_provider(r"D:/Photos/Memories/Pool day") is None
    print("ok: test_detect_provider_from_path_names")


def test_is_cloud_backed_uses_provider():
    assert cloudsync.is_cloud_backed(r"C:/Users/me/Jottacloud/Memories") is True
    # A plainly-local path with no placeholder attrs → not cloud.
    assert cloudsync.is_cloud_backed(str(Path(__file__).resolve().parent)) is False
    print("ok: test_is_cloud_backed_uses_provider")


def test_placeholder_attributes_safe_off_windows():
    # Must never raise, whatever the platform / path.
    cloudsync.has_placeholder_attributes(str(Path(__file__).resolve().parent))
    cloudsync.has_placeholder_attributes("/nonexistent/path/xyz")
    print("ok: test_placeholder_attributes_safe_off_windows")


if __name__ == "__main__":
    test_detect_provider_from_path_names()
    test_is_cloud_backed_uses_provider()
    test_placeholder_attributes_safe_off_windows()
    print("test_cloudsync: all tests passed")
