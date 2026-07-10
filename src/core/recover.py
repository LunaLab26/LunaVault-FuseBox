"""core/recover.py — run the recovery a user's "save the original" triggers.

extract.py holds the pure command BUILDERS; this RUNS them (subprocess) — the same
split verify.py uses between its builders and its probe_* runners. Shared by the
Memory view's save-the-original and testable headlessly.
"""

import subprocess
from pathlib import Path
from typing import Optional

from core.extract import (build_recovery_plan, build_recover_clip_cmd,
                          recover_metadata_args, recovered_filenames)


def recover_clip(ff: str, master_path, manifest, index: int, dest_dir, **kwargs) -> Optional[Path]:
    """Save memory `index` from `master_path` back to `dest_dir` under its original
    name — byte-exact where the manifest allows, with GPS/creation-time/device
    provenance re-attached. Returns the written path, or None on failure. `kwargs`
    pass through to subprocess (e.g. no_window())."""
    clips = getattr(manifest, "clips", [])
    if not (0 <= index < len(clips)):
        return None
    entry = clips[index]
    plan = build_recovery_plan(manifest, entry)
    if plan is None:
        return None
    name, _wav = recovered_filenames(entry)
    dest = Path(dest_dir) / name
    cmd = build_recover_clip_cmd(ff, str(master_path), plan, str(dest))
    meta = recover_metadata_args(entry)
    if meta:
        cmd = cmd[:-1] + meta + cmd[-1:]
    r = subprocess.run(cmd, capture_output=True, **kwargs)
    if r.returncode != 0 or not dest.exists():
        return None
    return dest
