"""ffmpeg_runner.py — Qt worker threads over the UI-agnostic core.

The actual command building, binary resolution, progress parsing and sync
analysis now live in the `core` package (pure, Qt-free, unit-tested). This module
keeps only the QThread orchestration: spawning ffmpeg, polling progress, emitting
signals, cancellation and cleanup.
"""

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThread, Signal

from grade_manager import Grade
from probe import probe_duration, pix_fmt_info

from core.binaries import get_app_dir, get_ffmpeg, no_window
from core import manifest as manifest_mod
from core.progress import read_progress, parse_progress
from core.sync_advanced import analyze_sync
from core.ffmpeg_cmd import (
    hms_to_seconds, MixSpec, OutputPlan, SLOWMO_RATIO,
    build_mux_cmd, build_mux_cmd_plan, build_concat_cmd, build_whatsapp_cmd,
    build_preview_cmd, build_thumbnail_cmd,
    build_archival_concat_cmd, build_final_archival_mux_cmd,
)

# Re-exported for existing call sites (main.py, merge_tab.py, whatsapp_tab.py).
_no_window = no_window


def _tail_text(path: Path, max_chars: int = 600) -> str:
    """Last meaningful lines of an ffmpeg stderr log, for error messages."""
    try:
        raw = path.read_text(errors="ignore")
    except Exception:
        return ""
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    tail = "\n".join(lines[-6:])
    return tail[-max_chars:]
__all__ = [
    "get_app_dir", "get_ffmpeg", "read_progress", "parse_progress",
    "ThumbnailThread", "MergeWorker", "WhatsAppWorker", "FramePreviewWorker",
]


# ── Thumbnail extractor thread ────────────────────────────────────────────────

class ThumbnailThread(QThread):
    frame_ready = Signal(str)

    def __init__(self, ffmpeg_bin: str, source: str, progress_file: Path,
                 temp_dir: Path, grade: Optional[Grade] = None):
        super().__init__()
        self._ffmpeg   = ffmpeg_bin
        self._source   = source
        self._progress = progress_file
        self._temp     = temp_dir
        self._grade    = grade
        self._running  = True
        self._thumb    = str(temp_dir / "thumb.jpg")

    def stop(self):
        self._running = False

    def run(self):
        time.sleep(1.5)
        last_ts = 0.0
        while self._running:
            data = read_progress(self._progress)
            try:
                ts = int(data.get("out_time_us", 0) or 0) / 1e6
            except Exception:
                ts = 0.0
            if ts > 0.5 and abs(ts - last_ts) > 0.8:
                last_ts = ts
                self._extract(ts)
            time.sleep(1.0)

    def _extract(self, ts: float):
        cmd = build_thumbnail_cmd(self._ffmpeg, self._source, ts, self._grade, self._thumb)
        try:
            subprocess.run(cmd, capture_output=True, timeout=8, **no_window())
            if Path(self._thumb).exists():
                self.frame_ready.emit(self._thumb)
        except Exception:
            pass


def _stop_thumb(thumb: Optional[ThumbnailThread]):
    """Stop and settle a thumbnail thread; its local ref is about to go away,
    and destroying a live QThread aborts the process. 10 s outlasts a stuck
    _extract() (subprocess timeout is 8 s)."""
    if thumb:
        thumb.stop()
        thumb.wait(10000)


# ── Merge worker ──────────────────────────────────────────────────────────────

