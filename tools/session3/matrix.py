"""Encoding-integrity matrix: drive the REAL Merge tab headlessly over test sets x settings,
then independently verify every output (full decode, stream layout, durations, A/V sync).

usage:
  matrix.py run <repo> <workdir> [filter]     orchestrate (each merge in a fresh subprocess)
  matrix.py single <repo> <workdir> <id>      one merge (internal)
"""
import os
FF = os.environ.get("LV_FFMPEG", "ffmpeg")
FP = os.environ.get("LV_FFPROBE", "ffprobe")
import json, os, shutil, subprocess, sys, time, traceback
from pathlib import Path

NIGHT = Path(os.environ.get("LV_WORK", Path(__file__).resolve().parents[2] / "_session3"))
SETS = NIGHT / "sets"
TOOLS = Path(__file__).resolve().parent

# id, set, options
CASES = [
    ("R1_default",            "R1_luna",           {}),
    ("R1_archival_md5",       "R1_luna",           {"archival": True, "verify": True}),
    ("R2_default",            "R2_cattle_mixed",   {}),
    ("R2_baseline_1080",      "R2_cattle_mixed",   {"baseline": "1920x1080"}),
    ("R2_archival_perclip",   "R2_cattle_mixed",   {"archival": True, "per_clip": True, "verify": True}),
    ("R2_compat_h264",        "R2_cattle_mixed",   {"compat": "h264"}),
    ("R2_blurfill",           "R2_cattle_mixed",   {"fill": "blur", "baseline": "1920x1080"}),
    ("R3_default",            "R3_multicam_july",  {}),
    ("R3_optimize_youtube",   "R3_multicam_july",  {"archival": True, "per_clip": True, "optimize": "youtube", "verify": True}),
    ("R3_compat_prores",      "R3_multicam_july",  {"compat": "prores:proxy"}),
    ("SYNC_default",          "SYNC",              {}),
    ("SYNC_baseline_1080p60", "SYNC",              {"baseline": "1920x1080@60"}),
    ("SYNC_baseline_720p25",  "SYNC",              {"baseline": "1280x720"}),
    ("SYNC_compat_h264",      "SYNC",              {"compat": "h264"}),
    ("SYNC_archival_perclip", "SYNC",              {"archival": True, "per_clip": True, "verify": True}),
    ("B_edge_default",        "B_mixed",           {}),
    ("SYNCW_default",         "SYNC_W",            {}),
]


def _set_path(name):
    return SETS / name


