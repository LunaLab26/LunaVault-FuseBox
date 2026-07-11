"""Tests for log_manager.py — entry rendering + auto-save-on-failure.
Runs under pytest and standalone."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import log_manager as lm


def _merge_entry(success=True, message="ok"):
    return {
        "timestamp": "2026-07-04T12:00:00",
        "type": "merge",
        "output": r"C:\out\master.mov",
        "source_folder": r"C:\clips",
        "track_order": "camera",
        "mix": {"mix_enabled": True, "kind": "lr", "match_levels": True, "include_video": True},
        "clip_count": 2,
        "total_duration_secs": 65.0,
        "clips": [
            {"name": "a.mp4", "has_wav": True, "audio_offset_ms": 12.3,
             "arrangement": {"video": "copy", "is_slowmo": False,
                             "tracks": [{"label": "Camera", "codec": "aac", "lossless": False, "role": "primary"}],
                             "decisions": ["conformed to baseline"]},
             "drift_ms_per_min": 0.4, "confidence_ms": 2.1, "polarity_inverted": False},
        ],
        "file_size_mb": 512.0,
        "success": success,
        "message": message,
    }


def _whatsapp_entry(success=True):
    return {
        "timestamp": "2026-07-04T12:05:00", "type": "whatsapp", "output": r"C:\out\clip.mp4",
        "source": r"C:\out\master.mov", "start": "00:00:10", "duration": "00:01:00",
        "grade": "None", "file_size_mb": 12.0, "success": success,
        "message": "ok" if success else "ffmpeg exit 1",
    }


def test_render_entry_text_merge_includes_key_fields():
    text = lm.render_entry_text(_merge_entry())
    assert "MERGE EXPORT" in text
    assert r"C:\out\master.mov" in text
    assert "Clips   : 2" in text
    assert "a.mp4" in text
    assert "offset +12.3 ms" in text
    assert "Mix     : on" in text


def test_render_entry_text_whatsapp_includes_grade_and_duration():
    text = lm.render_entry_text(_whatsapp_entry())
    assert "WHATSAPP EXPORT" in text
    assert "Grade   : None" in text
    assert "Duration: 00:01:00" in text


def test_render_entry_text_failed_shows_message():
    text = lm.render_entry_text(_merge_entry(success=False, message="ffmpeg failed on a.mp4"))
    assert "FAILED — ffmpeg failed on a.mp4" in text


def test_write_failure_txt_creates_real_file_with_rendered_content():
    d = lm.get_app_dir() / lm._FAILURE_LOG_DIR
    before = set(d.glob("*.txt")) if d.exists() else set()
    entry = _merge_entry(success=False, message="disk full")
    out = lm._write_failure_txt(entry)
    try:
        assert out is not None and out.exists()
        assert out.parent == d
        content = out.read_text(encoding="utf-8")
        assert "disk full" in content and "MERGE EXPORT" in content
        after = set(d.glob("*.txt"))
        assert out in after - before
    finally:
        if out is not None and out.exists():
            out.unlink()


def _with_patched(obj, name, value, body):
    real = getattr(obj, name)
    setattr(obj, name, value)
    try:
        body()
    finally:
        setattr(obj, name, real)


def test_append_writes_failure_txt_only_when_success_false_and_enabled():
    calls = []
    def fake_write(entry):
        calls.append(entry)
        return None

    with tempfile.TemporaryDirectory() as td:
        tmp_log = Path(td) / "export_log.json"

        def body_enabled():
            def body_write_patched():
                lm._append(_merge_entry(success=True))
                assert calls == []              # success -> never auto-saved
                lm._append(_merge_entry(success=False, message="boom"))
                assert len(calls) == 1          # failure + enabled -> auto-saved
            _with_patched(lm, "_write_failure_txt", fake_write, body_write_patched)

        def body_disabled():
            def body_write_patched():
                lm._append(_merge_entry(success=False, message="boom"))
                assert calls == []              # failure but disabled -> not auto-saved
            _with_patched(lm, "_write_failure_txt", fake_write, body_write_patched)

        def run_with_log_path(path_fn, auto_save_enabled):
            def body():
                _with_patched(lm, "_auto_save_enabled", lambda: auto_save_enabled, path_fn)
            _with_patched(lm, "_log_path", lambda: tmp_log, body)

        run_with_log_path(body_enabled, True)
        calls.clear()
        run_with_log_path(body_disabled, False)


class _ExplodingClip:
    """A clip whose .duration raises — simulates whatever real-world clip
    attribute bug once let a merge failure vanish with no log at all (see
    log_manager.log_merge's docstring)."""
    name = "broken.mp4"

    @property
    def duration(self):
        raise AttributeError("simulated broken clip attribute")


def test_log_merge_failure_writes_entry_even_when_clip_enrichment_throws():
    # A real merge failure must always leave SOME record — even if building
    # the rich per-clip breakdown blows up partway through (confirmed as a
    # real gap: this used to raise straight out of log_merge before _append()
    # ever ran, so a genuine failure left nothing in export_log.json or
    # failure_logs\ at all).
    calls = []
    with tempfile.TemporaryDirectory() as td:
        tmp_log = Path(td) / "export_log.json"

        def body():
            lm.log_merge(
                source_folder=r"C:\clips", output=r"C:\out\master.mov",
                clips=[_ExplodingClip()], track_order="camera",
                success=False, message="ffmpeg failed on broken.mp4",
            )
            assert len(calls) == 1
            entry = calls[0]
            assert entry["success"] is False
            assert "ffmpeg failed on broken.mp4" in entry["message"]
            assert "log enrichment failed" in entry["message"]

        def fake_append(entry):
            calls.append(entry)

        _with_patched(lm, "_log_path", lambda: tmp_log,
                     lambda: _with_patched(lm, "_append", fake_append, body))


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print("ok:", name)
    print("test_log_manager: all tests passed")