class MergeWorker(QThread):
    progress  = Signal(dict)
    thumbnail = Signal(str)
    finished  = Signal(bool, str)

    def __init__(self, clips: list, output_path: Path,
                 plan: OutputPlan, square_mode: str, title: str = "",
                 enable_preview: bool = True, scratch_override: str = "",
                 archival: bool = False):
        super().__init__()
        self._clips            = clips
        self._output           = output_path
        self._plan             = plan
        self._square_mode      = square_mode
        self._title            = title
        self._enable_preview   = enable_preview
        self._scratch_override = scratch_override
        self._archival         = archival   # embed odd-spec originals on parallel archival tracks
        self._final_tmp        = None
        self._cancelled        = False

    def _mix_for(self, clip) -> MixSpec:
        """Per-clip MixSpec carrying this clip's drift/polarity from sync."""
        return MixSpec(
            kind=self._plan.mix_kind,
            match_levels=self._plan.mix_match_levels,
            drift_ratio=clip.sync_drift_ratio,
            polarity_inverted=clip.sync_polarity_inverted,
        )

    def cancel(self):
        self._cancelled = True

    def _metrics(self, size: int, pct: float, stage_idx: int, stage_total: int) -> dict:
        """Smoothed write speed (bytes/s) and an overall ETA (seconds)."""
        now = time.time()
        if stage_idx != self._last_stage:
            self._last_stage = stage_idx
            self._last_size = 0
            self._last_t = now
        if self._last_t is not None and now > self._last_t:
            dsize = size - self._last_size
            if dsize >= 0:
                inst = dsize / (now - self._last_t)
                self._rate_bps = inst if not self._rate_bps else 0.6 * self._rate_bps + 0.4 * inst
        self._last_t, self._last_size = now, size
        frac = ((stage_idx - 1) + pct / 100.0) / max(1, stage_total)
        elapsed = now - self._t0
        eta = elapsed * (1 - frac) / frac if frac > 0.02 else 0.0
        return {"rate_bps": self._rate_bps, "eta_secs": eta, "elapsed_secs": elapsed}

    def _make_scratch(self) -> Path:
        """A fast, writable, LOCAL scratch dir for the per-clip temp files.

        Deliberately NOT the output folder — if the output is a slow cloud-synced
        location (e.g. Jottacloud), writing every temp clip there cripples speed.
        Only the finished master is written to the output folder (once).
        """
        candidates = []
        if self._scratch_override:
            candidates.append(Path(self._scratch_override) / "_lvfb_temp")
        candidates.append(get_app_dir() / "_temp")
        candidates.append(Path(tempfile.gettempdir()) / "lunavault_fusebox")
        for base in candidates:
            try:
                base.mkdir(parents=True, exist_ok=True)
                probe = base / ".write_test"
                probe.write_text("ok")
                probe.unlink()
                return base
            except Exception:
                continue
        return get_app_dir() / "_temp"

    # Cap on the embedded-manifest metadata value, well under the Windows
    # ~32 KB command-line limit; a shoot large enough to exceed it falls back to
    # the sidecar (which has no size limit) rather than risk failing the concat.
    _MANIFEST_EMBED_MAX = 24000

    def _build_manifest(self, clips: list):
        """Provenance manifest for the master — one entry per source clip, in
        baseline (chapter) order. Additive: recording where each original came
        from and whether it conformed. Archival-track fields stay unset until
        Phase 2 actually embeds the odd-spec originals."""
        m = manifest_mod.Manifest(master_filename=self._output.name)
        for idx, clip in enumerate(clips):
            st = clip.stream
            codec  = st.codec if st else ""
            width  = st.width if st else 0
            height = st.height if st else 0
            fps    = st.fps_str if st else ""
            pix    = st.pix_fmt if st else ""
            status = st.status if st else "unknown"
            try:
                size_bytes = clip.path.stat().st_size
            except Exception:
                size_bytes = 0
            has_cam = clip.has_camera_audio()
            acodec = (st.audio_codec if st else "") or ""
            # Camera audio is preserved losslessly when it's stream-copied: odd-spec
            # clips carry their original audio on the archival track (any codec), and
            # conforming clips keep AAC camera audio via -c:a copy. A conforming clip
            # with non-AAC camera audio would be a lossy re-encode in the baseline.
            audio_lossless = has_cam and (status != "ok" or acodec.lower() == "aac")
            m.clips.append(manifest_mod.ClipEntry(
                source_filename=clip.path.name,
                container=clip.path.suffix.lstrip(".").lower(),
                codec=codec, width=width, height=height, fps=fps, pix_fmt=pix,
                bit_depth=(pix_fmt_info(pix)[0] if pix else 0),
                duration=clip.duration, size_bytes=size_bytes,
                conform_status=status,
                spec_group=("" if status == "ok"
                            else manifest_mod.spec_signature(codec, width, height, fps, pix)),
                has_camera_audio=has_cam, original_audio_codec=acodec,
                audio_lossless=audio_lossless, has_wav=clip.has_wav(),
                baseline_chapter_index=idx,
            ))
        return m

    def _run_stage(self, cmd, temp_dir, progress_file, label, stage_idx, stage_total,
                   total_dur, thumb=None) -> bool:
        """Run one ffmpeg stage with progress polling + cancel handling. Returns
        True on success; on cancel/failure it cleans up, emits finished(False,…)
        and returns False so the caller can just `return`."""
        progress_file.write_text("")
        err_path = temp_dir / "ffmpeg_err.txt"
        ef = open(err_path, "wb")
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=ef, **no_window())
        while proc.poll() is None:
            if self._cancelled:
                proc.terminate(); ef.close(); _stop_thumb(thumb)
                self._cleanup(temp_dir); self.finished.emit(False, "Cancelled")
                return False
            parsed = parse_progress(read_progress(progress_file), total_dur)
            self.progress.emit({
                "pct": parsed["pct"], "size": parsed["size"],
                "stage": "concat", "stage_label": label,
                "stage_idx": stage_idx, "stage_total": stage_total,
                **self._metrics(parsed["size"], parsed["pct"], stage_idx, stage_total),
            })
            time.sleep(0.4)
        _stop_thumb(thumb)
        proc.wait(); ef.close()
        if proc.returncode != 0:
            tail = _tail_text(err_path); self._cleanup(temp_dir)
            self.finished.emit(False, f"{label} failed (exit {proc.returncode})"
                                      + (f"\n\n{tail}" if tail else ""))
            return False
        return True

    def _build_and_mux_archival(self, ff, clips, manifest, baseline, final_tmp,
                                temp_dir, progress_file, stage_total, total_dur) -> bool:
        """Build per-spec-group archival intermediates from the odd-spec ORIGINALS,
        assign the manifest's archival stream locations, then mux baseline + archival
        tracks into the final master with the (now complete) manifest embedded."""
        enabled = [t.kind for t in self._plan.tracks if t.enabled]
        base_audio_count = len(enabled)
        base_video_count = 1 if self._plan.include_video else 0
        manifest.baseline_audio_tracks = {kind: i for i, kind in enumerate(enabled)}

        # Group odd-spec clips by spec signature, preserving order — each group's
        # ORIGINAL files become one archival track.
        groups: dict = {}   # sig -> list[(clip, entry)]
        for i, clip in enumerate(clips):
            entry = manifest.clips[i]
            if entry.conform_status == "ok":
                continue
            groups.setdefault(entry.spec_group, []).append((clip, entry))

        archival_files = []
        groups_entries = []
        for gi, (sig, pairs) in enumerate(groups.items()):
            if len(pairs) == 1:
                # A lone clip needs no concat — use the original directly. Going
                # through the concat demuxer perturbs AAC priming (audio wouldn't
                # be bit-exact); a direct stream copy in the final mux IS bit-exact.
                archival_files.append(Path(pairs[0][0].path))
            else:
                lst = temp_dir / f"arch_list_{gi}.txt"
                with open(lst, "w", encoding="utf-8") as f:
                    for clip, _ in pairs:
                        safe = str(clip.path.resolve()).replace("\\", "/").replace("'", r"'\''")
                        f.write(f"file '{safe}'\n")
                interm = temp_dir / f"archive_{gi}.mov"
                if not self._run_stage(build_archival_concat_cmd(ff, lst, interm),
                                       temp_dir, progress_file,
                                       f"Archiving originals ({gi + 1}/{len(groups)})",
                                       stage_total, stage_total, total_dur):
                    return False
                archival_files.append(interm)
            groups_entries.append([e for _, e in pairs])

        manifest_mod.assign_archival_locations(groups_entries, base_video_count, base_audio_count)

        embed = manifest_mod.metadata_embed_args(
            manifest, is_mov=str(final_tmp).lower().endswith(".mov"))
        if embed and len(embed[-1]) > self._MANIFEST_EMBED_MAX:
            embed = None
        cmd = build_final_archival_mux_cmd(ff, baseline, archival_files, final_tmp,
                                           progress_file, extra_out_args=embed)
        return self._run_stage(cmd, temp_dir, progress_file, "Finalising archive",
                               stage_total, stage_total, total_dur)

    def run(self):
        ff, fp = get_ffmpeg()
        # Per-clip temp files go on a fast LOCAL scratch dir; only the finished
        # master is written to the output folder, under a temporary name, then
        # renamed into place (atomic when same-volume) so a cloud-sync folder
        # never sees a half-written file.
        temp_dir = self._make_scratch()
        final_tmp = self._output.parent / ("~partial_" + self._output.name)
        self._final_tmp = final_tmp
        try:
            if final_tmp.exists():
                final_tmp.unlink()
        except Exception:
            pass
        progress_file = temp_dir / "progress.txt"
        progress_file.write_text("")

        # live-metrics state
        self._t0 = time.time()
        self._rate_bps = 0.0
        self._last_t = None
        self._last_size = 0
        self._last_stage = -1

        clips       = sorted(self._clips, key=lambda c: c.order_idx)
        stage_total = len(clips) + 1

        temp_clips: list[Path] = []
        cumulative_duration = 0.0

        for i, clip in enumerate(clips):
            if self._cancelled:
                self._cleanup(temp_dir)
                self.finished.emit(False, "Cancelled")
                return

            label = f"Mux {clip.stem}" if clip.status == "ok" else f"Transcode {clip.stem}"
            self.progress.emit({
                "pct": 0, "size": 0,
                "stage": "mux", "stage_label": label,
                "stage_idx": i + 1, "stage_total": stage_total,
            })

            # Probe WAV and analyse sync (GCC-PHAT + drift). The lossless WAV
            # track uses the constant offset only; drift feeds the mix track.
            if clip.has_wav() and not clip.sync_done:
                clip.wav_duration = probe_duration(fp, str(clip.wav_path))
                if clip.wav_duration > 0 and clip.duration > clip.wav_duration * SLOWMO_RATIO:
                    # Slow-motion: WAV is real-time, video is stretched — a
                    # constant offset doesn't apply; we time-stretch instead.
                    clip.wav_offset = 0.0
                    clip.sync_done  = True
                else:
                    res = analyze_sync(ff, str(clip.path), str(clip.wav_path),
                                       clip.duration, clip.wav_duration)
                    clip.wav_offset            = res.constant_offset + clip.manual_nudge_ms / 1000.0
                    clip.sync_drift_ratio      = res.drift_ratio
                    clip.sync_confidence_ms    = res.confidence_ms
                    clip.sync_polarity_inverted = res.polarity_inverted
                    clip.sync_windows          = res.n_windows
                    clip.sync_lags_ms          = res.window_lags_ms
                    clip.sync_done             = True

            out_clip = temp_dir / f"clip_{i+1:02d}.mov"
            cmd = build_mux_cmd_plan(ff, clip, out_clip, progress_file,
                                     self._plan, self._square_mode,
                                     mix=self._mix_for(clip))

            if self._enable_preview:
                thumb = ThumbnailThread(ff, str(clip.path), progress_file, temp_dir)
                thumb.frame_ready.connect(self.thumbnail)
                thumb.start()
            else:
                thumb = None

            err_path = temp_dir / "ffmpeg_err.txt"
            ef = open(err_path, "wb")
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                    stderr=ef, **no_window())
            while proc.poll() is None:
                if self._cancelled:
                    proc.terminate(); ef.close()
                    _stop_thumb(thumb)
                    self._cleanup(temp_dir)
                    self.finished.emit(False, "Cancelled")
                    return
                parsed = parse_progress(read_progress(progress_file), clip.duration)
                self.progress.emit({
                    "pct": parsed["pct"], "size": parsed["size"],
                    "stage": "mux", "stage_label": label,
                    "stage_idx": i + 1, "stage_total": stage_total,
                    **self._metrics(parsed["size"], parsed["pct"], i + 1, stage_total),
                })
                time.sleep(0.4)

            _stop_thumb(thumb)
            proc.wait(); ef.close()
            if proc.returncode != 0:
                tail = _tail_text(err_path)
                self._cleanup(temp_dir)
                self.finished.emit(False, f"ffmpeg failed on {clip.name} (exit {proc.returncode})"
                                          + (f"\n\n{tail}" if tail else ""))
                return

            temp_clips.append(out_clip)
            cumulative_duration += clip.duration

        # ── Concat ────────────────────────────────────────────────────────────
        if self._cancelled:
            self._cleanup(temp_dir); self.finished.emit(False, "Cancelled"); return

        self.progress.emit({
            "pct": 0, "size": 0,
            "stage": "concat", "stage_label": "Merging",
            "stage_idx": stage_total, "stage_total": stage_total,
        })

        concat_file   = temp_dir / "concat_list.txt"
        chapters_file = temp_dir / "chapters.txt"

        with open(concat_file, "w", encoding="utf-8") as f:
            for p in temp_clips:
                safe = str(p.resolve()).replace("\\", "/").replace("'", r"'\''")
                f.write(f"file '{safe}'\n")

        with open(chapters_file, "w", encoding="utf-8") as f:
            f.write(";FFMETADATA1\n")
            if self._title:
                f.write(f"title={self._title}\n")
            f.write("\n")
            cum_ms = 0
            for clip in clips:
                dur_ms = int(clip.duration * 1000)
                f.write(f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={cum_ms}\nEND={cum_ms+dur_ms}\ntitle={clip.stem}\n\n")
                cum_ms += dur_ms

        # Archival manifest (additive): a sidecar JSON is always written after the
        # master lands; the same manifest is embedded as a master metadata tag too.
        # In archival mode the baseline is only an intermediate — the FINAL mux (with
        # the archival tracks) embeds the complete manifest, so skip the embed here.
        manifest = self._build_manifest(clips)
        if self._archival:
            baseline_target = temp_dir / "baseline.mov"
            embed = None
        else:
            baseline_target = final_tmp
            embed = manifest_mod.metadata_embed_args(
                manifest, is_mov=str(final_tmp).lower().endswith(".mov"))
            if embed and len(embed[-1]) > self._MANIFEST_EMBED_MAX:
                embed = None

        cmd = build_concat_cmd(ff, concat_file, chapters_file, baseline_target, progress_file,
                               extra_out_args=embed)

        thumb = None
        if self._enable_preview:
            thumb = ThumbnailThread(ff, str(temp_clips[0]), progress_file, temp_dir)
            thumb.frame_ready.connect(self.thumbnail)
            thumb.start()

        if not self._run_stage(cmd, temp_dir, progress_file,
                               "Building baseline" if self._archival else "Merging",
                               stage_total, stage_total, cumulative_duration, thumb=thumb):
            return

        if self._archival:
            if not self._build_and_mux_archival(ff, clips, manifest, baseline_target, final_tmp,
                                                temp_dir, progress_file, stage_total,
                                                cumulative_duration):
                return

        # Move the finished file into place atomically (same volume → instant,
        # so a cloud-sync client only ever sees the complete master).
        try:
            if self._output.exists():
                self._output.unlink()
            os.replace(final_tmp, self._output)
        except Exception as e:
            self._cleanup(temp_dir)
            self.finished.emit(False, f"Could not move output into place: {e}")
            return
        self._cleanup(temp_dir)

        # Sidecar manifest beside the finished master — best-effort, never fails
        # the merge (the master itself is already complete and valid).
        try:
            manifest_mod.write_sidecar(manifest, self._output)
        except Exception:
            pass

        size_gb = self._output.stat().st_size / 1024 ** 3
        self.finished.emit(True, f"Done — {size_gb:.2f} GB")

    def _cleanup(self, temp_dir: Path):
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass
        # Remove a leftover partial master in the output folder (cancel/failure).
        try:
            if self._final_tmp and Path(self._final_tmp).exists():
                Path(self._final_tmp).unlink()
        except Exception:
            pass


# ── WhatsApp export worker ────────────────────────────────────────────────────

class WhatsAppWorker(QThread):
    progress  = Signal(dict)
    thumbnail = Signal(str)
    finished  = Signal(bool, str)

    def __init__(self, source: str, start: str, duration: str,
                 output: Path, grade: Optional[Grade],
                 enable_preview: bool = True):
        super().__init__()
        self._source         = source
        self._start          = start
        self._duration       = duration
        self._output         = output
        self._grade          = grade
        self._enable_preview = enable_preview
        self._cancelled      = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        ff, _ = get_ffmpeg()
        temp_dir      = get_app_dir() / "_temp"
        temp_dir.mkdir(exist_ok=True)
        progress_file = temp_dir / "progress.txt"
        progress_file.write_text("")

        dur_secs = hms_to_seconds(self._duration)
        cmd = build_whatsapp_cmd(ff, self._source, self._start, self._duration,
                                 self._output, self._grade, progress_file)

        if self._enable_preview:
            thumb = ThumbnailThread(ff, self._source, progress_file, temp_dir, self._grade)
            thumb.frame_ready.connect(self.thumbnail)
            thumb.start()
        else:
            thumb = None

        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, **no_window())
        while proc.poll() is None:
            if self._cancelled:
                proc.terminate()
                _stop_thumb(thumb)
                self._cleanup(temp_dir); self.finished.emit(False, "Cancelled"); return
            parsed = parse_progress(read_progress(progress_file), dur_secs)
            self.progress.emit({"pct": parsed["pct"], "size": parsed["size"]})
            time.sleep(0.4)

        _stop_thumb(thumb)
        proc.wait()
        self._cleanup(temp_dir)

        if proc.returncode != 0:
            self.finished.emit(False, f"ffmpeg failed (exit {proc.returncode})")
            return

        size_mb = self._output.stat().st_size / 1024 / 1024
        self.finished.emit(True, f"Done — {size_mb:.1f} MB")

    def _cleanup(self, temp_dir: Path):
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


# ── Frame preview worker ──────────────────────────────────────────────────────

class FramePreviewWorker(QThread):
    done  = Signal(str)
    error = Signal(str)

    def __init__(self, source: str, timecode: str, grade: Optional[Grade], out_path: str):
        super().__init__()
        self._source   = source
        self._timecode = timecode
        self._grade    = grade
        self._out      = out_path

    def run(self):
        ff, _ = get_ffmpeg()
        cmd = build_preview_cmd(ff, self._source, self._timecode, self._grade, self._out)
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=20)
            if r.returncode == 0 and Path(self._out).exists():
                self.done.emit(self._out)
            else:
                self.error.emit(r.stderr.decode(errors="ignore")[-200:])
        except Exception as e:
            self.error.emit(str(e))