# ---------------------------------------------------------------- single merge (Qt)
def single(repo: Path, work: Path, case_id: str):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import faulthandler
    faulthandler.dump_traceback_later(int(os.environ.get("DUMP_S","1500")), repeat=True, file=sys.stderr)
    sys.path.insert(0, str(repo / "src"))
    from PySide6.QtWidgets import QApplication, QMessageBox
    app = QApplication.instance() or QApplication([])
    import theme  # noqa
    from settings import Settings
    import merge_tab as mt_mod
    import ffmpeg_runner as fr_mod, subprocess as _sp
    _cmdlog = open(work / f"{case_id}.cmds.txt", "w", encoding="utf-8")
    _P = _sp.Popen
    class _LogPopen(_P):
        def __init__(self, args, *a, **k):
            if isinstance(args, (list, tuple)) and args and "ff" in str(args[0]):
                _cmdlog.write(" ".join(map(str, args)) + "\n"); _cmdlog.flush()
            super().__init__(args, *a, **k)
    fr_mod.subprocess.Popen = _LogPopen
    mt_mod._CameraNamingDialog.exec = lambda self: 0
    mt_mod.QMessageBox.warning = staticmethod(lambda *a, **k: print("[dialog:warning]", a[1:3]))
    mt_mod.QMessageBox.information = staticmethod(lambda *a, **k: print("[dialog:info]", a[1:3]))
    mt_mod.QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    mt_mod.QMessageBox.exec = lambda self: 0

    def pump(sec=0.3):
        t = time.time()
        while time.time() - t < sec:
            app.processEvents(); time.sleep(0.02)

    def wait(cond, timeout, tick=0.1):
        t = time.time()
        while not cond() and time.time() - t < timeout:
            app.processEvents(); time.sleep(tick)
        pump(0.4)
        return cond()

    cid, set_name, opt = next(c for c in CASES if c[0] == case_id)
    res = {"id": cid, "set": set_name, "opt": opt}
    out_dir = work / cid
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    done, ver = {}, {}
    _orig_fin = mt_mod.MergeTab._on_finished
    def _fin(self, ok, msg, *a):
        done.update(ok=ok, msg=msg)
        return _orig_fin(self, ok, msg, *a)
    mt_mod.MergeTab._on_finished = _fin
    mt = mt_mod.MergeTab(Settings())
    mt.show()
    mt._load_folder(_set_path(set_name))
    if not wait(lambda: mt._clips and all(c.stream is not None for c in mt._clips), 120):
        res["status"] = "load_timeout"; return res
    res["clips"] = [c.path.name if hasattr(c, "path") else str(c) for c in mt._clips]
    groups = list(getattr(mt, "_spec_groups", []) or [])
    res["groups"] = [g.label() for g in groups]
    want = opt.get("baseline")
    if want and groups:
        def match(g):
            size, _, fps = want.partition("@")
            if f"{g.width}x{g.height}" != size:
                return False
            return not fps or str(g.fps).split("/")[0] == fps
        cands = [g for g in groups if match(g)]
        if cands:
            mt._on_baseline_chosen(cands[0])
        else:
            res["baseline_note"] = f"no group {want}"
    res["baseline"] = mt._chosen_group.label() if mt._chosen_group else None
    pump()
    mt._archival_check.setChecked(bool(opt.get("archival")))
    mt._per_clip_archival_check.setChecked(bool(opt.get("per_clip")))
    if opt.get("optimize"):
        mt._optimize_baseline_check.setChecked(True)
        if opt["optimize"] in mt._quality_radios:
            mt._quality_radios[opt["optimize"]].setChecked(True)
    else:
        mt._optimize_baseline_check.setChecked(False)
    mt._verify_md5_check.setChecked(bool(opt.get("verify")))
    comp = opt.get("compat")
    mt._compat_baseline_check.setChecked(bool(comp))
    if comp:
        if comp.startswith("prores"):
            mt._compat_codec_prores_radio.setChecked(True)
            mt._prores_profile_radios[comp.split(":")[1]].setChecked(True)
        else:
            mt._compat_codec_h264_radio.setChecked(True)
    if opt.get("fill") == "blur":
        mt._fill_combo.setCurrentIndex(1)
    pump()
    res["table"] = [(c.path.name if hasattr(c, "path") else "?", getattr(c.stream, "status", None))
                    for c in mt._clips]
    mt._out_dir.setText(str(out_dir))
    mt._out_name.setText(f"{cid}.mov")
    t0 = time.time()
    mt._start_merge()
    if mt._worker is None:
        res["status"] = "no_worker"; return res
    if hasattr(mt._worker, "verification_done"):
        mt._worker.verification_done.connect(lambda ok, s, p: ver.update(ok=ok, summary=s))
    if not wait(lambda: "ok" in done, 3600, 0.2):
        res["status"] = "timeout"; return res
    res["merge_s"] = round(time.time() - t0, 1)
    res["merge_ok"] = done["ok"]; res["merge_msg"] = str(done["msg"])[:600]
    res["verify"] = ver or None
    res["status"] = "merged" if done["ok"] else "merge_failed"
    print("RESULT " + json.dumps(res, default=str), flush=True)
    os._exit(0)


