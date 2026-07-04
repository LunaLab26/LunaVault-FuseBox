"""extract_workers.py — background QThread workers for the Extract tab.

Follows the same thread-lifetime discipline as review_workers.py: a
cancellation flag checked between clips, the running subprocess tracked so
cancel() can terminate it early, and the owner keeps a reference until
finished/settled — never drop the last reference to a live QThread.
"""

import subprocess
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThread, Signal

from core.binaries import no_window
from core.extract import (
    build_recovery_plan, build_recover_clip_cmd, build_recover_wav_cmd,
    recovered_filenames,
)
from core.manifest import Manifest, read_manifest


class ManifestLoadWorker(QThread):
    """Read a master's manifest (embedded or sidecar) off the UI thread —
    the embedded path shells out to ffprobe, which can be slow on a
    cloud-synced file."""
    manifest_ready = Signal(object)   # Manifest, or None if not found/unreadable

    def __init__(self, ffprobe_bin: str, master_path: str, parent=None):
        super().__init__(parent)
        self._ffprobe = ffprobe_bin
        self._path = str(master_path)

    def run(self):
        m = read_manifest(self._ffprobe, self._path)
        self.manifest_ready.emit(m)


class ExtractWorker(QThread):
    """Recover a batch of clips (video + camera audio + WAV backup, per the
    manifest's RecoveryPlan) into an output folder. One worker handles the
    whole batch so cancel() and progress reporting are simple; each clip is
    independent stream-copy/decode work, not a rendering pipeline."""
    progress   = Signal(int, int, str)   # done_count, total_count, current clip name
    clip_done  = Signal(str, list)       # source_filename, list of recovered output paths
    clip_error = Signal(str, str)        # source_filename, message
    finished_all = Signal(bool)          # True unless cancelled

    def __init__(self, ffmpeg_bin: str, master_path: str, manifest: Manifest,
                entries: list, out_dir: Path, parent=None):
        super().__init__(parent)
        self._ffmpeg = ffmpeg_bin
        self._master_path = str(master_path)
        self._manifest = manifest
        self._entries = list(entries)     # ClipEntry objects to recover, in order
        self._out_dir = Path(out_dir)
        self._cancelled = False
        self._proc: Optional[subprocess.Popen] = None

    def cancel(self):
        self._cancelled = True
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass

    def _run_cmd(self, cmd: list) -> bool:
        if self._cancelled:
            return False
        try:
            self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.PIPE, **no_window())
            _, stderr = self._proc.communicate()
        except Exception:
            self._proc = None
            return False
        rc = self._proc.returncode
        self._proc = None
        return rc == 0 and not self._cancelled

    def run(self):
        self._out_dir.mkdir(parents=True, exist_ok=True)
        total = len(self._entries)
        for i, entry in enumerate(self._entries):
            if self._cancelled:
                self.finished_all.emit(False)
                return
            self.progress.emit(i, total, entry.source_filename)
            plan = build_recovery_plan(self._manifest, entry)
            if plan is None:
                self.clip_error.emit(entry.source_filename, "couldn't locate this clip in the manifest")
                continue

            video_name, wav_name = recovered_filenames(entry)
            out_video = self._out_dir / video_name
            recovered = []
            cmd = build_recover_clip_cmd(self._ffmpeg, self._master_path, plan, str(out_video))
            if self._run_cmd(cmd) and out_video.exists():
                recovered.append(out_video)
            else:
                if self._cancelled:
                    self.finished_all.emit(False)
                    return
                self.clip_error.emit(entry.source_filename, "video/audio recovery failed")
                continue

            if wav_name and plan.wav_stream is not None:
                out_wav = self._out_dir / wav_name
                wav_cmd = build_recover_wav_cmd(self._ffmpeg, self._master_path, plan, str(out_wav))
                if self._run_cmd(wav_cmd) and out_wav.exists():
                    recovered.append(out_wav)
                elif self._cancelled:
                    self.finished_all.emit(False)
                    return
                # a failed WAV recovery isn't fatal to the clip — the video/audio already landed

            self.clip_done.emit(entry.source_filename, recovered)

        self.progress.emit(total, total, "")
        self.finished_all.emit(True)
