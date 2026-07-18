"""extract_workers.py — background QThread workers for the Extract tab.

Follows the same thread-lifetime discipline as review_workers.py: a
cancellation flag checked between clips, the running subprocess tracked so
cancel() can terminate it early, and the owner keeps a reference until
finished/settled — never drop the last reference to a live QThread.
"""

import subprocess
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThread, Signal

from core.binaries import no_window
from core.eta import ConservativeEta
from core.extract import (
    build_recovery_plan, build_recover_clip_cmd, build_recover_wav_cmd,
    build_recover_camera_audio_cmd, is_mp4_compatible_audio, recover_metadata_args,
    recovered_filenames, build_generic_recovery_plans, build_generic_recover_clip_cmd,
    generic_recovered_filename, build_recover_wav_archival_cmd, build_generic_recover_wav_cmd,
    build_recover_lrv_archival_cmd,
)
from core.manifest import Manifest, read_manifest
from probe import probe_audio_tracks, probe_video_tracks, probe_chapters_safe

# 48kHz/24-bit ("pcm_s24le", the default bit_depth every WAV-decode command
# builder here uses)/stereo — this app's own WAV-backup convention. Used to
# estimate a decoded WAV/camera-audio-split output's byte size, since (unlike
# the video recovery, which is a straight stream copy of ClipEntry.size_bytes)
# a decode target has never been materialised as its own file before now, so
# there's no stored size to read.
_WAV_BYTES_PER_SEC = 48000 * 3 * 2


class ManifestLoadWorker(QThread):
    """Read a master's manifest (embedded or sidecar) off the UI thread —
    the embedded path shells out to ffprobe, which can be slow on a
    cloud-synced file. Also probes chapters + audio/video tracks
    unconditionally (cheap — one more ffprobe call) so the caller can
    immediately fall back to chapter-based recovery (see
    core.extract.build_generic_recovery_plans), and offer the Extract tab's
    manual audio-role/video-stream controls for a foreign (no-manifest)
    master, without a second round-trip."""
    # Manifest|None, list[ChapterInfo], list[AudioTrackInfo], list[VideoTrackInfo],
    # chapters_error: str|None — set when the chapter probe itself failed
    # (e.g. a crashed/broken ffprobe), distinct from a clean probe that found
    # zero chapters.
    manifest_ready = Signal(object, list, list, list, object)

    def __init__(self, ffprobe_bin: str, master_path: str, parent=None):
        super().__init__(parent)
        self._ffprobe = ffprobe_bin
        self._path = str(master_path)

    def run(self):
        m = read_manifest(self._ffprobe, self._path)
        chapters, chapters_error = probe_chapters_safe(self._ffprobe, self._path)
        audio_tracks = probe_audio_tracks(self._ffprobe, self._path)
        video_tracks = probe_video_tracks(self._ffprobe, self._path)
        self.manifest_ready.emit(m, chapters, audio_tracks, video_tracks, chapters_error)


