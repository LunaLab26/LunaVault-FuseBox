"""md5_matrix_test_ext.py — Battle-test extension of md5_matrix_test.py.

Adds the axes md5_matrix_test.py's MATRIX doesn't cover: hw_decode, hw_encoder,
output track plan (camera/wav/mix), fill mode (black/blur), square/crop mode.
Reuses md5_matrix_test's Qt bootstrap, MergeTab driving, and subprocess-per-test
isolation pattern verbatim rather than reinventing it — see that module's
docstring for why each test runs in its own subprocess.

Standalone (not pytest) — run directly with the project's own Python:
    python md5_matrix_test_ext.py <source_folder> <work_dir> --cells cells.json --tag NAME
"cells.json" is a JSON list of cell dicts (see CellSpec fields below); this
script's own CLI can also generate the built-in cell sets with --preset.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

import md5_matrix_test as base  # noqa: E402

PER_TEST_TIMEOUT_S = int(os.environ.get("MD5_PER_TEST_TIMEOUT_S", "900"))
LOAD_TIMEOUT_S = 90

# Default field values for a cell — every key here can be overridden per-cell.
CELL_DEFAULTS = dict(
    archival=False, per_clip=False, optimize=False, quality=None,
    compat_baseline=False, compat_codec="h264", compat_prores_profile="hq",
    hw_decode="off", hw_encoder="off",
    track_plan="preset_camera",   # preset_camera | preset_wav | camera_only | wav_only | cam_wav_mix_lr | cam_wav_mix_5050
    fill="black", square_mode="crop",
    baseline_index=0, verify_md5=True,
)


def run_one_ext(source_folder: Path, work_dir: Path, test_id: str, cell: dict) -> dict:
    base._init_qt()
    app, mt_mod, MergeTab, Settings = base.app, base.mt_mod, base.MergeTab, base.Settings
    c = dict(CELL_DEFAULTS)
    c.update(cell)
    settings = Settings()
    result = {"test_id": test_id, "cell": c, "source_folder": str(source_folder),
              "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    mt = None
    try:
        mt = MergeTab(settings)
        mt.show()

        # Force the hw pipeline deterministically rather than waiting on the
        # async GPU probe thread / relying on "Recommended" mode's own logic
        # (see merge_tab._resolve_pipeline) — a battle test needs every one of
        # the 4 decode x encode combinations, including ones "Recommended"
        # mode would never itself pick.
        mt._settings.set("merge_pipeline_recommended", False)
        mt._settings.set("merge_decode_method", "hardware" if c["hw_decode"] != "off" else "software")
        mt._settings.set("merge_encode_method", "hardware" if c["hw_encoder"] != "off" else "software")
        mt._gpu_vendors = ["vaapi"] if (c["hw_decode"] != "off" or c["hw_encoder"] != "off") else []

        mt._load_folder(source_folder)
        loaded = base._wait_for(lambda: mt._clips and all(cl.stream is not None for cl in mt._clips),
                                LOAD_TIMEOUT_S)
        if not loaded:
            result.update(status="error", error="clips never finished probing")
            return result
        result["clip_count"] = len(mt._clips)

        if mt._spec_groups and c["baseline_index"] < len(mt._spec_groups):
            mt._on_baseline_chosen(mt._spec_groups[c["baseline_index"]])
        for _i in range(10):
            app.processEvents(); time.sleep(0.02)

        mt._archival_check.setChecked(c["archival"])
        mt._per_clip_archival_check.setChecked(c["per_clip"])
        mt._optimize_baseline_check.setChecked(c["optimize"])
        mt._verify_md5_check.setChecked(c["verify_md5"])
        if c["optimize"] and c["quality"] in mt._quality_radios:
            mt._quality_radios[c["quality"]].setChecked(True)
        mt._compat_baseline_check.setChecked(c["compat_baseline"])
        if c["compat_baseline"]:
            if c["compat_codec"] == "prores":
                mt._compat_codec_prores_radio.setChecked(True)
                mt._prores_profile_radios[c["compat_prores_profile"]].setChecked(True)
            else:
                mt._compat_codec_h264_radio.setChecked(True)

        # Fill / square-crop combos
        mt._fill_combo.setCurrentIndex(1 if c["fill"] == "blur" else 0)
        mt._square_combo.setCurrentIndex(0 if c["square_mode"] == "crop" else 1)

        # Track plan
        from core.ffmpeg_cmd import OutputTrack
        tp = c["track_plan"]
        if tp == "preset_wav":
            mt._track_combo.setCurrentIndex(1)
            mt._custom_tracks = None
        elif tp == "preset_camera":
            mt._track_combo.setCurrentIndex(0)
            mt._custom_tracks = None
        elif tp == "camera_only":
            mt._custom_tracks = [OutputTrack("camera", True), OutputTrack("wav", False)]
        elif tp == "wav_only":
            mt._custom_tracks = [OutputTrack("camera", False), OutputTrack("wav", True)]
        elif tp == "cam_wav_mix_lr":
            mt._custom_tracks = [OutputTrack("camera", True), OutputTrack("wav", True), OutputTrack("mix", True)]
            mt._mix_kind_combo.setCurrentIndex(0)
        elif tp == "cam_wav_mix_5050":
            mt._custom_tracks = [OutputTrack("camera", True), OutputTrack("wav", True), OutputTrack("mix", True)]
            mt._mix_kind_combo.setCurrentIndex(1)
        for _ in range(10):
            app.processEvents(); time.sleep(0.02)

        result["effective"] = {
            "archival": mt._archival_check.isChecked(),
            "per_clip_archival": mt._per_clip_archival_check.isChecked(),
            "optimize_baseline": mt._optimize_baseline_check.isEnabled() and mt._optimize_baseline_check.isChecked(),
            "compat_baseline": mt._compat_baseline_check.isChecked(),
            "compat_codec": getattr(mt, "compat_codec", "h264"),
            "compat_prores_profile": getattr(mt, "compat_prores_profile", "hq"),
            "hw_decode_resolved": mt._resolve_pipeline()[0],
            "hw_encoder_resolved": mt._resolve_pipeline()[1],
        }

        out_dir = work_dir / test_id
        if out_dir.exists():
            shutil.rmtree(out_dir, ignore_errors=True)
        out_dir.mkdir(parents=True)
        mt._out_dir.setText(str(out_dir))
        mt._out_name.setText(f"{test_id}.mov")

        t_merge0 = time.time()
        mt._start_merge()
        if mt._worker is None:
            result.update(status="error", error="_start_merge did not create a worker")
            return result

        done = {}
        verify = {}
        mt._worker.finished.connect(lambda ok, msg: done.update(finished=True, success=ok, message=msg))
        mt._worker.verification_done.connect(
            lambda ok, summary, path: verify.update(all_passed=ok, summary=summary, report_path=path))

        finished = base._wait_for(lambda: done.get("finished", False), PER_TEST_TIMEOUT_S, tick=0.2)
        result["merge_seconds"] = round(time.time() - t_merge0, 1)

        out_file = out_dir / f"{test_id}.mov"
        verify_log = out_dir / f"{test_id}.verify.log"
        restore_log = out_dir / f"{test_id}.restore.log"

        if not finished:
            # Known Qt-teardown quirk (see md5_matrix_test.py's own run_matrix
            # docstring: "hangs after a test's real work finishes... the work
            # itself was always correct on disk, only this process's own
            # cleanup got stuck") — the `finished` signal can fail to be
            # observed here even though the worker already wrote every real
            # output file. Fall back to reading those files directly rather
            # than reporting a bare timeout when the real work plainly
            # completed (matches the battle-test brief's own instruction to
            # trust on-disk evidence over a wrapper's status line).
            file_settled = base._wait_for(
                lambda: out_file.exists() and restore_log.exists()
                and (verify_log.exists() if c["verify_md5"] else True), 15, tick=0.5)
            if not file_settled:
                result.update(status="timeout", error=f"no finished signal within {PER_TEST_TIMEOUT_S}s, "
                                                       "and expected output files never appeared either")
                return result
            result["merge_success"] = True
            result["signal_timeout_fallback"] = True
            if c["verify_md5"] and verify_log.exists():
                vlog = verify_log.read_text(encoding="utf-8", errors="replace")
                m = re.search(r"Result: (\d+) / (\d+) clips verified byte-identical", vlog)
                all_passed = bool(m and m.group(1) == m.group(2))
                result["verify_ran"] = True
                result["verify_all_passed"] = all_passed
                result["verify_summary"] = vlog.splitlines()[1] if len(vlog.splitlines()) > 1 else vlog[:200]
                result["report_path"] = str(verify_log)
            else:
                result["verify_ran"] = False
        else:
            result["merge_success"] = done.get("success")
            result["merge_message"] = done.get("message")
            result["verify_ran"] = bool(verify)
            result["verify_all_passed"] = verify.get("all_passed")
            result["verify_summary"] = verify.get("summary")
            result["report_path"] = verify.get("report_path")

        result["output_exists"] = out_file.exists()
        result["output_size"] = out_file.stat().st_size if out_file.exists() else 0

        if not result.get("merge_success"):
            result["status"] = "merge_failed"
        elif c["verify_md5"] and result.get("verify_ran") and not result.get("verify_all_passed"):
            result["status"] = "md5_mismatch"
        elif c["verify_md5"] and not result.get("verify_ran"):
            result["status"] = "no_verification_ran"
        else:
            result["status"] = "pass"

        if result["status"] == "pass" and c["verify_md5"]:
            try:
                out_file.unlink(missing_ok=True)
            except Exception:
                pass
        result["output_dir"] = str(out_dir)
        return result
    except Exception as e:
        result.update(status="exception", error=f"{e}\n{traceback.format_exc()[-2000:]}")
        return result
    finally:
        if mt is not None:
            try:
                mt.shutdown()
            except Exception:
                pass
            mt.deleteLater()


def _write_progress(progress_path, done_n, total_n, durations, current):
    avg = (sum(durations) / len(durations)) if durations else 0
    remaining = total_n - done_n
    eta_s = avg * remaining
    payload = {"done": done_n, "total": total_n, "percent": round(100 * done_n / max(1, total_n), 1),
               "current_test": current, "avg_seconds_per_test": round(avg, 1),
               "eta_seconds": round(eta_s, 1), "eta_human": time.strftime("%Hh%Mm", time.gmtime(eta_s)) if eta_s else "n/a",
               "updated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    progress_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def run_matrix(source_folder: Path, work_dir: Path, tag: str, cells: list, per_test_timeout_s=None):
    per_test_timeout_s = per_test_timeout_s or (PER_TEST_TIMEOUT_S + 120)
    work_dir.mkdir(parents=True, exist_ok=True)
    results_path = work_dir / f"results_{tag}.jsonl"
    progress_path = work_dir / f"progress_{tag}.json"
    results = []
    durations = []
    total = len(cells)
    print(f"Starting ext matrix '{tag}': {total} cells against {source_folder}", flush=True)
    for i, cell in enumerate(cells):
        test_id = f"{tag}_{cell['name']}"
        print(f"\n[{i+1}/{total}] {test_id}  {cell}", flush=True)
        _write_progress(progress_path, i, total, durations, test_id)
        t0 = time.time()
        out_path = work_dir / f"_single_{test_id}.json"
        out_path.unlink(missing_ok=True)
        cell_path = work_dir / f"_cell_{test_id}.json"
        cell_path.write_text(json.dumps(cell), encoding="utf-8")
        cmd = [sys.executable, str(Path(__file__).resolve()), str(source_folder), str(work_dir),
              "--single", "--test-id", test_id, "--cell-file", str(cell_path), "--out", str(out_path)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=per_test_timeout_s)
            if out_path.exists():
                r = json.loads(out_path.read_text(encoding="utf-8"))
            else:
                r = {"test_id": test_id, "cell": cell, "status": "exception",
                     "error": f"subprocess produced no result file (exit {proc.returncode})\n"
                              f"stdout tail: {proc.stdout[-1500:]}\nstderr tail: {proc.stderr[-1500:]}"}
        except subprocess.TimeoutExpired:
            r = {"test_id": test_id, "cell": cell, "status": "timeout",
                 "error": f"subprocess did not finish within {per_test_timeout_s}s"}
        finally:
            out_path.unlink(missing_ok=True)
            cell_path.unlink(missing_ok=True)
            # A timed-out/killed subprocess's own ffmpeg child can outlive it
            # (MergeWorker's cancellation only takes effect between steps, not
            # inside a single already-running ffmpeg call) — confirmed directly
            # this session: a stale ffmpeg from a killed cell was still
            # writing into the SAME shared _temp/ scratch dir as the NEXT
            # cell's own fresh ffmpeg, a real cross-cell contamination risk.
            # Belt-and-suspenders cleanup between every cell, not just on the
            # timeout path, since we can't be sure the signal-timeout fallback
            # always wins the race either.
            try:
                temp_dir_pattern = str((Path(__file__).resolve().parents[1] / "_temp").resolve())
                subprocess.run(["pkill", "-9", "-f", temp_dir_pattern], capture_output=True, timeout=5)
            except Exception:
                pass
        dt = time.time() - t0
        durations.append(dt)
        results.append(r)
        with open(results_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(r) + "\n")
        _write_progress(progress_path, i + 1, total, durations, test_id)
        print(f"  -> {r.get('status')}  ({dt:.1f}s)  {r.get('verify_summary') or r.get('error') or ''}", flush=True)
    print(f"\nExt matrix '{tag}' complete.", flush=True)
    return results


def build_hirisk_cells():
    """True full product: hw_decode x hw_encoder x archival x compat (2x2x3x5=60)."""
    cells = []
    for hwd in ("off", "auto"):
        for hwe in ("off", "auto"):
            for arch_name, archival, per_clip in (("archoff", False, False), ("archshared", True, False), ("archpercpip", True, True)):
                for compat_name, compat_baseline, compat_codec, prores_profile in (
                    ("compatoff", False, "h264", "hq"),
                    ("compath264", True, "h264", "hq"),
                    ("compatproresproxy", True, "prores", "proxy"),
                    ("compatproresstd", True, "prores", "standard"),
                    ("compatproreshq", True, "prores", "hq"),
                ):
                    name = f"d{hwd}_e{hwe}_{arch_name}_{compat_name}"
                    cells.append(dict(name=name, hw_decode=hwd, hw_encoder=hwe,
                                      archival=archival, per_clip=per_clip,
                                      compat_baseline=compat_baseline, compat_codec=compat_codec,
                                      compat_prores_profile=prores_profile, verify_md5=True))
    return cells


def build_lowrisk_cells():
    cells = []
    # Track plan x4 (representative: archival shared, hw off)
    for name, tp in (("trackplan_camera_only", "camera_only"), ("trackplan_wav_only", "wav_only"),
                     ("trackplan_mix_lr", "cam_wav_mix_lr"), ("trackplan_mix_5050", "cam_wav_mix_5050")):
        cells.append(dict(name=name, archival=True, per_clip=False, track_plan=tp, verify_md5=True))
    # Quality presets x4 (need optimize+archival+per_clip)
    for preset in ("archival", "master", "youtube", "social"):
        cells.append(dict(name=f"quality_{preset}", archival=True, per_clip=True, optimize=True,
                          quality=preset, verify_md5=True))
    return cells


def build_aspect_cells():
    cells = []
    for fill in ("black", "blur"):
        cells.append(dict(name=f"fill_{fill}", archival=True, fill=fill, verify_md5=True))
    for sq in ("crop", "pad"):
        cells.append(dict(name=f"square_{sq}", archival=True, square_mode=sq, verify_md5=True))
    return cells


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("source_folder")
    ap.add_argument("work_dir")
    ap.add_argument("--tag", default="ext")
    ap.add_argument("--preset", choices=["hirisk", "lowrisk", "aspect"], default=None)
    ap.add_argument("--baseline-index", type=int, default=0)
    ap.add_argument("--single", action="store_true")
    ap.add_argument("--test-id")
    ap.add_argument("--cell-file")
    ap.add_argument("--out")
    args = ap.parse_args()

    if args.single:
        cell = json.loads(Path(args.cell_file).read_text(encoding="utf-8"))
        result = run_one_ext(Path(args.source_folder), Path(args.work_dir), args.test_id, cell)
        Path(args.out).write_text(json.dumps(result), encoding="utf-8")
        sys.exit(0 if result.get("status") == "pass" else 1)
    else:
        if args.preset == "hirisk":
            cells = build_hirisk_cells()
        elif args.preset == "lowrisk":
            cells = build_lowrisk_cells()
        elif args.preset == "aspect":
            cells = build_aspect_cells()
        else:
            print("must pass --preset when not --single"); sys.exit(2)
        for c in cells:
            c.setdefault("baseline_index", args.baseline_index)
        run_matrix(Path(args.source_folder), Path(args.work_dir), args.tag, cells)
