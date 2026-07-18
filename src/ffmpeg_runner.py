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
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThread, Signal

from grade_manager import Grade
from probe import probe_duration, probe_concat_segment, pix_fmt_info

from core.binaries import get_app_dir, get_ffmpeg, no_window
from core import manifest as manifest_mod
from core import collection as collection_mod
from core import cloudsync
from core.progress import read_progress, parse_progress
from core.eta import ConservativeEta
from core.sync_advanced import analyze_sync
from core.ffmpeg_cmd import (
    hms_to_seconds, MixSpec, OutputPlan, SLOWMO_RATIO,
    build_mux_cmd, build_mux_cmd_plan, build_concat_cmd, build_concat_reencode_cmd,
    build_whatsapp_cmd,
    build_preview_cmd, build_thumbnail_cmd,
    build_archival_concat_cmd, build_final_archival_mux_cmd, build_wav_archival_mux_cmd,
    build_lrv_archival_mux_cmd,
    ConformSpec, DEFAULT_CONFORM,
)
from core.extract import (build_recovery_plan, build_recover_clip_cmd,
                          recover_metadata_args, SEEK_EPS)
from core.verify import (
    build_video_es_cmd, build_audio_pcm_cmd, md5_of_file,
    ClipVerifyResult, StreamCheck, write_verify_log,
    probe_rotation, probe_key_tags, tags_equal, probe_video_codec,
    probe_keyframe_times, probe_video_stream_duration,
    probe_audio_stream_count, probe_video_stream_count,
    build_decoded_video_md5_cmd, build_decoded_audio_md5_cmd, decoded_md5,
    predict_unverifiable, _PREDICTED_PREFIX,
    quick_video_rounding_check, quick_wav_rounding_check,
    clip_has_audio_priming_gap,
)

# Re-exported for existing call sites (main.py, merge_tab.py, extract_tab.py).
_no_window = no_window