class ExtractWorker(QThread):
    """Recover a batch of clips (video + camera audio + WAV backup, per the
    manifest's RecoveryPlan) into an output folder. One worker handles the
    whole batch so cancel() and progress reporting are simple; each clip is
    independent stream-copy/decode work, not a rendering pipeline."""
    progress   = Signal(int, int, str)   # done_count, total_count, current clip name
    # {produced_bytes, expected_total_bytes, pct, rate_bps, elapsed_secs,
    #  eta_secs, total_secs} — see core.eta.ConservativeEta.estimate.
    bytes_progress = Signal(dict)
    clip_done  = Signal(str, list)       # source_filename, list of recovered output paths
    clip_error = Signal(str, str)        # source_filename, message
    finished_all = Signal(bool)          # True unless cancelled

    def __init__(self, ffmpeg_bin: str, master_path: str, manifest: Manifest,
                entries: list, out_dir: Path, container: str = "native", parent=None):
        super().__init__(parent)
        self._ffmpeg = ffmpeg_bin
        self._master_path = str(master_path)
        self._manifest = manifest
        self._entries = list(entries)     # ClipEntry objects to recover, in order
        self._out_dir = Path(out_dir)
        self._container = container       # "native" | "mov" | "mp4"
        self._cancelled = False
        self._proc: Optional[subprocess.Popen] = None
        self._eta = ConservativeEta()
        self._expected_total_bytes = 1
        self._produced_bytes_base = 0

    def cancel(self):
        self._cancelled = True
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass

    def _estimate_expected_total_bytes(self) -> int:
        """Sum of each selected clip's expected recovered-file size(s) — the
        dominant term is the video(+camera-audio) recovery, a straight
        stream copy of the original's own bytes (ClipEntry.size_bytes,
        already stored in the manifest); WAV-backup/camera-audio-split
        decodes are estimated from duration at _WAV_BYTES_PER_SEC."""
        total = 0
        for entry in self._entries:
            total += max(0, entry.size_bytes)
            plan = build_recovery_plan(self._manifest, entry)
            if plan is None:
                continue
            _, wav_name = recovered_filenames(entry, self._container)
            if wav_name and plan.wav_stream is not None:
                total += int(max(0.0, plan.wav_duration) * _WAV_BYTES_PER_SEC)
            split_camera_audio = (self._container == "mp4" and plan.audio_stream is not None
                                  and not is_mp4_compatible_audio(entry.original_audio_codec))
            if split_camera_audio:
                total += int(max(0.0, plan.video_duration) * _WAV_BYTES_PER_SEC)
        return max(1, total)

    def _run_cmd(self, cmd: list, out_path: Optional[Path] = None) -> bool:
        """Run one ffmpeg command. When `out_path` is given, polls its
        growing size on disk as a live progress proxy instead of a blocking
        wait — these are all stream copies or constant-bitrate PCM decodes,
        so file size tracks completion closely without needing to thread a
        -progress file through every command builder here. stderr goes to
        DEVNULL (never read/surfaced by the old blocking version either) —
        a PIPE that's polled instead of drained via communicate() can
        deadlock the child once its buffer fills."""
        if self._cancelled:
            return False
        try:
            self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL, **no_window())
        except Exception:
            self._proc = None
            return False
        while self._proc.poll() is None:
            if self._cancelled:
                try:
                    self._proc.terminate()
                except Exception:
                    pass
                break
            if out_path is not None:
                try:
                    size = out_path.stat().st_size
                except Exception:
                    size = 0
                produced = self._produced_bytes_base + size
                est = self._eta.estimate(produced, self._expected_total_bytes)
                self.bytes_progress.emit({
                    "produced_bytes": produced,
                    "expected_total_bytes": self._expected_total_bytes,
                    **est,
                })
            time.sleep(0.3)
        rc = self._proc.wait()
        self._proc = None
        ok = rc == 0 and not self._cancelled
        if ok and out_path is not None:
            try:
                self._produced_bytes_base += out_path.stat().st_size
            except Exception:
                pass
        return ok

    def run(self):
        self._out_dir.mkdir(parents=True, exist_ok=True)
        total = len(self._entries)
        self._expected_total_bytes = self._estimate_expected_total_bytes()
        for i, entry in enumerate(self._entries):
            if self._cancelled:
                self.finished_all.emit(False)
                return
            self.progress.emit(i, total, entry.source_filename)
            plan = build_recovery_plan(self._manifest, entry)
            if plan is None:
                self.clip_error.emit(entry.source_filename, "couldn't locate this clip in the manifest")
                continue

            video_name, wav_name = recovered_filenames(entry, self._container)
            out_video = self._out_dir / video_name
            recovered = []
            # MP4 can't hold every camera-audio codec (e.g. uncompressed PCM from some
            # action cameras) — when targeting MP4, split an incompatible track out to
            # its own WAV instead of failing/silently dropping it.
            split_camera_audio = (self._container == "mp4" and plan.audio_stream is not None
                                  and not is_mp4_compatible_audio(entry.original_audio_codec))
            cmd = build_recover_clip_cmd(self._ffmpeg, self._master_path, plan, str(out_video),
                                        include_audio=not split_camera_audio)
            # Re-attach this clip's own GPS/creation-time/device provenance from the
            # manifest — that metadata lives at the whole-FILE level in MOV/MP4, so it
            # was never a property of the copied stream itself (see recover_metadata_args).
            meta_args = recover_metadata_args(entry)
            if meta_args:
                cmd = cmd[:-1] + meta_args + cmd[-1:]
            if self._run_cmd(cmd, out_video) and out_video.exists():
                recovered.append(out_video)
            else:
                if self._cancelled:
                    self.finished_all.emit(False)
                    return
                self.clip_error.emit(entry.source_filename, "video/audio recovery failed")
                continue

            if split_camera_audio:
                out_cam_audio = self._out_dir / f"{Path(entry.source_filename).stem} (camera audio).wav"
                cam_audio_cmd = build_recover_camera_audio_cmd(self._ffmpeg, self._master_path,
                                                               plan, str(out_cam_audio))
                if self._run_cmd(cam_audio_cmd, out_cam_audio) and out_cam_audio.exists():
                    recovered.append(out_cam_audio)
                elif self._cancelled:
                    self.finished_all.emit(False)
                    return
                # a failed camera-audio split isn't fatal — the video already landed

            if wav_name and plan.wav_stream is not None:
                out_wav = self._out_dir / wav_name
                wav_cmd = build_recover_wav_cmd(self._ffmpeg, self._master_path, plan, str(out_wav))
                if self._run_cmd(wav_cmd, out_wav) and out_wav.exists():
                    recovered.append(out_wav)
                elif self._cancelled:
                    self.finished_all.emit(False)
                    return
                # a failed WAV recovery isn't fatal to the clip — the video/audio already landed

            if plan.wav_archival_stream is not None:
                # This clip's WAV-mismatch resolution ticked "Also preserve this WAV in
                # full" — a plain stream copy of its own dedicated, untrimmed track.
                out_wav_full = self._out_dir / f"{Path(entry.source_filename).stem} (WAV - preserved original).wav"
                wav_full_cmd = build_recover_wav_archival_cmd(self._ffmpeg, self._master_path,
                                                              plan, str(out_wav_full))
                if self._run_cmd(wav_full_cmd, out_wav_full) and out_wav_full.exists():
                    recovered.append(out_wav_full)
                elif self._cancelled:
                    self.finished_all.emit(False)
                    return
                # a failed preserved-WAV recovery isn't fatal to the clip either

            if plan.lrv_video_archival_track is not None:
                # This clip's video options ticked "Also preserve the LRV proxy
                # on its own track" — a plain stream copy of its dedicated tracks.
                out_lrv = self._out_dir / f"{Path(entry.source_filename).stem} (LRV proxy).mov"
                lrv_cmd = build_recover_lrv_archival_cmd(self._ffmpeg, self._master_path,
                                                         plan, str(out_lrv))
                if self._run_cmd(lrv_cmd, out_lrv) and out_lrv.exists():
                    recovered.append(out_lrv)
                elif self._cancelled:
                    self.finished_all.emit(False)
                    return
                # a failed preserved-LRV recovery isn't fatal to the clip either

            self.clip_done.emit(entry.source_filename, recovered)

        self.progress.emit(total, total, "")
        self.finished_all.emit(True)