# ---------------------------------------------------------------- independent checks
def ffprobe_json(path):
    r = subprocess.run([FP, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                       capture_output=True, text=True)
    return json.loads(r.stdout or "{}")


def check_output(path: Path, set_name: str):
    chk = {}
    pj = ffprobe_json(path)
    streams = pj.get("streams", [])
    chk["streams"] = [{k: s.get(k) for k in ("index", "codec_type", "codec_name", "width", "height",
                                              "r_frame_rate", "pix_fmt", "sample_rate", "channels",
                                              "duration", "nb_frames")} | {"title": s.get("tags", {}).get("title")}
                      for s in streams]
    chk["format_duration"] = float(pj.get("format", {}).get("duration", 0) or 0)
    # full decode of every stream
    r = subprocess.run([FF, "-v", "error", "-i", str(path), "-map", "0:v?", "-map", "0:a?",
                        "-f", "null", "-"], capture_output=True, text=True)
    errs = [l for l in r.stderr.splitlines() if l.strip()]
    chk["decode_errors"] = len(errs)
    chk["decode_error_sample"] = errs[:5]
    # durations: first video vs each audio
    v0 = next((s for s in streams if s["codec_type"] == "video"), None)
    vd = float(v0.get("duration") or 0) if v0 else 0
    chk["video0_duration"] = vd
    chk["audio_minus_video_ms"] = [round((float(s.get("duration") or 0) - vd) * 1000, 1)
                                   for s in streams if s["codec_type"] == "audio"]
    if set_name.startswith("SYNC"):
        n_audio = sum(1 for s in streams if s["codec_type"] == "audio")
        chk["sync_tracks"] = []
        for ai in range(n_audio):
            r = subprocess.run([sys.executable, str(TOOLS / "av_events.py"), str(path), str(ai)],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            try:
                ev = json.loads(r.stdout)
                offs = [p["offset_ms"] for p in ev["pairs"]]
                good = [o for o in offs if o is not None]
                title = [s for s in streams if s["codec_type"] == "audio"][ai].get("title")
                chk["sync_tracks"].append({"a": ai, "title": title, "video_events": ev["video_events"],
                                           "audio_events": ev["audio_events"], "offsets_ms": offs})
                if ai == 0:
                    chk["sync_offsets_ms"] = offs
                    chk["sync_worst_ms"] = max((abs(o) for o in good), default=None)
                    chk["sync_missing"] = sum(1 for o in offs if o is None)
            except Exception as e:
                chk["sync_tracks"].append({"a": ai, "error": str(e) + r.stderr[-400:]})
    return chk


def run(repo: Path, work: Path, filt: str = ""):
    work.mkdir(parents=True, exist_ok=True)
    resf = work / "results.jsonl"
    for cid, set_name, opt in CASES:
        if filt and filt not in cid:
            continue
        t0 = time.time()
        print(f"[{time.strftime('%H:%M:%S')}] START {cid}", flush=True)
        shutil.rmtree(work / cid, ignore_errors=True)
        # UTF-8 both ways: Windows' default cp1252 can't carry the app's dialog
        # text (e.g. "⚠", "→"), which crashed a child mid-print before.
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        p = subprocess.run([sys.executable, __file__, "single", str(repo), str(work), cid],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=4000, env=env)
        (work / f"{cid}.log").write_text(p.stdout + "\n--- stderr\n" + p.stderr, encoding="utf-8")
        res = None
        for line in p.stdout.splitlines()[::-1]:
            if line.startswith("RESULT "):
                res = json.loads(line[7:]); break
        if res is None:
            res = {"id": cid, "status": "crash", "rc": p.returncode, "tail": p.stderr[-1500:]}
        out = work / cid / f"{cid}.mov"
        if out.exists():
            res["size_mb"] = round(out.stat().st_size / 1e6, 1)
            res["check"] = check_output(out, set_name)
        res["wall_s"] = round(time.time() - t0, 1)
        with resf.open("a") as f:
            f.write(json.dumps(res) + "\n")
        c = res.get("check", {})
        print(f"[{time.strftime('%H:%M:%S')}] END {cid} status={res.get('status')} "
              f"decode_err={c.get('decode_errors')} a-v={c.get('audio_minus_video_ms')} "
              f"sync_worst={c.get('sync_worst_ms')} missing={c.get('sync_missing')} wall={res['wall_s']}s",
              flush=True)


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "single":
        try:
            r = single(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4])
        except Exception:
            r = {"id": sys.argv[4], "status": "exception", "tb": traceback.format_exc()[-2000:]}
        print("RESULT " + json.dumps(r, default=str), flush=True)
        os._exit(0)
    else:
        run(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4] if len(sys.argv) > 4 else "")