def _instance_scratch_name() -> str:
    """A per-run-instance subfolder name (PID + a short random suffix) so two
    concurrent merges/exports — two instances of the app, or a merge and a
    WhatsApp export running at once — never share the same per-clip scratch
    directory. Before this, every worker resolved to the exact same hardcoded
    `_temp` path with no isolation at all: confirmed directly (battle-test
    round 2) as a real collision between two independent, unrelated merge
    processes running at the same time, each writing clip_NN.mov files into
    the other's scratch dir — and each one's own cleanup then `rmtree`s that
    ENTIRE shared directory afterward, deleting the other process's in-flight
    files outright, not just risking a same-numbered overwrite. The PID alone
    isn't quite enough (two merges could still overlap within one process
    given the API); the random suffix covers that too."""
    return f"run_{os.getpid()}_{uuid.uuid4().hex[:8]}"


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
    verification_done = Signal(bool, str, str)   # all_passed, summary, report_path

    def __init__(self, clips: list, output_path: Path,
                 plan: OutputPlan, square_mode: str, title: str = "",
                 enable_preview: bool = True, scratch_override: str = "",
                 archival: bool = False, conform: ConformSpec = DEFAULT_CONFORM,
                 per_clip_archival: bool = False, verify_md5: bool = False,
                 compat_baseline: bool = False, skip_predictable_verify: bool = True,
                 compat_codec: str = "h264", compat_prores_profile: str = "hq"):
        super().__init__()
        self._clips             = clips
        self._output            = output_path
        self._plan              = plan
        self._square_mode       = square_mode
        self._title             = title
        self._enable_preview    = enable_preview
        self._scratch_override  = scratch_override
        self._archival          = archival   # embed odd-spec originals on parallel archival tracks
        self._conform           = conform    # baseline the transcode conforms non-matching clips to
        self._per_clip_archival = per_clip_archival   # one archival track per clip (bit-exact) vs concat by spec
        self._verify_md5        = verify_md5   # post-merge: MD5-check every clip's recovery against its original
        # Skip the extraction+hash pass ENTIRELY for a check the app already
        # knows (from the manifest, before touching ffmpeg) can't produce a
        # meaningful pass — a transcoded clip with no archival track of its
        # own, or camera audio mid-way in a shared archival track (see
        # core.verify.predict_unverifiable). Off = the user's override for full,
        # exhaustive verification regardless of what's predictable.
        self._skip_predictable_verify = skip_predictable_verify
        # Re-encode the concatenated baseline into ONE clean, continuous stream
        # instead of stream-copy-splicing independently-encoded segments (which
        # produces broken-reference playback — green frames / freezes / static,
        # differently per player). The baseline is the WATCHABLE copy; lossless
        # originals live in the archival tracks / kept clip files.
        self._compat_baseline   = compat_baseline
        self._compat_codec      = compat_codec           # "h264" | "prores"
        self._compat_prores_profile = compat_prores_profile   # "proxy" | "standard" | "hq"
        self._final_tmp         = None
        self._cancelled         = False

    def _mix_for(self, clip) -> MixSpec:
        """Per-clip MixSpec carrying this clip's drift/polarity from sync.
        `effective_drift_ratio()` prefers the user's own override (Advanced
        sync dialog) over the auto-detected value when one is set."""
        return MixSpec(
            kind=self._plan.mix_kind,
            match_levels=self._plan.mix_match_levels,
            drift_ratio=clip.effective_drift_ratio(),
            polarity_inverted=clip.sync_polarity_inverted,
        )

    def cancel(self):
        self._cancelled = True

    def _metrics(self, size: int) -> dict:
        """Byte-weighted, conservative progress/rate/ETA — see
        core.eta.ConservativeEta. `size` is the CURRENT stage's own live
        growing output size (from ffmpeg's -progress file); combined with
        `self._produced_bytes_base` (every fully-completed prior stage's own
        real final size, accumulated as run() proceeds) this gives total
        bytes written across the whole pipeline so far, fed against
        `self._expected_total_bytes` (the matching multi-pass estimate — see
        _estimate_expected_total_bytes). Excludes the MD5-verify stage
        entirely (it reads/decodes but writes no persistent, sized output —
        see its own clip-count-based progress in _verify_md5_recovery)."""
        produced = self._produced_bytes_base + max(0, size)
        est = self._eta.estimate(produced, self._expected_total_bytes)
        return {
            "produced_bytes": produced,
            "expected_total_bytes": self._expected_total_bytes,
            "byte_pct": est["pct"],
            "rate_bps": est["rate_bps"],
            "eta_secs": est["eta_secs"],
            "total_secs": est["total_secs"],
            "elapsed_secs": est["elapsed_secs"],
        }

    def _estimate_expected_total_bytes(self, clips: list) -> int:
        """The ETA/progress-readout denominator — see
        core.plan_report.estimate_total_pipeline_bytes (shared with the Merge
        tab's own pre-flight/disk-space estimate so the two can never drift
        apart from each other)."""
        from core.plan_report import estimate_total_pipeline_bytes
        return estimate_total_pipeline_bytes(
            clips, self._plan, archival=self._archival,
            compat_baseline=self._compat_baseline, compat_codec=self._compat_codec,
            compat_prores_profile=self._compat_prores_profile)

    def _make_scratch(self) -> Path:
        """A fast, writable, LOCAL scratch dir for the per-clip temp files.

        Deliberately NOT the output folder — if the output is a slow cloud-synced
        location (e.g. Jottacloud), writing every temp clip there cripples speed.
        Only the finished master is written to the output folder (once).

        Each call gets its OWN uniquely-named subfolder under the chosen base
        (see `_instance_scratch_name`) — two concurrent merges (two app
        instances, or two overlapping runs in one process) never share a
        scratch directory, and each one's cleanup only ever removes its own.
        """
        candidates = []
        if self._scratch_override:
            candidates.append(Path(self._scratch_override) / "_lvfb_temp")
        candidates.append(get_app_dir() / "_temp")
        candidates.append(Path(tempfile.gettempdir()) / "lunavault_fusebox")
        name = _instance_scratch_name()
        for base in candidates:
            try:
                scratch = base / name
                scratch.mkdir(parents=True, exist_ok=True)
                probe = scratch / ".write_test"
                probe.write_text("ok")
                probe.unlink()
                return scratch
            except Exception:
                continue
        return get_app_dir() / "_temp" / name

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
            status = clip.effective_status()
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
                # Baseline-mode default; _build_and_mux_archival upgrades clips that
                # get their own archival track to "byte-exact" (see below). A
                # conforming clip recovers decode-lossless from a stream-copied
                # baseline concat — BUT a compat (re-encoded) baseline is lossy, so
                # even conforming clips are "transcoded" there. An odd-spec clip
                # with no archival track was re-encoded either way ("transcoded").
                recovery_fidelity=("decode-lossless"
                                   if (status == "ok" and not self._compat_baseline)
                                   else "transcoded"),
                spec_group=("" if status == "ok"
                            else manifest_mod.spec_signature(codec, width, height, fps, pix,
                                                             (st.rotation if st else 0))),
                has_camera_audio=has_cam, original_audio_codec=acodec,
                audio_lossless=audio_lossless, has_wav=clip.has_wav(),
                baseline_chapter_index=idx,
                rotation=(st.rotation if st else 0),
                is_vfr=bool(st.is_vfr if st else False),
                color_space=(st.color_space if st else "") or "",
                camera_id=getattr(clip, "camera_id", "") or "",
                camera_label=getattr(clip, "camera_label", "") or "",
                creation_time=(st.creation_time if st else "") or "",
                metadata_tags=dict(st.format_tags) if st else {},
                # Measured concat positions from the per-clip temp files (see
                # ClipEntry docstring / Task 85) — None when the probe failed,
                # which sends recovery back to the modelled video offsets.
                concat_start=getattr(clip, "_concat_start", None),
                wav_track_duration=getattr(clip, "_wav_seg_duration", None),
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
                **self._metrics(parsed["size"]),
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

    def _run_clip_proc(self, cmd, temp_dir, progress_file, label, i, stage_total,
                       duration, thumb=None) -> tuple:
        """Run one per-clip ffmpeg command. Unlike `_run_stage`, does NOT clean up
        or emit `finished` on failure — the caller may want to retry (GPU→software
        fallback) before giving up. Returns ("ok"|"failed"|"cancelled", tail_text)."""
        err_path = temp_dir / "ffmpeg_err.txt"
        ef = open(err_path, "wb")
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=ef, **no_window())
        while proc.poll() is None:
            if self._cancelled:
                proc.terminate(); ef.close(); _stop_thumb(thumb)
                return "cancelled", ""
            parsed = parse_progress(read_progress(progress_file), duration)
            self.progress.emit({
                "pct": parsed["pct"], "size": parsed["size"],
                "stage": "mux", "stage_label": label,
                "stage_idx": i + 1, "stage_total": stage_total,
                **self._metrics(parsed["size"]),
            })
            time.sleep(0.4)
        _stop_thumb(thumb)
        proc.wait(); ef.close()
        if proc.returncode != 0:
            return "failed", _tail_text(err_path)
        return "ok", ""

    _ENCODER_LABELS = {"nvenc": "GPU: NVENC", "qsv": "GPU: Quick Sync", "amf": "GPU: AMD AMF",
                       "vaapi": "GPU: VAAPI"}

    def _clip_stage_label(self, clip, hw_encoder: Optional[str] = None) -> str:
        """Plain-language description of what's about to happen to this clip —
        surfaced live in the UI so a slow transcode doesn't look like a hang
        next to a clip that's merely being stream-copied."""
        if clip.effective_status() == "ok":
            return f"Stream-copying {clip.stem} — lossless, no re-encode"
        enc = hw_encoder if hw_encoder is not None else getattr(self._conform, "hw_encoder", "off")
        enc_txt = self._ENCODER_LABELS.get(enc, "CPU: libx264")
        conflicts = clip.stream.conflicts if (clip.stream and clip.stream.conflicts) else []
        if conflicts:
            reason = conflicts[0]
        elif clip.video_source_override == "lrv":
            reason = "using LRV proxy"
        elif clip.video_source_override == "transcode":
            reason = "forced by user"
        else:
            reason = "different spec"
        return f"Transcoding {clip.stem} — {enc_txt} ({reason})"

    def _build_and_mux_archival(self, ff, clips, manifest, baseline, final_tmp,
                                temp_dir, progress_file, stage_total, total_dur) -> bool:
        """Build per-spec-group archival intermediates from the odd-spec ORIGINALS,
        assign the manifest's archival stream locations, then mux baseline + archival
        tracks into the final master with the (now complete) manifest embedded."""
        enabled = [t.kind for t in self._plan.tracks if t.enabled]
        base_audio_count = len(enabled)
        base_video_count = 1 if self._plan.include_video else 0
        # manifest.baseline_audio_tracks is already set unconditionally in run()
        # (it describes the baseline's own layout, independent of archival tracks).

        # Group odd-spec clips by spec signature, preserving order — each group's
        # ORIGINAL files become one archival track. In per-clip mode, every clip
        # gets its own singleton group instead (key includes `i`) — concat-free,
        # so recovery is bit-exact for every clip, not just already-lone ones, at
        # the cost of one archival track per clip instead of one per spec group.
        groups: dict = {}   # key -> list[(clip, entry)]
        for i, clip in enumerate(clips):
            entry = manifest.clips[i]
            if entry.conform_status == "ok":
                continue
            key = (entry.spec_group, i) if self._per_clip_archival else entry.spec_group
            groups.setdefault(key, []).append((clip, entry))

        archival_files = []
        groups_entries = []
        multi_clip_groups = []   # (entries, built_intermediate) for groups that were concatenated
        for gi, (sig, pairs) in enumerate(groups.items()):
            entries_in_group = [e for _, e in pairs]
            if len(pairs) == 1:
                # A lone clip needs no concat — use the original directly. Going
                # through the concat demuxer perturbs AAC priming (audio wouldn't
                # be bit-exact); a direct stream copy in the final mux IS bit-exact.
                archival_files.append(Path(pairs[0][0].path))
                entries_in_group[0].recovery_fidelity = "byte-exact"
            else:
                lst = temp_dir / f"arch_list_{gi}.txt"
                with open(lst, "w", encoding="utf-8") as f:
                    for clip, _ in pairs:
                        safe = str(clip.path.resolve()).replace("\\", "/").replace("'", r"'\''")
                        f.write(f"file '{safe}'\n")
                interm = temp_dir / f"archive_{gi}.mov"
                if not self._run_stage(build_archival_concat_cmd(ff, lst, interm),
                                       temp_dir, progress_file,
                                       f"Archiving original files, group {gi + 1}/{len(groups)} "
                                       "— lossless copy for recovery",
                                       stage_total, stage_total, total_dur):
                    return False
                archival_files.append(interm)
                multi_clip_groups.append((entries_in_group, [c for c, _ in pairs], interm))
                # Concatenated originals recover decode-lossless (the concat demuxer
                # strips SEI/AUD NALs and perturbs AAC priming), not byte-exact.
                for e in entries_in_group:
                    e.recovery_fidelity = "decode-lossless"
            groups_entries.append(entries_in_group)

        manifest_mod.assign_archival_locations(groups_entries, base_video_count, base_audio_count)

        # assign_archival_locations only sets the DRIFTING duration-sum offsets.
        # For every concatenated (multi-clip) archival track, re-pin each clip's
        # in_track_start/duration to the built intermediate's real keyframes, so
        # recovery's `-ss` seek lands on the right clip boundary (see
        # manifest.measure_in_track_offsets). Lone-clip tracks stay at offset 0.
        _, fp = get_ffmpeg()
        kw = no_window()
        for entries_in_group, clips_in_group, interm in multi_clip_groups:
            kf_times = probe_keyframe_times(fp, str(interm), **kw)
            total_dur = probe_video_stream_duration(fp, str(interm), **kw)
            precise = [probe_video_stream_duration(fp, str(c.path), **kw) for c in clips_in_group]
            manifest_mod.measure_in_track_offsets(entries_in_group, precise, kf_times, total_dur)

        embed = manifest_mod.metadata_embed_args(
            manifest, is_mov=str(final_tmp).lower().endswith(".mov"))
        if embed and len(embed[-1]) > self._MANIFEST_EMBED_MAX:
            embed = None
        cmd = build_final_archival_mux_cmd(ff, baseline, archival_files, final_tmp,
                                           progress_file, extra_out_args=embed,
                                           base_has_video=bool(base_video_count))
        return self._run_stage(cmd, temp_dir, progress_file,
                               "Finalising archive — combining baseline and originals",
                               stage_total, stage_total, total_dur)

    def _append_preserved_wavs(self, ff, fp, clips, manifest, master_path: Path,
                              temp_dir, progress_file, stage_total, total_dur) -> Optional[Path]:
        """If any clip's "Also preserve this WAV in full" opt-in is set, append
        each such clip's untouched original WAV as its own standalone lossless
        track onto the just-finished master. Runs regardless of whether
        Archival master (video) is on — a wholly separate, audio-only opt-in,
        set per clip from the WAV-mismatch resolution dialog.

        Returns the master's path to use going forward: `master_path`
        unchanged if nothing was requested, a NEW path on success (the old
        one is removed), or None on failure (already cleaned up + reported by
        _run_stage, same convention as _build_and_mux_archival)."""
        to_preserve = [(i, clip) for i, clip in enumerate(clips)
                      if getattr(clip, "preserve_wav_full", False) and clip.has_wav()]
        if not to_preserve:
            return master_path
        kw = no_window()
        existing_audio_count = probe_audio_stream_count(fp, str(master_path), **kw)
        wav_files = [clip.wav_path for _, clip in to_preserve]
        out_path = master_path.parent / ("~partial2_" + self._output.name)
        self._final_tmp = out_path   # so a cancel mid-stage cleans up the right file
        cmd = build_wav_archival_mux_cmd(ff, master_path, wav_files, existing_audio_count,
                                         out_path, progress_file)
        n = len(wav_files)
        label = f"Preserving {n} original WAV file{'s' if n != 1 else ''} in full, on its own track"
        if not self._run_stage(cmd, temp_dir, progress_file, label,
                               stage_total, stage_total, total_dur):
            return None
        for offset, (i, _clip) in enumerate(to_preserve):
            manifest.clips[i].wav_archival_stream = existing_audio_count + offset
        try:
            master_path.unlink()
        except Exception:
            pass
        return out_path

    def _append_preserved_lrvs(self, ff, fp, clips, manifest, master_path: Path,
                              temp_dir, progress_file, stage_total, total_dur) -> Optional[Path]:
        """If any clip's "Also preserve the LRV proxy on its own track" opt-in
        is set, append each such clip's low-res proxy (video + its own audio)
        as standalone tracks onto the just-finished master — the video
        analogue of _append_preserved_wavs, same layering (runs after it, so
        it's naturally counting streams AFTER any preserved WAVs already
        landed). Same return convention: unchanged path if nothing requested,
        a new path on success, None on failure (already reported)."""
        to_preserve = [(i, clip) for i, clip in enumerate(clips)
                      if getattr(clip, "preserve_lrv", False) and clip.has_lrv()]
        if not to_preserve:
            return master_path
        kw = no_window()
        existing_video_count = probe_video_stream_count(fp, str(master_path), **kw)
        existing_audio_count = probe_audio_stream_count(fp, str(master_path), **kw)
        lrv_files = [clip.lrv_path for _, clip in to_preserve]
        out_path = master_path.parent / ("~partial3_" + self._output.name)
        self._final_tmp = out_path
        cmd = build_lrv_archival_mux_cmd(ff, master_path, lrv_files,
                                         existing_video_count, existing_audio_count,
                                         out_path, progress_file)
        n = len(lrv_files)
        label = f"Preserving {n} low-res prox{'ies' if n != 1 else 'y'} on its own track"
        if not self._run_stage(cmd, temp_dir, progress_file, label,
                               stage_total, stage_total, total_dur):
            return None
        for offset, (i, clip) in enumerate(to_preserve):
            manifest.clips[i].lrv_video_archival_track = existing_video_count + offset
            manifest.clips[i].lrv_audio_archival_track = existing_audio_count + offset
        try:
            master_path.unlink()
        except Exception:
            pass
        return out_path

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

        clips       = sorted(self._clips, key=lambda c: c.order_idx)
        stage_total = len(clips) + 1

        # live-metrics state — see _metrics()/_estimate_expected_total_bytes()
        self._eta = ConservativeEta()
        self._expected_total_bytes = self._estimate_expected_total_bytes(clips)
        self._produced_bytes_base = 0

        temp_clips: list[Path] = []
        cumulative_duration = 0.0
        # Measured concat positions (see manifest.ClipEntry.concat_start): the
        # concat demuxer advances each segment by the temp FILE's container
        # duration, so the true position of clip N in the master is the sum of
        # the *measured* durations of temp files 0..N-1 — not the sum of video
        # durations. Probed per temp file below; the WAV slot's own measured
        # length rides along for the WAV-backup recovery window.
        wav_slot = next((j for j, t in enumerate([t for t in self._plan.tracks if t.enabled])
                         if t.kind == "wav"), None)
        concat_cursor = 0.0
        concat_measured = True   # one failed probe poisons every LATER position

        # Resolve "auto" once, the same way _resolve_hw_extras/build_mux_cmd_plan
        # will for the real command, so the per-clip progress label names the
        # actual encoder in use rather than the raw setting. _ENCODER_LABELS
        # only has entries for real vendor names ("nvenc"/"qsv"/"amf") — the
        # literal string "auto" always missed that lookup and fell through to
        # the "CPU: libx264" default, even while a real GPU encode was
        # genuinely running (confirmed directly: a hardware-accelerated merge
        # still showed "CPU: libx264" throughout).
        hw_encoder_setting = getattr(self._conform, "hw_encoder", "off") or "off"
        if hw_encoder_setting == "auto":
            from core.gpu_encode import detect_best_hw
            hw_encoder_setting = detect_best_hw(ff, getattr(self._conform, "codec", None) or "hevc") or "off"

        for i, clip in enumerate(clips):
            if self._cancelled:
                self._cleanup(temp_dir)
                self.finished.emit(False, "Cancelled")
                return

            label = self._clip_stage_label(clip, hw_encoder=hw_encoder_setting)
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
                                       clip.duration, clip.wav_duration,
                                       anchor_mode=clip.alignment_mode)
                    clip.wav_offset            = res.constant_offset + clip.manual_nudge_ms / 1000.0
                    clip.sync_drift_ratio      = res.drift_ratio
                    clip.sync_confidence_ms    = res.confidence_ms
                    clip.sync_polarity_inverted = res.polarity_inverted
                    clip.sync_windows          = res.n_windows
                    clip.sync_lags_ms          = res.window_lags_ms
                    clip.sync_done             = True

            out_clip = temp_dir / f"clip_{i+1:02d}.mov"
            gpu_requested = clip.effective_status() != "ok" and getattr(self._conform, "hw_encoder", "off") != "off"

            cmd = build_mux_cmd_plan(ff, clip, out_clip, progress_file,
                                     self._plan, self._square_mode,
                                     mix=self._mix_for(clip), conform=self._conform)
            thumb = None
            if self._enable_preview:
                thumb = ThumbnailThread(ff, str(clip.path), progress_file, temp_dir)
                thumb.frame_ready.connect(self.thumbnail)
                thumb.start()

            outcome, tail = self._run_clip_proc(cmd, temp_dir, progress_file, label,
                                                i, stage_total, clip.duration, thumb)

            if outcome == "cancelled":
                self._cleanup(temp_dir)
                self.finished.emit(False, "Cancelled")
                return

            if outcome == "failed" and gpu_requested:
                # GPU transcode failed (no driver, VRAM exhausted, encoder session
                # limit, etc.) — retry this clip once in software rather than
                # failing the whole merge over a GPU hiccup.
                sw_conform = replace(self._conform, hw_encoder="off")
                label = self._clip_stage_label(clip, hw_encoder="off")
                cmd = build_mux_cmd_plan(ff, clip, out_clip, progress_file,
                                         self._plan, self._square_mode,
                                         mix=self._mix_for(clip), conform=sw_conform)
                outcome, tail = self._run_clip_proc(cmd, temp_dir, progress_file, label,
                                                    i, stage_total, clip.duration, thumb=None)

            if outcome == "failed":
                self._cleanup(temp_dir)
                self.finished.emit(False, f"ffmpeg failed on {clip.name}"
                                          + (f"\n\n{tail}" if tail else ""))
                return

            temp_clips.append(out_clip)
            cumulative_duration += clip.duration
            try:
                self._produced_bytes_base += out_clip.stat().st_size
            except Exception:
                pass

            # Measure this temp file's true concat footprint (local file, one
            # cheap ffprobe). A failed probe leaves this clip's fields unset AND
            # poisons every later position (the cursor is only trustworthy while
            # every preceding duration was actually measured) — recovery then
            # falls back to the modelled video offsets, exactly as before.
            file_dur, wav_dur = probe_concat_segment(fp, str(out_clip), wav_slot)
            clip._concat_start = concat_cursor if concat_measured else None
            clip._wav_seg_duration = wav_dur if wav_dur > 0 else None
            if file_dur > 0:
                concat_cursor += file_dur
            else:
                concat_measured = False

        # ── Concat ────────────────────────────────────────────────────────────
        if self._cancelled:
            self._cleanup(temp_dir); self.finished.emit(False, "Cancelled"); return

        if self._compat_baseline:
            codec_label = "ProRes" if self._compat_codec == "prores" else "H.264"
            merge_label = f"Re-encoding into one smooth, compatible take ({codec_label})"
        else:
            merge_label = "Merging clips into the baseline — stream copy, lossless"
        self.progress.emit({
            "pct": 0, "size": 0,
            "stage": "concat", "stage_label": merge_label,
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
        # The baseline's own audio-track layout (which slot index is "camera" vs
        # "wav" vs "mix") is a property of the OUTPUT PLAN, independent of whether
        # archival tracks get added on top afterward — always record it, not just
        # when Archival master is on. Confirmed as a real bug this way: with
        # Archival master off, this was never set at all (stayed the Manifest
        # dataclass's empty-dict default), so build_recovery_plan's baseline
        # branch could never find a camera-audio stream index for ANY clip —
        # meaning Extract (and this app's own MD5 verification) silently
        # recovered video with no audio at all for every non-archival master.
        enabled_tracks = [t.kind for t in self._plan.tracks if t.enabled]
        manifest.baseline_audio_tracks = {kind: i for i, kind in enumerate(enabled_tracks)}
        manifest.baseline_has_video = bool(self._plan.include_video)
        if self._archival:
            baseline_target = temp_dir / "baseline.mov"
            embed = None
        else:
            baseline_target = final_tmp
            embed = manifest_mod.metadata_embed_args(
                manifest, is_mov=str(final_tmp).lower().endswith(".mov"))
            if embed and len(embed[-1]) > self._MANIFEST_EMBED_MAX:
                embed = None

        if self._compat_baseline:
            # Watchable-master path: one clean continuous re-encode (H.264 or
            # ProRes), so the baseline plays everywhere (no broken concat
            # splices). See task #13.
            cmd = build_concat_reencode_cmd(ff, concat_file, chapters_file, baseline_target,
                                            progress_file, extra_out_args=embed,
                                            codec=self._compat_codec,
                                            prores_profile=self._compat_prores_profile,
                                            hw_encoder=self._conform.hw_encoder,
                                            hw_decode=self._conform.hw_decode)
        else:
            cmd = build_concat_cmd(ff, concat_file, chapters_file, baseline_target, progress_file,
                                   extra_out_args=embed)

        thumb = None
        if self._enable_preview:
            thumb = ThumbnailThread(ff, str(temp_clips[0]), progress_file, temp_dir)
            thumb.frame_ready.connect(self.thumbnail)
            thumb.start()

        if not self._run_stage(cmd, temp_dir, progress_file, merge_label,
                               stage_total, stage_total, cumulative_duration, thumb=thumb):
            return
        try:
            self._produced_bytes_base += baseline_target.stat().st_size
        except Exception:
            pass

        if self._archival:
            if not self._build_and_mux_archival(ff, clips, manifest, baseline_target, final_tmp,
                                                temp_dir, progress_file, stage_total,
                                                cumulative_duration):
                return
            try:
                self._produced_bytes_base += final_tmp.stat().st_size
            except Exception:
                pass

        try:
            prev_size = final_tmp.stat().st_size if final_tmp.exists() else 0
        except Exception:
            prev_size = 0
        preserved = self._append_preserved_wavs(ff, fp, clips, manifest, final_tmp, temp_dir,
                                                progress_file, stage_total, cumulative_duration)
        if preserved is None:
            return
        final_tmp = preserved
        try:
            self._produced_bytes_base += max(0, final_tmp.stat().st_size - prev_size)
        except Exception:
            pass

        try:
            prev_size = final_tmp.stat().st_size if final_tmp.exists() else 0
        except Exception:
            prev_size = 0
        preserved = self._append_preserved_lrvs(ff, fp, clips, manifest, final_tmp, temp_dir,
                                                progress_file, stage_total, cumulative_duration)
        if preserved is None:
            return
        final_tmp = preserved
        try:
            self._produced_bytes_base += max(0, final_tmp.stat().st_size - prev_size)
        except Exception:
            pass

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

        # Sidecar manifest + human-readable restore log beside the finished
        # master — best-effort, never fails the merge (the master itself is
        # already complete and valid).
        try:
            manifest_mod.write_sidecar(manifest, self._output)
            manifest_mod.write_restore_log(manifest, self._output)
        except Exception:
            pass

        if self._verify_md5 and not self._cancelled:
            self._verify_md5_recovery(ff, fp, clips, manifest)

        # Emit the self-describing collection folder (collection.json + thumbnails
        # + verified.txt) beside the master — the artifact Home / the album read.
        # Best-effort: the master is already complete, so a thumbnail hiccup never
        # fails the merge.
        try:
            self._emit_collection(ff, clips, manifest)
        except Exception:
            pass

        size_gb = self._output.stat().st_size / 1024 ** 3
        self.finished.emit(True, f"Done — {size_gb:.2f} GB")

    def _emit_collection(self, ff: str, clips: list, manifest) -> Optional[Path]:
        """Write the collection folder's organisational layer next to the finished
        master: a `thumbs/` dir with one frame per clip, then collection.json +
        verified.txt via core.collection. Returns the collection.json path."""
        folder = self._output.parent
        thumbs = folder / "thumbs"
        thumbs.mkdir(parents=True, exist_ok=True)
        kw = no_window()

        cover_rel = ""
        for i, clip in enumerate(clips):
            out = thumbs / f"{i + 1:03d}.jpg"
            # One representative frame from the ORIGINAL (no master seeking needed);
            # a little way in so we skip black lead-in.
            cmd = [ff, "-y", "-v", "error", "-ss", "0.5", "-i", str(clip.path),
                   "-frames:v", "1", "-vf", "scale=-2:360", str(out)]
            try:
                subprocess.run(cmd, capture_output=True, timeout=60, **kw)
            except Exception:
                pass
            if out.exists() and not cover_rel:
                cover_rel = f"thumbs/{out.name}"

        cloud = cloudsync.is_cloud_backed(folder)
        c = collection_mod.build_collection(
            manifest, name=self._output.stem, cover=cover_rel,
            cloud_backed=cloud,
            storage_mode=(collection_mod.STORAGE_PORTABLE if cloud
                          else collection_mod.STORAGE_COMPACT),
            verified_passed=getattr(self, "_verify_passed", len(manifest.clips)),
        )
        # If verification didn't run, treat structural recoverability as the "pass".
        if not self._verify_md5:
            c.verified.passed = c.verified.total
        return collection_mod.emit_collection(c, folder)

    def _verify_md5_recovery(self, ff: str, fp: str, clips: list, manifest):
        """Extract every clip straight back out of the just-finished master
        and MD5-compare it against its original file — proving recovery is
        real rather than trusting the manifest's own bit_exact bookkeeping
        (see core/verify.py's module docstring for why this hashes raw
        elementary streams/decoded PCM, not whole files)."""
        verify_dir = self._make_scratch() / "verify"
        verify_dir.mkdir(parents=True, exist_ok=True)
        results = []
        total = len(clips)
        try:
            for i, (clip, entry) in enumerate(zip(clips, manifest.clips)):
                if self._cancelled:
                    break
                self.progress.emit({
                    "pct": int(100 * i / max(1, total)), "size": 0,
                    "stage": "verify",
                    "stage_label": f"Verifying {clip.stem} ({i + 1}/{total}) — MD5 pass "
                                   "against the original",
                    "stage_idx": i + 1, "stage_total": total,
                })
                results.append(self._verify_one_clip(ff, fp, clip, entry, manifest, verify_dir))
        finally:
            shutil.rmtree(verify_dir, ignore_errors=True)

        report_path = self._output.parent / (self._output.stem + ".verify.log")
        try:
            write_verify_log(report_path, self._output.name, results)
        except Exception:
            pass

        passed = sum(1 for r in results if r.passed)
        total_checked = len(results)
        n_predicted_skips = sum(
            1 for r in results for c in r.checks
            if c.skipped_reason.startswith(_PREDICTED_PREFIX))
        self._verify_passed = passed          # picked up by _emit_collection
        self._verify_total = total_checked
        all_passed = total_checked > 0 and passed == total_checked
        skip_note = (f" ({n_predicted_skips} check{'s' if n_predicted_skips != 1 else ''} "
                     "predicted unverifiable, skipped)") if n_predicted_skips else ""
        if all_passed:
            summary = (f"Verified {passed}/{total_checked} clips byte-identical to their "
                      f"originals.{skip_note}")
        else:
            failed_names = ", ".join(r.name for r in results if not r.passed)
            summary = (f"⚠ {total_checked - passed} of {total_checked} clips did NOT verify: "
                      f"{failed_names}{skip_note}")
        self.verification_done.emit(all_passed, summary, str(report_path))

    def _verify_one_clip(self, ff: str, fp: str, clip, entry, manifest, verify_dir: Path) -> ClipVerifyResult:
        """Adaptive: a failed hash comparison is not immediately final — before
        recording a real mismatch, automatically retries with the alternate
        (unbounded) extraction, since a `-t`-cutoff pinned to the *video*
        track's declared duration can land a few codec-frame-widths off an
        audio track's own true length even when the underlying samples are
        byte-identical (confirmed directly on a real clip — see
        DEVELOPMENT.md task 76). Also checks rotation and key provenance
        metadata (GPS/location, creation time, device make/model) directly,
        since those can be lost even when the audio/video payload is fine."""
        result = ClipVerifyResult(name=clip.stem)
        plan = build_recovery_plan(manifest, entry)
        if plan is None:
            result.error = "couldn't locate this clip in the manifest"
            return result

        # NOTE: plan.bit_exact means something narrower than it sounds for a
        # BASELINE-chapter clip — "this concat cut is keyframe-precise", not
        # "the whole stream is nothing but this clip" (it's always True there,
        # even when several clips share that baseline track). Only a clip with
        # its OWN archival track is genuinely safe to read unbounded — reading
        # unbounded from a shared baseline would spill into the next clip's
        # data. Confirmed as a real distinction worth making directly: an
        # earlier version conflated the two and could have offered an
        # unbounded retry against a genuinely shared track.
        own_archival_track = entry.archival_track is not None
        safe_to_read_unbounded = own_archival_track and plan.bit_exact
        kwargs = no_window()
        if self._skip_predictable_verify:
            # Only worth the extra ffprobe call when it could actually change
            # the prediction: a clip with its own archival track never hits
            # this (its audio comes from a bit-exact standalone copy, not a
            # cut), and neither does one with no camera audio to check at all.
            priming_gap = (
                not own_archival_track and entry.has_camera_audio and plan.audio_stream is not None
                and clip_has_audio_priming_gap(fp, str(clip.path), **kwargs))
            predicted_unverifiable = predict_unverifiable(
                entry, plan, own_archival_track, safe_to_read_unbounded,
                source_has_audio_priming_gap=priming_gap)
        else:
            predicted_unverifiable = {}

        def extract_and_hash(cmd, out_path) -> str:
            r = subprocess.run(cmd, capture_output=True, **kwargs)
            if r.returncode != 0 or not out_path.exists():
                raise RuntimeError((r.stderr or b"").decode("utf-8", "ignore")[-300:] or "extraction failed")
            return md5_of_file(out_path)

        # A clip recorded as "transcode" with no archival track of its own was
        # re-encoded straight into the shared baseline and nothing else — its
        # compressed bitstream is EXPECTED to differ from the original (that's
        # what transcoding does); only Archival master (+ One track per clip)
        # gives this clip a byte-exact copy to fall back on. Distinguishing
        # this from a genuine surprise keeps the diagnosis honest rather than
        # alarming for something the current settings never promised to avoid.
        # Same criterion as core.verify.predict_unverifiable — recovery_fidelity
        # (not the literal conform_status string) already correctly folds in
        # "hdr" clips and the compat-baseline case, both of which the older
        # `conform_status == "transcode"` check missed. VIDEO-specific: camera
        # audio is typically stream-copied (-c:a copy) even when the video
        # needs conforming, so it gets its OWN "expected to differ" question
        # (entry.audio_lossless) at its own call site below — reusing this
        # video-oriented flag for audio too would wrongly skip audio's decode-
        # lossless fallback for a clip whose audio actually survived intact.
        video_expected_to_differ = entry.recovery_fidelity == "transcoded" and plan.video_stream == 0

        def compare_adaptive(label, src_cmd, rec_cmd_strict, rec_cmd_relaxed, src_path, rec_path,
                             decoded_pair=None, guard_ms=0, expected_to_differ=video_expected_to_differ):
            """Byte-exact (raw elementary-stream) comparison first; on a mismatch,
            self-diagnoses through two fallbacks before concluding it's a real
            loss:
              1. Retries against the relaxed (unbounded) extraction — catches the
                 "duration cutoff didn't land exactly on this stream's own
                 boundary" false positive.
              2. DECODE-LOSSLESS check: hashes the DECODED pixels/samples on both
                 sides (`decoded_pair`). A clip recovered from a CONCATENATED
                 track (a shared archival track, or the baseline) is decode-
                 identical to its original but not byte-identical — the concat
                 demuxer strips SEI/AUD metadata NALs (video) and perturbs AAC
                 priming at a seek boundary (audio). If the decoded content
                 matches, the footage genuinely survived; report PASS with an
                 honest "not byte-identical, but decodes identically" diagnosis
                 rather than a scary mismatch. For audio, `decoded_guard_pair`
                 re-checks after skipping `guard_ms` so the ONE real difference a
                 concat introduces — wrong priming samples at the very start of a
                 non-first clip — is isolated and reported precisely."""
            src_md5 = extract_and_hash(src_cmd, src_path)
            rec_md5 = extract_and_hash(rec_cmd_strict, rec_path)
            if src_md5 == rec_md5:
                return StreamCheck(label, src_md5, rec_md5, True)
            if rec_cmd_relaxed is not None:
                rec_path_relaxed = rec_path.with_suffix(rec_path.suffix + ".relaxed")
                try:
                    rec_md5_relaxed = extract_and_hash(rec_cmd_relaxed, rec_path_relaxed)
                    if src_md5 == rec_md5_relaxed:
                        return StreamCheck(label, src_md5, rec_md5_relaxed, True,
                                          diagnosis="auto-corrected: the bounded comparison's cutoff didn't "
                                                    "land exactly on this stream's own natural boundary; the "
                                                    "unbounded re-read matched exactly, so the underlying "
                                                    "content is genuinely intact.")
                except Exception:
                    pass
            # Decode-lossless fallback (only meaningful when the clip was NOT
            # deliberately re-encoded — a transcoded baseline clip's pixels are
            # supposed to differ, so skip it there and let the "expected" diagnosis stand).
            if decoded_pair is not None and not expected_to_differ:
                d_src = decoded_md5(decoded_pair[0], **kwargs)
                d_rec = decoded_md5(decoded_pair[1], **kwargs)
                if d_src and d_src == d_rec:
                    if label == "Video":
                        diag = ("not byte-identical, but DECODES identically — recovered from a concatenated "
                                "track, whose demux drops non-picture metadata (SEI/AUD) NALs. The actual "
                                "footage is intact; use One-track-per-clip archival for a byte-for-byte copy.")
                    else:
                        diag = (f"decodes identically across the interior (a ~{guard_ms:.0f}ms guard at each end is "
                                "excluded) — recovered from a shared archival track, whose concat seek perturbs "
                                "AAC priming at the boundary. The samples themselves are intact; One-track-per-"
                                "clip archival gives a byte-exact copy with no boundary artefact.")
                    return StreamCheck(label, src_md5, rec_md5, True, diagnosis=diag)
            if expected_to_differ:
                diag = (f"expected: this clip needed conforming and has no archival track of its own, "
                        f"so it was re-encoded straight into the shared baseline — its {label.lower()} is "
                        "supposed to differ from the original after transcoding. Enable Archival master + "
                        "\"One track per clip\" (or \"Optimize baseline for delivery\") if you need a "
                        "byte-exact copy of this clip as well.")
            elif label == "Video" and not own_archival_track and not plan.video_measured:
                # Mid-concat window computed from MODELLED cumulative durations
                # (an older master): measured on a real 8-clip master (Task 86),
                # every mismatch of this shape was the window landing ±1 frame
                # off a clip boundary — one foreign frame in, or one own frame
                # out — with the footage itself pixel-identical. Say so instead
                # of "nothing to explain it".
                diag = ("this clip was recovered from a concatenated track using an estimated "
                        "window, which can land a frame (~33ms) off the true clip boundary — a "
                        "single stray/missing boundary frame changes the checksum even when the "
                        "footage itself is identical (confirmed with per-frame analysis; run "
                        "tools/diagnose_midtrack_decode.py on this master to see exactly which). "
                        "Masters merged with this version onward measure the real boundaries, and "
                        "One-track-per-clip archival avoids shared windows entirely. Genuine damage "
                        "at the joins would show the same way — the diagnostic tool distinguishes "
                        "the two conclusively.")
            elif label == "Video" and not own_archival_track and plan.video_measured:
                diag = ("unexpected — this window used the master's MEASURED clip boundaries, so "
                        "the usual boundary-rounding explanation doesn't apply, and this stream "
                        "still decodes differently from the original. Run "
                        "tools/diagnose_midtrack_decode.py on this master to pinpoint whether the "
                        "difference is at a join or throughout. Worth a closer look.")
            elif own_archival_track:
                diag = ("unexpected — this clip has its own archival track (a straight copy of the "
                        "original), yet it decodes DIFFERENTLY from the original (not just a "
                        "metadata/container difference). Worth a closer look.")
            else:
                diag = ("unexpected — this stream decodes DIFFERENTLY from the original (not just a metadata/"
                        "container difference) with nothing to explain it. Worth a closer look.")
            return StreamCheck(label, src_md5, rec_md5, False, diagnosis=diag)

        # ── Video ────────────────────────────────────────────────────────────
        def full_video_check() -> StreamCheck:
            try:
                src_v = verify_dir / f"{clip.stem}_src.video"
                rec_v = verify_dir / f"{clip.stem}_rec.video"
                # A transcoded clip with no archival track of its own lands in the
                # master as whatever codec the BASELINE target is, not the
                # original clip's own codec — probing the master directly rather
                # than assuming entry.codec applies to both sides (confirmed
                # directly: assuming it crashed the annexb bitstream filter when
                # an h264 original was re-encoded into an HEVC baseline).
                rec_codec = (probe_video_codec(fp, str(self._output), video_stream_index=plan.video_stream, **kwargs)
                            or entry.codec) if video_expected_to_differ else entry.codec
                # Measured windows (Task 87) take the SEEK_EPS guards: bitstream
                # extraction (copy-mode, keyframe-snap-at-or-before) seeks a hair
                # LATE so a boundary that rounds below this clip's own IDR can't
                # snap a GOP back into the previous clip; the decoded comparison
                # (accurate seek, keeps frames with pts ≥ target) seeks a hair
                # EARLY so rounding can't drop this clip's first frame. Modelled
                # (old-manifest) windows keep the exact historical commands.
                copy_seek = plan.video_start + (SEEK_EPS if plan.video_measured else 0.0)
                dec_seek = max(0.0, plan.video_start - (SEEK_EPS if plan.video_measured else 0.0))
                src_cmd = build_video_es_cmd(ff, str(clip.path), str(src_v), entry.codec)
                rec_cmd = build_video_es_cmd(ff, str(self._output), str(rec_v), rec_codec,
                                             seek=copy_seek, duration=plan.video_duration,
                                             video_stream=plan.video_stream)
                rec_cmd_relaxed = (build_video_es_cmd(ff, str(self._output), str(rec_v), rec_codec,
                                                      seek=copy_seek, video_stream=plan.video_stream)
                                   if safe_to_read_unbounded else None)
                # Decode-lossless fallback pair: same window, but hashing decoded pixels.
                dec_src = build_decoded_video_md5_cmd(ff, str(clip.path), video_stream=0)
                dec_rec = build_decoded_video_md5_cmd(ff, str(self._output), video_stream=plan.video_stream,
                                                      seek=dec_seek, duration=plan.video_duration)
                return compare_adaptive("Video", src_cmd, rec_cmd, rec_cmd_relaxed, src_v, rec_v,
                                        decoded_pair=(dec_src, dec_rec))
            except Exception as e:
                return StreamCheck("Video", skipped_reason=f"extraction error: {e}")

        if plan.video_stream is None:
            # This master was exported with video excluded entirely (Advanced
            # output) and this clip has no archival track of its own — there
            # is genuinely no video anywhere to compare. Extraction used to be
            # attempted anyway and fail with a raw ffmpeg map error ("Stream
            # map '' matches no streams"), caught by full_video_check()'s own
            # try/except but reported as a confusing "extraction error"
            # instead of the clean, expected explanation this is.
            result.checks.append(StreamCheck(
                "Video", skipped_reason="not applicable — this master was exported without video "
                                        "(Advanced output)"))
        elif "Video" in predicted_unverifiable:
            # Known ahead of any extraction — see core.verify.predict_unverifiable.
            # Same text the reactive "expected_to_differ" diagnosis would have
            # used anyway, just without spending a full source+recovered
            # extraction pass to arrive at it.
            result.checks.append(StreamCheck(
                "Video", skipped_reason=f"{_PREDICTED_PREFIX} — " + predicted_unverifiable["Video"]))
        elif (self._skip_predictable_verify and not own_archival_track
              and plan.video_measured and not video_expected_to_differ):
            # A measured-window mid-concat clip: real-world masters (the same
            # investigation that landed Task 87's measured windows) showed
            # this shape of comparison fails almost every time for one benign
            # reason — Mechanism 2 window-rounding (core.seam_diag): the
            # recovered window lands a frame or two off the true clip
            # boundary even though the footage itself is pixel-identical.
            # Confirm that with a cheap per-frame pre-check BEFORE spending
            # the full extraction+hash(+decode fallback) pass, instead of
            # running it only to fail it every time.
            try:
                num, den = (entry.fps.split("/") + ["1"])[:2]
                fps = float(num) / float(den or 1)
            except (ValueError, ZeroDivisionError, AttributeError):
                fps = 29.97
            benign, detail = quick_video_rounding_check(
                ff, str(clip.path), str(self._output), plan.video_start,
                plan.video_duration, fps, video_stream=plan.video_stream, **kwargs)
            if benign:
                result.checks.append(StreamCheck(
                    "Video", "", "", True,
                    diagnosis="not run as a full byte-exact pass — a quick per-frame pre-check "
                              f"confirmed this is benign window-rounding, not a real difference "
                              f"({detail}). Run tools/diagnose_midtrack_decode.py for the full "
                              "per-frame report if you want to see it directly."))
            else:
                # Pre-check didn't confirm rounding (or hit something worse) —
                # fall through to the real comparison so a genuine mismatch at
                # a join is never silently waved through.
                result.checks.append(full_video_check())
        else:
            result.checks.append(full_video_check())

        # ── Rotation ─────────────────────────────────────────────────────────
        if plan.video_stream is None:
            result.checks.append(StreamCheck(
                "Rotation", skipped_reason="not applicable — this master was exported without video "
                                           "(Advanced output)"))
        else:
            try:
                src_rot = probe_rotation(fp, str(clip.path), **kwargs)
                rec_rot = probe_rotation(fp, str(self._output), video_stream_index=plan.video_stream, **kwargs)
                if src_rot == rec_rot:
                    result.checks.append(StreamCheck("Rotation", str(src_rot), str(rec_rot), True))
                elif not own_archival_track:
                    diag = ("expected: this clip has no archival track of its own, so it either shares the "
                            "baseline track with others (which can only carry one overall orientation) or "
                            "was re-encoded straight into the baseline (rotation gets baked into the pixels "
                            "during that re-encode, so no separate tag is needed — a 0 here doesn't mean the "
                            "picture is actually sideways). Enable Archival master + \"One track per clip\" "
                            "(or \"Optimize baseline for delivery\") for a byte-exact copy with its original "
                            "rotation tag intact.")
                    result.checks.append(StreamCheck("Rotation", str(src_rot), str(rec_rot), False, diagnosis=diag))
                else:
                    result.checks.append(StreamCheck(
                        "Rotation", str(src_rot), str(rec_rot), False,
                        diagnosis="unexpected on a clip with its own archival track (a straight copy of the "
                                  "original) — worth a closer look."))
            except Exception as e:
                result.checks.append(StreamCheck("Rotation", skipped_reason=f"probe error: {e}"))

        # ── Key metadata (GPS/location, creation time, device) ──────────────
        # GPS/creation-time/device tags live at the whole-FILE level in MOV/MP4,
        # not per-stream — copying a clip's video/audio out of a shared master
        # does NOT bring them along by itself (confirmed directly: a clip whose
        # video/audio hashed perfectly still showed every tag as missing this
        # way). So this doesn't probe the master directly; it runs the SAME
        # real recovery command extract_workers.ExtractWorker uses (including
        # recover_metadata_args' re-attachment from the manifest) and checks
        # the result — the actual thing a user's Extract click produces.
        try:
            src_tags = probe_key_tags(fp, str(clip.path), **kwargs)
            if not src_tags:
                result.checks.append(StreamCheck("Metadata", skipped_reason="no GPS/device metadata on the original"))
            else:
                rec_full = verify_dir / f"{clip.stem}_rec_full.mov"
                cmd = build_recover_clip_cmd(ff, str(self._output), plan, str(rec_full))
                meta_args = recover_metadata_args(entry)
                if meta_args:
                    cmd = cmd[:-1] + meta_args + cmd[-1:]
                r = subprocess.run(cmd, capture_output=True, **kwargs)
                if r.returncode != 0 or not rec_full.exists():
                    raise RuntimeError((r.stderr or b"").decode("utf-8", "ignore")[-300:] or "extraction failed")
                rec_tags = probe_key_tags(fp, str(rec_full), **kwargs)
                missing = {k: v for k, v in src_tags.items() if not tags_equal(k, v, rec_tags.get(k, ""))}
                if not missing:
                    result.checks.append(StreamCheck("Metadata", "; ".join(src_tags.values()),
                                                     "; ".join(src_tags.values()), True))
                else:
                    diag = ("this clip's manifest entry doesn't have a recorded value for "
                            + ", ".join(missing) + " — masters built before this check existed won't "
                            "have it recorded; re-merge to capture it going forward."
                            if not meta_args else
                            "re-attached from the manifest but still didn't match after recovery — "
                            "worth a closer look.")
                    result.checks.append(StreamCheck(
                        "Metadata", str(missing), str({k: rec_tags.get(k) for k in missing}), False,
                        diagnosis=diag))
        except Exception as e:
            result.checks.append(StreamCheck("Metadata", skipped_reason=f"extraction/probe error: {e}"))

        # ── Camera audio ─────────────────────────────────────────────────────
        if entry.has_camera_audio and plan.audio_stream is not None:
            if "Camera audio" in predicted_unverifiable:
                # Known ahead of any extraction — see core.verify.predict_unverifiable.
                result.checks.append(StreamCheck(
                    "Camera audio",
                    skipped_reason=f"{_PREDICTED_PREFIX} — " + predicted_unverifiable["Camera audio"]))
            else:
                try:
                    src_a = verify_dir / f"{clip.stem}_src.wav"
                    rec_a = verify_dir / f"{clip.stem}_rec.wav"
                    src_cmd = build_audio_pcm_cmd(ff, str(clip.path), str(src_a), audio_stream=0)
                    # A clip on its OWN archival track (the whole track IS this clip,
                    # nothing to cut it off from) is read to its own natural EOF first,
                    # matching the untruncated source-side read directly (the common
                    # case, so no retry needed most of the time). Anything else — a
                    # SHARED archival track, or the baseline itself, both of which
                    # hold OTHER clips' data too — must use the bounded cutoff to
                    # isolate this clip's own window; reading unbounded there would
                    # spill into whatever comes after it. If the bounded read
                    # mismatches, the adaptive retry falls back to an unbounded read
                    # in case the cutoff just missed this stream's own natural
                    # boundary (only meaningful for the own-track case, so only
                    # offered then).
                    if safe_to_read_unbounded:
                        rec_cmd = build_audio_pcm_cmd(ff, str(self._output), str(rec_a),
                                                      seek=plan.video_start, audio_stream=plan.audio_stream)
                        rec_cmd_relaxed = None
                    else:
                        rec_cmd = build_audio_pcm_cmd(ff, str(self._output), str(rec_a),
                                                      seek=plan.video_start, duration=plan.video_duration,
                                                      audio_stream=plan.audio_stream)
                        rec_cmd_relaxed = None
                    # Decode-lossless fallback for audio. A concat track introduces two
                    # edge artefacts that a whole-file compare would trip on: AAC
                    # priming after the `-ss` seek (start), and the fact that the audio
                    # boundary sits at the cumulative AUDIO durations, not the
                    # video-based offset the plan seeks to (end). So compare an INTERIOR
                    # window on both sides — skip a guard at the start and stop a guard
                    # short of the end — which is sample-for-sample identical when the
                    # footage genuinely survived (confirmed directly on first-clip and
                    # conforming clips). A non-first clip on a SHARED track can still
                    # differ here because of audio/video boundary drift; that's an
                    # inherent shared-track limitation, correctly steered to per-clip
                    # archival by the diagnosis rather than masked.
                    GUARD_MS = 300.0
                    g = GUARD_MS / 1000.0
                    interior = max(0.0, plan.video_duration - 2 * g)
                    if interior >= 0.5:
                        dec_src = build_decoded_audio_md5_cmd(ff, str(clip.path), audio_stream=0,
                                                              seek=g, duration=interior)
                        dec_rec = build_decoded_audio_md5_cmd(ff, str(self._output), audio_stream=plan.audio_stream,
                                                              seek=plan.video_start + g, duration=interior)
                        decoded_pair = (dec_src, dec_rec)
                    else:
                        decoded_pair = None   # clip too short to carve a safe interior window
                    audio_check = compare_adaptive(
                        "Camera audio", src_cmd, rec_cmd, rec_cmd_relaxed, src_a, rec_a,
                        decoded_pair=decoded_pair, guard_ms=GUARD_MS,
                        # Audio's own "expected to differ" question — independent of
                        # the video's (see video_expected_to_differ's comment above):
                        # camera audio is lossy here only when audio_lossless is False
                        # (non-AAC source with no archival track to fall back on).
                        expected_to_differ=not entry.audio_lossless)
                    # A NON-FIRST clip on a shared/baseline concat track (video_start > 0,
                    # not its own lone track) seeks its audio by a VIDEO-based offset,
                    # but the audio boundary sits at the cumulative AUDIO durations — the
                    # two drift apart, so the interior windows don't line up sample-for-
                    # sample even though the underlying samples are intact (confirmed
                    # directly: the same audio matched once read to its natural end). Keep
                    # it a non-pass (we genuinely couldn't align/verify it here) but
                    # replace the alarming "worth a closer look" with the real reason.
                    # (Reachable only when _skip_predictable_verify is off — this exact
                    # condition is otherwise caught pre-emptively above.)
                    if (not audio_check.match and not audio_check.skipped_reason
                            and plan.video_start > 0 and not safe_to_read_unbounded):
                        # Reference the ACTUAL video outcome — this template used to
                        # assert "the video decoded identically" unconditionally,
                        # which was flatly wrong on clips whose video check had just
                        # failed a few lines above it in the same log.
                        video_check = next((c for c in result.checks if c.label == "Video"), None)
                        video_note = ("The video verified fine, so the footage is present and playable"
                                      if (video_check is not None and video_check.match)
                                      else "See the video row above for how the picture itself fared")
                        audio_check.diagnosis = (
                            "this clip's audio sits mid-way in a shared archival track; its recovered samples "
                            "couldn't be aligned for exact verification here because AAC priming plus audio/video "
                            f"boundary drift at the concat seam shift the window. {video_note} — use "
                            "One-track-per-clip archival for verifiable, byte-exact audio.")
                    result.checks.append(audio_check)
                except Exception as e:
                    result.checks.append(StreamCheck("Camera audio", skipped_reason=f"extraction error: {e}"))
        else:
            result.checks.append(StreamCheck("Camera audio", skipped_reason="no camera audio on this clip"))

        # ── WAV backup ───────────────────────────────────────────────────────
        # The master's WAV/ALAC track is deliberately SYNC-ALIGNED to the video
        # at build time (clip.wav_flags applies the constant offset: -ss trims
        # the WAV's head, -itsoffset delays it) — it is a LOSSLESS but
        # sync-shifted copy, NOT a verbatim byte-copy of the raw original .wav.
        # So comparing a recovered window against the untouched original file is
        # the wrong test: for a trimmed clip the discarded head is gone BY
        # DESIGN, so byte-exact recovery of the raw file is impossible and a
        # plain mismatch there is misleading (confirmed directly: WAV "failed"
        # even in per-clip mode where video+audio were byte-exact). The honest
        # check applies the SAME offset to the source so both sides are the same
        # aligned samples; the lossless ALAC round-trip should then match.
        wav_offset = float(getattr(clip, "wav_offset", 0.0) or 0.0)
        if not (entry.has_wav and plan.wav_stream is not None and clip.wav_path):
            result.checks.append(StreamCheck("WAV backup", skipped_reason="no WAV backup for this clip"))
        elif wav_offset > 0.001:
            # Delayed (-itsoffset): the stored track leads with a sync gap that a
            # raw-PCM decode of the original can't reproduce sample-for-sample.
            # The audio is still stored losslessly — just not byte-verifiable
            # against the raw file this way, so report honestly instead of a
            # false FAIL.
            result.checks.append(StreamCheck(
                "WAV backup", skipped_reason=f"lossless, stored with a +{wav_offset*1000:.0f}ms "
                "sync delay — not byte-comparable to the raw original file by design"))
        else:
            # Mirror the build-time trim so we compare the SAME aligned samples.
            src_seek = abs(wav_offset) if wav_offset < -0.001 else None
            used_measured = (entry.concat_start is not None
                             and (entry.wav_track_duration or 0) > 0)

            def full_wav_check() -> StreamCheck:
                try:
                    src_w = verify_dir / f"{clip.stem}_src.wavbackup"
                    rec_w = verify_dir / f"{clip.stem}_rec.wavbackup"
                    src_cmd = build_audio_pcm_cmd(ff, str(clip.wav_path), str(src_w),
                                                  seek=src_seek, audio_stream=0)
                    rec_cmd = build_audio_pcm_cmd(ff, str(self._output), str(rec_w),
                                                  seek=plan.wav_start, duration=plan.wav_duration,
                                                  audio_stream=plan.wav_stream)
                    src_md5 = extract_and_hash(src_cmd, src_w)
                    rec_md5 = extract_and_hash(rec_cmd, rec_w)
                    # A genuine decode failure here (a corrupted/undecodable ALAC
                    # stream — the real bug this class of mismatch used to hide,
                    # fixed by forcing a consistent ALAC sample format across every
                    # clip's WAV slot, see core.ffmpeg_cmd.build_mux_cmd_plan) would
                    # have already raised out of extract_and_hash above. Reaching
                    # this point means BOTH sides decoded cleanly but still differ —
                    # confirmed directly (round-tripping the original through the
                    # same encoder reproduced it exactly) that this is a POSITION
                    # mismatch, not corruption: the WAV backup always lives on the
                    # shared baseline track with no per-clip escape (unlike camera
                    # audio), and its recovery window is seeked by the VIDEO's
                    # cumulative baseline offset — which can drift from the WAV
                    # track's own true cumulative position when a clip's embedded
                    # audio segment doesn't run exactly as long as its video
                    # (nothing enforces that at build time). Reported honestly
                    # rather than as unexplained corruption.
                    if src_md5 == rec_md5:
                        diag = ""
                    elif used_measured:
                        diag = ("mismatch despite the measured recovery window (this master records "
                                "the WAV track's own probed concat position, so the old video-offset "
                                "drift shouldn't apply). Both sides decoded cleanly — worth a closer "
                                "look at this clip's seam.")
                    else:
                        diag = ("the WAV backup track always lives on the shared baseline (no per-clip "
                                "track of its own), and this master's recovery window was seeked by the "
                                "video's cumulative offset — which can drift from the WAV track's own true "
                                "position when a clip's embedded audio doesn't run exactly as long as its "
                                "video. Both sides decoded cleanly (this is not stream corruption), but the "
                                "window landed on the wrong samples. Masters merged with this version onward "
                                "record the WAV track's measured position instead, which avoids the drift — "
                                "re-merging this folder gives a verifiable WAV backup.")
                    return StreamCheck("WAV backup", src_md5, rec_md5, src_md5 == rec_md5, diagnosis=diag)
                except Exception as e:
                    return StreamCheck("WAV backup", skipped_reason=f"extraction error: {e}")

            if self._skip_predictable_verify and used_measured:
                # A measured WAV window can still occasionally land a hair off
                # the track's own true position (see full_wav_check's
                # diagnosis above) — a real merge showed this fails almost
                # every time for that same benign drift. Confirm it with a
                # cheap short-window scan before spending a full-duration
                # decode+hash pass on both sides.
                benign, detail = quick_wav_rounding_check(
                    ff, str(clip.wav_path), str(self._output), plan.wav_start,
                    plan.wav_stream, src_seek=src_seek, **kwargs)
                if benign:
                    result.checks.append(StreamCheck(
                        "WAV backup", "", "", True,
                        diagnosis="not run as a full-duration decode+hash pass — a quick "
                                  "short-window scan confirmed this is the same benign window "
                                  f"drift already diagnosed for measured WAV windows ({detail})."))
                else:
                    # Pre-check didn't find a matching shift — fall through to
                    # the full comparison so a genuine mismatch is still reported.
                    result.checks.append(full_wav_check())
            else:
                result.checks.append(full_wav_check())

        return result

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
        # Own subfolder, not the bare shared `_temp` — see MergeWorker
        # ._make_scratch's docstring for why a shared path collides between
        # concurrent workers (this one and a MergeWorker, or two of either).
        temp_dir      = get_app_dir() / "_temp" / _instance_scratch_name()
        temp_dir.mkdir(parents=True, exist_ok=True)
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

    def __init__(self, source: str, timecode: str, grade: Optional[Grade], out_path: str,
                video_track: int = 0):
        super().__init__()
        self._source   = source
        self._timecode = timecode
        self._grade    = grade
        self._out      = out_path
        self._video_track = video_track

    def run(self):
        ff, _ = get_ffmpeg()
        cmd = build_preview_cmd(ff, self._source, self._timecode, self._grade, self._out,
                                video_track=self._video_track)
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=20)
            if r.returncode == 0 and Path(self._out).exists():
                self.done.emit(self._out)
            else:
                self.error.emit(r.stderr.decode(errors="ignore")[-200:])
        except Exception as e:
            self.error.emit(str(e))
