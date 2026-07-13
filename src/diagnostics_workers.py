"""diagnostics_workers.py — background QThread running pre-flight diagnostic
checks (core/diagnostics.py) over a batch of clips, one clip at a time,
cancellable mid-check (the slow decode scans especially need this — see
core/diagnostics.py's module docstring for the investigation this
formalizes)."""

import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThread, Signal

from core.binaries import no_window
from core.verify import probe_video_codec
from core import diagnostics as diag


class DiagnosticsWorker(QThread):
    # clip_index (position in the clips list passed in), DiagnosticResult
    result_ready = Signal(int, object)
    progress     = Signal(int, int, str)   # done_count, total_count, current clip name
    finished_all = Signal(bool)            # True unless cancelled

    def __init__(self, ffmpeg_bin: str, ffprobe_bin: str, clips: list, check_ids: list, parent=None):
        super().__init__(parent)
        self._ff = ffmpeg_bin
        self._fp = ffprobe_bin
        self._clips = list(clips)          # ClipInfo objects: .path, .duration, .stem
        self._check_ids = set(check_ids)
        self._cancelled = False
        self._proc: Optional[subprocess.Popen] = None

    def cancel(self):
        self._cancelled = True
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass

    def _run(self, cmd: list, timeout: Optional[float] = None) -> tuple:
        """(returncode, stdout, stderr) via Popen (not run) so cancel() can
        terminate a slow decode scan instead of blocking the whole batch."""
        if self._cancelled:
            return (-1, "", "cancelled")
        try:
            self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                          text=True, errors="ignore", **no_window())
            out, err = self._proc.communicate(timeout=timeout)
            rc = self._proc.returncode
        except subprocess.TimeoutExpired:
            self._proc.kill()
            out, err = self._proc.communicate()
            rc = -1
        except Exception as e:
            return (-1, "", str(e))
        finally:
            self._proc = None
        return (rc, out or "", err or "")

    def _run_container(self, i: int, path: str):
        rc, out, err = self._run(diag.build_container_probe_cmd(self._fp, path), timeout=30)
        self.result_ready.emit(i, diag.parse_container_result(rc, out, err))

    def _run_timestamps(self, i: int, path: str):
        rc, out, err = self._run(diag.build_timestamp_probe_cmd(self._fp, path), timeout=120)
        self.result_ready.emit(i, diag.parse_timestamp_result(rc, out, err))

    def _run_streamcopy(self, i: int, path: str, duration: float):
        with tempfile.TemporaryDirectory() as td:
            sample_secs = min(10.0, duration) if duration > 0 else 5.0
            out_copy = Path(td) / "sample.mov"
            rc, _, err_copy = self._run(
                diag.build_streamcopy_test_cmd(self._ff, path, str(out_copy), sample_secs), timeout=60)
            copy_ok = rc == 0
            annexb_ok = None
            err_annexb = ""
            if copy_ok and not self._cancelled:
                codec = probe_video_codec(self._fp, path, **no_window())
                annexb_cmd = diag.build_annexb_test_cmd(self._ff, path, str(Path(td) / "sample.ts"),
                                                        sample_secs, codec)
                if annexb_cmd is not None:
                    rc2, _, err_annexb = self._run(annexb_cmd, timeout=60)
                    annexb_ok = rc2 == 0
        self.result_ready.emit(i, diag.parse_streamcopy_result(copy_ok, err_copy, annexb_ok, err_annexb))

    def _run_quickdecode(self, i: int, path: str, duration: float):
        results = []
        for start, length in diag.sample_windows(duration):
            if self._cancelled:
                return
            rc, _, err = self._run(diag.build_decode_scan_cmd(self._ff, path, start, length), timeout=120)
            results.append((start, err))
        self.result_ready.emit(i, diag.parse_decode_scan_results("quickdecode", results))

    def _run_fulldecode(self, i: int, path: str):
        rc, _, err = self._run(diag.build_decode_scan_cmd(self._ff, path), timeout=3600)
        if not self._cancelled:
            self.result_ready.emit(i, diag.parse_decode_scan_results("fulldecode", [(None, err)]))

    def run(self):
        total = len(self._clips)
        # Run in ascending cost order so a cancel mid-batch has already
        # delivered the cheap, informative results for every clip reached
        # so far, rather than getting stuck deep in one clip's slow scan.
        steps = [
            ("container",   self._run_container,   False),
            ("timestamps",  self._run_timestamps,  False),
            ("streamcopy",  self._run_streamcopy,  True),
            ("quickdecode", self._run_quickdecode, True),
            ("fulldecode",  self._run_fulldecode,  False),
        ]
        for i, clip in enumerate(self._clips):
            if self._cancelled:
                self.finished_all.emit(False)
                return
            self.progress.emit(i, total, clip.stem)
            path = str(clip.path)
            duration = getattr(clip, "duration", 0.0) or 0.0
            for check_id, fn, needs_duration in steps:
                if check_id not in self._check_ids:
                    continue
                if self._cancelled:
                    self.finished_all.emit(False)
                    return
                if needs_duration:
                    fn(i, path, duration)
                else:
                    fn(i, path)
        if self._cancelled:
            self.finished_all.emit(False)
            return
        self.progress.emit(total, total, "")
        self.finished_all.emit(True)
