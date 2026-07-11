"""md5_matrix_test.py — Autonomous MD5-recovery regression matrix.

Drives the REAL Merge tab (headless/offscreen) through a matrix of the
settings that affect whether a clip is bit-exact recoverable — Archival
master / One track per clip / Optimize baseline for delivery — with MD5
verification on, against a real source folder. Every run's result is
appended to results.jsonl immediately (safe to inspect mid-run or resume
analysis if the process is interrupted); a human-readable summary.txt is
regenerated after each run too. Progress (with an ETA based on the average
per-test duration so far) is written to progress.json after each test.

Passing runs delete their (large) master afterward to save disk space —
the verify.log/manifest/restore-log survive either way. FAILING runs keep
every file for analysis.

Quality presets don't affect bit-exactness (archival tracks are never
touched by the quality setting — only the baseline's own delivery encode
is), so the matrix only varies Archival/OTPC/Optimize; when Optimize is on,
a single representative preset (default: youtube) is used throughout.

Usage:
    python md5_matrix_test.py <source_folder> <work_dir> [--tag NAME] [--baseline-index N]

Standalone (not pytest) — run directly with the project's own Python.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Some dialog text the app produces (e.g. a "⚠" warning glyph) isn't
# representable in Windows' legacy cp1252 console codepage — confirmed
# directly: that raised UnicodeEncodeError out of a print() called from
# inside a Qt slot (_on_verification_done), which killed the test process
# mid-run rather than just mangling a log line. Force UTF-8 with lossy
# fallback so no console-encoding limitation can ever crash a test.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# The Qt/QApplication stack is only needed by the actual per-test worker
# (run_one, invoked via --single in a fresh subprocess). The subprocess-
# launching orchestrator (run_matrix, in the parent process) never touches
# Qt at all, so importing it here unconditionally would pointlessly spin up
# a QApplication in the long-lived parent — deferred into _init_qt() instead.
app = None
mt_mod = None
MergeTab = None
_CameraNamingDialog = None
Settings = None


def _init_qt():
    global app, mt_mod, MergeTab, _CameraNamingDialog, Settings
    if app is not None:
        return
    from PySide6.QtWidgets import QApplication, QMessageBox
    app = QApplication.instance() or QApplication([])

    import theme  # noqa: F401
    from settings import Settings as _Settings
    import merge_tab as _mt_mod
    from merge_tab import MergeTab as _MergeTab, _CameraNamingDialog as _CND
    Settings = _Settings
    mt_mod = _mt_mod
    MergeTab = _MergeTab
    _CameraNamingDialog = _CND

    # Headless-safety: no modal dialog should ever block this process. Every
    # QMessageBox classmethod the Merge tab can call is neutralised to a safe,
    # non-blocking default (Yes/OK — never "cancel the operation").
    _CameraNamingDialog.exec = lambda self: 0

    def _safe_dialog_print(label, a):
        try:
            print(label, a[1:3] if len(a) > 2 else a)
        except Exception:
            pass   # a print() failure must never propagate out of a Qt slot and stall the run

    mt_mod.QMessageBox.warning = staticmethod(lambda *a, **k: _safe_dialog_print("  [dialog:warning]", a))
    mt_mod.QMessageBox.information = staticmethod(lambda *a, **k: _safe_dialog_print("  [dialog:info]", a))
    mt_mod.QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    # _on_finished's "Merge complete!" dialog builds a raw QMessageBox instance
    # and calls .exec() directly, NOT one of the classmethod shortcuts above —
    # confirmed as a real gap this way: every SUCCESSFUL headless merge hung
    # here indefinitely (a modal exec() blocks even with no display to click),
    # invisible until something finally killed the process, at which point it
    # was mis-recorded as a timeout rather than the pass it actually was.
    mt_mod.QMessageBox.exec = lambda self: 0

PER_TEST_TIMEOUT_S = int(os.environ.get("MD5_PER_TEST_TIMEOUT_S", "1200"))
                            # 20 min default hard cap per merge — a hang here must not stall the
                            # whole matrix. Env-overridable because the heaviest config (per-clip
                            # archival of several 4K clips) genuinely needs longer than the light
                            # ones; raise MD5_PER_TEST_TIMEOUT_S when running just that one.
LOAD_TIMEOUT_S = 90

# (archival, per_clip_archival, optimize_baseline, quality_preset_or_None)
MATRIX = [
    ("baseline_only",     False, False, False, None),
    ("archival_shared",   True,  False, False, None),
    ("archival_percpip",  True,  True,  False, None),
    ("optimize_youtube",  True,  True,  True,  "youtube"),
]


def _wait_for(condition, timeout_s, tick=0.05):
    t0 = time.time()
    while not condition() and time.time() - t0 < timeout_s:
        app.processEvents()
        time.sleep(tick)
    for _ in range(20):   # grace period for the last queued cross-thread signal
        app.processEvents()
        time.sleep(0.02)
    return condition()


def run_one(source_folder: Path, work_dir: Path, test_id: str,
           archival: bool, per_clip: bool, optimize: bool, quality_preset,
           baseline_index: int = 0) -> dict:
    _init_qt()
    settings = Settings()
    result = {
        "test_id": test_id, "archival": archival, "per_clip_archival": per_clip,
        "optimize_baseline": optimize, "quality_preset": quality_preset,
        "baseline_index": baseline_index, "source_folder": str(source_folder),
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    mt = None
    try:
        mt = MergeTab(settings)
        mt.show()
        mt._load_folder(source_folder)
        loaded = _wait_for(lambda: mt._clips and all(c.stream is not None for c in mt._clips),
                           LOAD_TIMEOUT_S)
        if not loaded:
            result.update(status="error", error="clips never finished probing")
            return result
        result["clip_count"] = len(mt._clips)

        if mt._spec_groups and baseline_index < len(mt._spec_groups):
            mt._on_baseline_chosen(mt._spec_groups[baseline_index])
        for _ in range(10):
            app.processEvents()
            time.sleep(0.02)

        mt._archival_check.setChecked(archival)
        mt._per_clip_archival_check.setChecked(per_clip)
        mt._optimize_baseline_check.setChecked(optimize)
        mt._verify_md5_check.setChecked(True)
        if optimize and quality_preset in mt._quality_radios:
            mt._quality_radios[quality_preset].setChecked(True)
        for _ in range(10):
            app.processEvents()
            time.sleep(0.02)
        # record what actually took effect after the dependency cascade
        result["effective"] = {
            "archival": mt._archival_check.isChecked(),
            "per_clip_archival": mt._per_clip_archival_check.isChecked(),
            "optimize_baseline": mt._optimize_baseline_check.isEnabled() and mt._optimize_baseline_check.isChecked(),
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
            result.update(status="error", error="_start_merge did not create a worker "
                                                 "(no clips selected / no output folder?)")
            return result

        done = {}
        verify = {}
        mt._worker.finished.connect(lambda ok, msg: done.update(finished=True, success=ok, message=msg))
        mt._worker.verification_done.connect(
            lambda ok, summary, path: verify.update(all_passed=ok, summary=summary, report_path=path))

        finished = _wait_for(lambda: done.get("finished", False), PER_TEST_TIMEOUT_S, tick=0.2)
        result["merge_seconds"] = round(time.time() - t_merge0, 1)

        if not finished:
            result.update(status="timeout", error=f"no finished signal within {PER_TEST_TIMEOUT_S}s")
            return result

        result["merge_success"] = done.get("success")
        result["merge_message"] = done.get("message")
        result["verify_ran"] = bool(verify)
        result["verify_all_passed"] = verify.get("all_passed")
        result["verify_summary"] = verify.get("summary")
        result["report_path"] = verify.get("report_path")

        if not done.get("success"):
            result["status"] = "merge_failed"
        elif verify and not verify.get("all_passed"):
            result["status"] = "md5_mismatch"
        elif not verify:
            result["status"] = "no_verification_ran"
        else:
            result["status"] = "pass"

        # Preserve the report/manifest/log always; delete only the (large) master
        # on a clean pass to save disk space.
        if result["status"] == "pass":
            try:
                (out_dir / f"{test_id}.mov").unlink(missing_ok=True)
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


def _write_progress(progress_path: Path, done_n: int, total_n: int, durations: list, current: str):
    avg = (sum(durations) / len(durations)) if durations else 0
    remaining = total_n - done_n
    eta_s = avg * remaining
    payload = {
        "done": done_n, "total": total_n,
        "percent": round(100 * done_n / max(1, total_n), 1),
        "current_test": current,
        "avg_seconds_per_test": round(avg, 1),
        "eta_seconds": round(eta_s, 1),
        "eta_human": time.strftime("%Hh%Mm", time.gmtime(eta_s)) if eta_s else "n/a",
        "updated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    progress_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_summary(summary_path: Path, results: list):
    lines = [f"MD5 recovery matrix — {len(results)} test(s) run", ""]
    for r in results:
        eff = r.get("effective", {})
        lines.append(f"[{r.get('status', '?').upper():16s}] {r.get('test_id', '?'):20s} "
                     f"archival={eff.get('archival', r.get('archival'))!s:5} "
                     f"otpc={eff.get('per_clip_archival', r.get('per_clip_archival'))!s:5} "
                     f"optimize={eff.get('optimize_baseline', r.get('optimize_baseline'))!s:5} "
                     f"quality={r.get('quality_preset')}")
        if r.get("verify_summary"):
            lines.append(f"                   {r['verify_summary']}")
        if r.get("error"):
            lines.append(f"                   ERROR: {r['error'][:300]}")
        if r.get("report_path"):
            lines.append(f"                   report: {r['report_path']}")
    summary_path.write_text("\n".join(lines), encoding="utf-8")


def run_matrix(source_folder: Path, work_dir: Path, tag: str, baseline_index: int = 0,
              matrix=None, per_test_timeout_s: int = None) -> list:
    """Runs each MATRIX config as an ISOLATED subprocess (this same script,
    invoked with --single) rather than looping run_one() calls inside this
    process. Confirmed repeatedly this session: a persistent QApplication
    that drives multiple sequential MergeTab/MergeWorker lifecycles hangs
    after a test's real work (merge + verify) finishes, before the next test
    can start — the work itself was always correct on disk, only this
    process's own cleanup got stuck. A fresh subprocess per test sidesteps
    that entirely: whatever state got wedged just dies with the subprocess,
    and this parent (which never touches Qt — see _init_qt()) can't inherit
    the hang."""
    matrix = matrix or MATRIX
    # Grace window beyond the merge's own hard cap, only to bound the known
    # post-completion Qt-teardown hang (see module docstring) — kept short
    # since real work reliably finishes in minutes, not the full cap.
    per_test_timeout_s = per_test_timeout_s or (PER_TEST_TIMEOUT_S + 120)
    work_dir.mkdir(parents=True, exist_ok=True)
    results_path = work_dir / f"results_{tag}.jsonl"
    summary_path = work_dir / f"summary_{tag}.txt"
    progress_path = work_dir / f"progress_{tag}.json"

    results = []
    durations = []
    total = len(matrix)
    print(f"Starting matrix '{tag}': {total} tests against {source_folder}")
    for i, (test_id, archival, per_clip, optimize, quality) in enumerate(matrix):
        full_id = f"{tag}_{test_id}"
        print(f"\n[{i+1}/{total}] {full_id}  "
             f"(archival={archival} otpc={per_clip} optimize={optimize} quality={quality})", flush=True)
        _write_progress(progress_path, i, total, durations, full_id)
        t0 = time.time()

        out_path = work_dir / f"_single_{full_id}.json"
        out_path.unlink(missing_ok=True)
        cmd = [
            sys.executable, str(Path(__file__).resolve()), str(source_folder), str(work_dir),
            "--single", "--test-id", full_id,
            "--archival", "1" if archival else "0",
            "--per-clip", "1" if per_clip else "0",
            "--optimize", "1" if optimize else "0",
            "--quality", quality or "",
            "--baseline-index", str(baseline_index),
            "--out", str(out_path),
        ]
        # Base fields every result needs regardless of how the subprocess
        # exited — _write_summary() reads these directly (not .get()), so a
        # thinner fallback dict on a timeout/no-output-file path would crash
        # summary writing instead of just recording the failure (confirmed
        # directly: exactly this happened on the first live run).
        base_fields = dict(test_id=full_id, archival=archival, per_clip_archival=per_clip,
                          optimize_baseline=optimize, quality_preset=quality,
                          baseline_index=baseline_index, source_folder=str(source_folder))
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=per_test_timeout_s)
            if out_path.exists():
                r = json.loads(out_path.read_text(encoding="utf-8"))
            else:
                r = {
                    **base_fields, "status": "exception",
                    "error": f"subprocess produced no result file (exit {proc.returncode})\n"
                             f"stdout tail: {proc.stdout[-1500:]}\nstderr tail: {proc.stderr[-1500:]}",
                }
        except subprocess.TimeoutExpired:
            r = {**base_fields, "status": "timeout",
                 "error": f"subprocess did not finish within {per_test_timeout_s}s"}
        finally:
            out_path.unlink(missing_ok=True)

        dt = time.time() - t0
        durations.append(dt)
        results.append(r)
        with open(results_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(r) + "\n")
        _write_summary(summary_path, results)
        _write_progress(progress_path, i + 1, total, durations, full_id)
        print(f"  -> {r.get('status')}  ({dt:.1f}s)  {r.get('verify_summary') or r.get('error') or ''}", flush=True)

    print(f"\nMatrix '{tag}' complete. Summary: {summary_path}")
    return results


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("source_folder")
    ap.add_argument("work_dir")
    ap.add_argument("--tag", default="matrix")
    ap.add_argument("--baseline-index", type=int, default=0)
    # --single mode: run exactly ONE config in THIS process and exit — this is
    # what run_matrix() shells out to per test, to keep every merge's Qt
    # lifecycle in its own throwaway process.
    ap.add_argument("--single", action="store_true")
    ap.add_argument("--test-id")
    ap.add_argument("--archival")
    ap.add_argument("--per-clip")
    ap.add_argument("--optimize")
    ap.add_argument("--quality")
    ap.add_argument("--out")
    args = ap.parse_args()

    if args.single:
        result = run_one(
            Path(args.source_folder), Path(args.work_dir), args.test_id,
            args.archival == "1", args.per_clip == "1", args.optimize == "1",
            args.quality or None, args.baseline_index,
        )
        Path(args.out).write_text(json.dumps(result), encoding="utf-8")
        sys.exit(0 if result.get("status") == "pass" else 1)
    else:
        run_matrix(Path(args.source_folder), Path(args.work_dir), args.tag, args.baseline_index)
