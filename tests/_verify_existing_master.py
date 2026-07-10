"""Standalone: run the (updated) MD5 verification against an ALREADY-BUILT
master + its sidecar manifest, without re-merging. Lets us validate the
decode-lossless verification fallback in minutes against the retained
large_archival_shared master instead of a 20-min rebuild.

Usage: python _verify_existing_master.py <master.mov> <source_folder>
"""
import os
import sys
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
for s in (sys.stdout, sys.stderr):
    if hasattr(s, "reconfigure"):
        s.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402
app = QApplication.instance() or QApplication([])

import core.manifest as manifest_mod  # noqa: E402
from ffmpeg_runner import MergeWorker, get_ffmpeg  # noqa: E402
from core.verify import write_verify_log  # noqa: E402

master = Path(sys.argv[1])
source_folder = Path(sys.argv[2])

ff, fp = get_ffmpeg()
manifest = manifest_mod.from_json(manifest_mod.sidecar_path(master).read_text(encoding="utf-8"))

# Minimal stand-in for a ClipInfo — _verify_one_clip only needs these attrs.
def make_clip(entry):
    src = source_folder / entry.source_filename
    return types.SimpleNamespace(
        path=src, stem=src.stem, name=src.name,
        wav_path=None, wav_offset=0.0,   # WAV not exercised here; focus on video+audio
    )

clips = [make_clip(e) for e in manifest.clips]

# Build a MergeWorker without running __init__ (we only need a few attrs + methods).
w = MergeWorker.__new__(MergeWorker)
w._output = master
w._scratch_base = None

verify_dir = Path(os.environ["TEMP"]) / "verify_existing"
verify_dir.mkdir(parents=True, exist_ok=True)

results = []
for clip, entry in zip(clips, manifest.clips):
    print(f"verifying {clip.stem} ...", flush=True)
    results.append(w._verify_one_clip(ff, fp, clip, entry, manifest, verify_dir))

out_log = master.with_name(master.stem + ".REVERIFY.log")
write_verify_log(out_log, master.name, results)
passed = sum(1 for r in results if r.passed)
print(f"\n{passed}/{len(results)} clips PASS. Log: {out_log}")
