"""tests/test_binaries.py — core.binaries.get_ffmpeg()'s bundled-binary lookup."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core import binaries


def _make_bin(tmp_path, executable=True):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    suffix = ".exe" if sys.platform == "win32" else ""   # get_ffmpeg looks for ffmpeg.exe on Windows
    ff = bindir / f"ffmpeg{suffix}"
    fp = bindir / f"ffprobe{suffix}"
    ff.write_text("#!/bin/sh\n")
    fp.write_text("#!/bin/sh\n")
    if executable:
        ff.chmod(0o755)
        fp.chmod(0o755)
    else:
        ff.chmod(0o644)
        fp.chmod(0o644)
    return ff, fp


def test_get_ffmpeg_returns_bundled_paths_when_present(tmp_path, monkeypatch):
    ff, fp = _make_bin(tmp_path, executable=False)
    monkeypatch.setattr(binaries, "get_app_dir", lambda: tmp_path)
    out_ff, out_fp = binaries.get_ffmpeg()
    assert out_ff == str(ff)
    assert out_fp == str(fp)
    assert os.access(ff, os.X_OK)
    assert os.access(fp, os.X_OK)


def test_get_ffmpeg_skips_chmod_when_already_executable(tmp_path, monkeypatch):
    """Real bug found on Steam Deck: bin/ffmpeg pointed at a root-owned system
    binary (a user workaround for a bundled binary that couldn't run there) —
    an unconditional chmod() on a path you don't own raises PermissionError,
    crashing the whole app before any window shows. Already-executable means
    no chmod call is even attempted."""
    ff, fp = _make_bin(tmp_path, executable=True)
    monkeypatch.setattr(binaries, "get_app_dir", lambda: tmp_path)

    def _boom(*a, **k):
        raise PermissionError("Operation not permitted")
    monkeypatch.setattr(Path, "chmod", _boom)

    out_ff, out_fp = binaries.get_ffmpeg()
    assert out_ff == str(ff)
    assert out_fp == str(fp)


def test_get_ffmpeg_survives_unchmoddable_binary(tmp_path, monkeypatch):
    """Same real bug, direct case: the binary is NOT already executable (a
    fresh unzip that lost the bit) AND chmod fails (root-owned symlink target,
    read-only mount, etc.) — get_ffmpeg must not raise; the actual exec
    attempt downstream is the real, loud test of whether it can run."""
    ff, fp = _make_bin(tmp_path, executable=False)
    monkeypatch.setattr(binaries, "get_app_dir", lambda: tmp_path)

    def _boom(*a, **k):
        raise PermissionError("Operation not permitted")
    monkeypatch.setattr(Path, "chmod", _boom)

    out_ff, out_fp = binaries.get_ffmpeg()
    assert out_ff == str(ff)
    assert out_fp == str(fp)


def test_get_ffmpeg_falls_back_when_bundled_binary_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(binaries, "get_app_dir", lambda: tmp_path)
    out_ff, out_fp = binaries.get_ffmpeg()
    assert out_ff == "ffmpeg"
    assert out_fp == "ffprobe"


@pytest.mark.parametrize("text,ok", [
    ("ffmpeg version 7.0.2-static https://johnvansickle.com/ffmpeg/", True),
    ("ffmpeg version 7.1.1-full_build-www.gyan.dev Copyright", True),
    ("ffmpeg version 6.1 Copyright", True),
    ("ffmpeg version 7.1.2 Copyright", False),
    ("ffmpeg version 9.0.2-full_build-www.gyan.dev", False),
    ("ffmpeg version N-120000-gabcdef1234-20260601 Copyright", False),
    ("", False),
])
def test_ffmpeg_join_safety(text, ok):
    assert binaries.ffmpeg_join_safety(text)[0] is ok


def test_check_ffmpeg_script_on_bundled_binary():
    import subprocess
    root = Path(__file__).resolve().parents[1]
    ff = root / "bin" / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    if not ff.exists():
        pytest.skip("no bundled ffmpeg")
    r = subprocess.run([sys.executable, str(root / "tools" / "check_ffmpeg.py"), str(ff)],
                       capture_output=True, text=True)
    assert r.returncode in (0, 1) and ("OK" in r.stdout or "ERROR" in r.stdout)
