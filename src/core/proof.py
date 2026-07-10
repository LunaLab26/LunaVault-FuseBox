"""core/proof.py — the "see a memory come back" demonstration.

Takes one memory, puts it into a vault and pulls it straight back, then checks the
returned clip is identical — the onboarding aha (PRODUCT_DIRECTION.md, the seven
moments). Fast by design: the caller picks the SHORTEST clip. Reuses core.verify's
raw elementary-stream / decoded-PCM comparisons, so the proof measures exactly
what the full MD5 verification measures.
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.verify import (build_video_es_cmd, build_audio_pcm_cmd, md5_of_file,
                         probe_video_codec)


@dataclass
class ProofResult:
    matched: bool = False
    video_match: bool = False
    audio_match: bool = False
    audio_present: bool = False
    src_video_md5: str = ""
    rec_video_md5: str = ""
    message: str = ""
    error: str = ""


def pick_shortest(clips_with_durations):
    """Given [(path, duration), …] return the path of the shortest clip (the one
    to run the proof on, so the aha is near-instant). Ties/None-durations are
    tolerated."""
    best = None
    for path, dur in clips_with_durations:
        d = dur if (isinstance(dur, (int, float)) and dur > 0) else float("inf")
        if best is None or d < best[1]:
            best = (path, d)
    return best[0] if best else None


def _md5_of_cmd_output(cmd, out_path, kwargs) -> Optional[str]:
    r = subprocess.run(cmd, capture_output=True, **kwargs)
    if r.returncode != 0 or not Path(out_path).exists():
        return None
    return md5_of_file(out_path)


def prove_recovery(ff: str, fp: str, clip_path, work_dir, **kwargs) -> ProofResult:
    """Put `clip_path` into a vault (a stream-copy remux — the same lossless path a
    lone archival track takes) and pull it back, comparing raw video + decoded
    audio against the original. Byte-exact for a lone clip, so a genuine match is
    expected. `kwargs` are passed to subprocess (e.g. no_window())."""
    res = ProofResult()
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    vault = work / "proof_vault.mov"

    codec = probe_video_codec(fp, str(clip_path), **kwargs) or ""

    # Into the vault: stream-copy the clip (video + audio if present) into a mov.
    mk = subprocess.run(
        [ff, "-y", "-v", "error", "-i", str(clip_path),
         "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", str(vault)],
        capture_output=True, **kwargs)
    if mk.returncode != 0 or not vault.exists():
        res.error = "could not build the proof vault"
        return res

    # Video: raw elementary stream, both sides.
    src_v, rec_v = work / "p_src.video", work / "p_rec.video"
    res.src_video_md5 = _md5_of_cmd_output(
        build_video_es_cmd(ff, str(clip_path), str(src_v), codec), src_v, kwargs) or ""
    res.rec_video_md5 = _md5_of_cmd_output(
        build_video_es_cmd(ff, str(vault), str(rec_v), codec), rec_v, kwargs) or ""
    res.video_match = bool(res.src_video_md5) and res.src_video_md5 == res.rec_video_md5

    # Audio: decoded PCM, both sides (only if the clip carries audio).
    src_a, rec_a = work / "p_src.pcm", work / "p_rec.pcm"
    sa = _md5_of_cmd_output(build_audio_pcm_cmd(ff, str(clip_path), str(src_a)), src_a, kwargs)
    if sa is not None:
        res.audio_present = True
        ra = _md5_of_cmd_output(build_audio_pcm_cmd(ff, str(vault), str(rec_a)), rec_a, kwargs)
        res.audio_match = (ra is not None and ra == sa)

    for p in (src_v, rec_v, src_a, rec_a, vault):
        try:
            Path(p).unlink(missing_ok=True)
        except Exception:
            pass

    res.matched = res.video_match and (res.audio_match or not res.audio_present)
    res.message = ("Your memory came back — exactly as you filmed it."
                   if res.matched else
                   "The recovered memory did not match — worth a closer look.")
    return res