class GenericExtractWorker(QThread):
    """The no-manifest counterpart to ExtractWorker: recover a batch of
    GenericRecoveryPlans (chapter-based trims of the master's own baseline
    video/audio, no archival-track/rotation awareness) into an output
    folder. Same shape/lifetime discipline as ExtractWorker."""
    progress   = Signal(int, int, str)   # done_count, total_count, current clip name
    bytes_progress = Signal(dict)        # see ExtractWorker.bytes_progress
    clip_done  = Signal(str, list)       # recovered filename, list of recovered output paths
    clip_error = Signal(str, str)        # recovered filename, message
    finished_all = Signal(bool)          # True unless cancelled

    def __init__(self, ffmpeg_bin: str, master_path: str, plans: list, out_dir: Path,
                container: str = "native", parent=None):
        super().__init__(parent)
        self._ffmpeg = ffmpeg_bin
        self._master_path = str(master_path)
        self._plans = list(plans)
        self._out_dir = Path(out_dir)
        self._container = container       # "native" | "mov" | "mp4"
        self._cancelled = False
        self._proc: Optional[subprocess.Popen] = None
        self._eta = ConservativeEta()
        self._expected_total_bytes = 1
        self._produced_bytes_base = 0

    def cancel(self):
        self._cancelled = True
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass

    def _estimate_expected_total_bytes(self) -> int:
        """No manifest means no per-clip byte size is recorded anywhere —
        estimate each plan's share proportionally to its duration against
        the MASTER's own total duration and file size (this app's own
        masters run near-constant average bitrate across their length; a
        foreign master may vary more, but this still beats no estimate)."""
        try:
            master_size = Path(self._master_path).stat().st_size
        except Exception:
            return max(1, len(self._plans))
        total_duration = sum(max(0.01, p.duration) for p in self._plans)
        if total_duration <= 0:
            return max(1, master_size)
        total = 0
        for p in self._plans:
            total += int(master_size * (max(0.01, p.duration) / total_duration))
            if p.wav_stream is not None:
                total += int(max(0.0, p.duration) * _WAV_BYTES_PER_SEC)
        return max(1, total)

    def _run_cmd(self, cmd: list, out_path: Optional[Path] = None) -> bool:
        """See ExtractWorker._run_cmd — identical polling/byte-tracking approach."""
        if self._cancelled:
            return False
        try:
            self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL, **no_window())
        except Exception:
            self._proc = None
            return False
        while self._proc.poll() is None:
            if self._cancelled:
                try:
                    self._proc.terminate()
                except Exception:
                    pass
                break
            if out_path is not None:
                try:
                    size = out_path.stat().st_size
                except Exception:
                    size = 0
                produced = self._produced_bytes_base + size
                est = self._eta.estimate(produced, self._expected_total_bytes)
                self.bytes_progress.emit({
                    "produced_bytes": produced,
                    "expected_total_bytes": self._expected_total_bytes,
                    **est,
                })
            time.sleep(0.3)
        rc = self._proc.wait()
        self._proc = None
        ok = rc == 0 and not self._cancelled
        if ok and out_path is not None:
            try:
                self._produced_bytes_base += out_path.stat().st_size
            except Exception:
                pass
        return ok

    def run(self):
        self._out_dir.mkdir(parents=True, exist_ok=True)
        total = len(self._plans)
        self._expected_total_bytes = self._estimate_expected_total_bytes()
        for i, plan in enumerate(self._plans):
            if self._cancelled:
                self.finished_all.emit(False)
                return
            name = generic_recovered_filename(plan, self._container)
            self.progress.emit(i, total, name)
            out_video = self._out_dir / name
            cmd = build_generic_recover_clip_cmd(self._ffmpeg, self._master_path, plan, str(out_video))
            if not (self._run_cmd(cmd, out_video) and out_video.exists()):
                if self._cancelled:
                    self.finished_all.emit(False)
                    return
                self.clip_error.emit(name, "recovery failed")
                continue

            recovered = [out_video]
            if plan.wav_stream is not None:
                # Manually assigned in the Extract tab's audio-role table — a
                # second audio track this master carries that isn't the camera
                # audio (no manifest means there's no automatic way to know
                # this exists, so it's only ever present as a deliberate
                # user choice).
                out_wav = self._out_dir / f"{Path(name).stem}.wav"
                wav_cmd = build_generic_recover_wav_cmd(self._ffmpeg, self._master_path,
                                                        plan, str(out_wav))
                if self._run_cmd(wav_cmd, out_wav) and out_wav.exists():
                    recovered.append(out_wav)
                elif self._cancelled:
                    self.finished_all.emit(False)
                    return
                # a failed WAV-role recovery isn't fatal — the video already landed

            self.clip_done.emit(name, recovered)

        self.progress.emit(total, total, "")
        self.finished_all.emit(True)
