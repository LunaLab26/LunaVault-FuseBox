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
    ff = bindir / "ffmpeg"
    fp = bindir / "ffprobe"
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
