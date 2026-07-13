"""core/diagnostics.py — pre-flight per-clip diagnostic checks: catch a
corrupt/troublesome clip BEFORE a merge, rather than discovering it only
after a stream-copy or delivery re-encode fails downstream.

Formalizes a real investigation (DEVELOPMENT.md's clip 026 deep-dive):
container structure, packet timestamps, stream-copy compatibility, and
decode-error scans all came back clean on a clip the user suspected was
corrupt — definitively ruling it out rather than leaving it a guess.

Pure: command builders + result parsers only, no subprocess calls — mirrors
core/extract.py's own split between building commands here and running them
in a worker (diagnostics_workers.py), so the slow checks (decode scans) can
be cancelled mid-run without this module needing to know about threads.
"""

import json
from dataclasses import dataclass
from typing import Optional


@dataclass
class DiagnosticResult:
    check_id: str
    label: str
    verdict: str      # "clean" | "warning" | "problem" | "error" (the check itself couldn't run)
    detail: str = ""


@dataclass
class CheckInfo:
    check_id: str
    label: str
    description: str
    default_on: bool
    cost_hint: str    # "fast" | "medium" | "slow"


CHECKS = [
    CheckInfo("container", "Container & stream structure",
             "Codec, resolution, pixel format, and rotation are readable and consistent.",
             True, "fast"),
    CheckInfo("timestamps", "Timestamp & keyframe integrity",
             "Packet-level timestamp gaps and irregular keyframe spacing.",
             True, "fast"),
    CheckInfo("streamcopy", "Stream-copy compatibility",
             "Confirms the clip copies cleanly into the merge pipeline (bitstream-filter check).",
             True, "fast"),
    CheckInfo("quickdecode", "Quick decode sample scan",
             "Decodes short windows (start/middle/end) with aggressive error detection.",
             False, "medium"),
    CheckInfo("fulldecode", "Full decode scan",
             "Decodes the entire clip checking for errors — can take minutes per 4K clip.",
             False, "slow"),
]
_CHECKS_BY_ID = {c.check_id: c for c in CHECKS}


# ── Container & stream structure ────────────────────────────────────────────

def build_container_probe_cmd(fp: str, path: str) -> list:
    return [fp, "-v", "error", "-show_entries",
           "stream=index,codec_type,codec_name,width,height,pix_fmt",
           "-of", "json", str(path)]


def parse_container_result(returncode: int, stdout: str, stderr: str) -> DiagnosticResult:
    label = _CHECKS_BY_ID["container"].label
    if returncode != 0:
        return DiagnosticResult("container", label, "error", (stderr or "probe failed").strip()[:300])
    try:
        streams = json.loads(stdout or "{}").get("streams", [])
    except Exception as e:
        return DiagnosticResult("container", label, "error", f"couldn't parse probe output: {e}")
    vids = [s for s in streams if s.get("codec_type") == "video"]
    if not vids:
        return DiagnosticResult("container", label, "problem", "No video stream found.")
    v = vids[0]
    missing = [k for k in ("codec_name", "width", "height", "pix_fmt") if not v.get(k)]
    if missing:
        return DiagnosticResult("container", label, "problem",
                                f"Video stream is missing: {', '.join(missing)}.")
    if stderr.strip():
        return DiagnosticResult("container", label, "warning", stderr.strip()[:300])
    return DiagnosticResult("container", label, "clean",
                            f"{v['codec_name']} {v['width']}x{v['height']} {v['pix_fmt']}")


# ── Timestamp & keyframe integrity ──────────────────────────────────────────

def build_timestamp_probe_cmd(fp: str, path: str) -> list:
    # DTS (decode order), NOT PTS (presentation order): any stream with
    # B-frames — the overwhelming majority of real H.264/HEVC footage —
    # legitimately reorders PTS relative to packet/storage order, which is
    # normal reference-frame structure, not corruption (confirmed directly:
    # a plain libx264 encode with its own default B-frames tripped a PTS-based
    # version of this check on every clip). DTS is guaranteed non-decreasing
    # regardless of B-frame reordering, so THAT'S the correct monotonicity
    # signal for "are packets arriving in a sane order".
    return [fp, "-v", "error", "-select_streams", "v:0",
           "-show_entries", "packet=dts_time,flags", "-of", "csv=p=0", str(path)]


def parse_timestamp_result(returncode: int, stdout: str, stderr: str) -> DiagnosticResult:
    label = _CHECKS_BY_ID["timestamps"].label
    if returncode != 0:
        return DiagnosticResult("timestamps", label, "error", (stderr or "probe failed").strip()[:300])
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    if not lines:
        return DiagnosticResult("timestamps", label, "error", "No packet data returned.")
    dts_list = []
    kf_idx = []
    for i, line in enumerate(lines):
        parts = line.split(",")
        if len(parts) < 2:
            continue
        try:
            dts_list.append(float(parts[0]))
        except ValueError:
            dts_list.append(None)
        if "K" in parts[1]:
            kf_idx.append(i)
    valid_dts = [d for d in dts_list if d is not None]
    non_monotonic = sum(1 for a, b in zip(valid_dts, valid_dts[1:]) if b < a)
    issues = []
    if non_monotonic:
        issues.append(f"{non_monotonic} out-of-order timestamp(s)")
    if len(kf_idx) < 1:
        issues.append("no keyframes found")
    elif len(kf_idx) >= 2:
        gaps = [kf_idx[j + 1] - kf_idx[j] for j in range(len(kf_idx) - 1)]
        if min(gaps) > 0 and max(gaps) > 2 * min(gaps):
            issues.append(f"irregular keyframe spacing ({min(gaps)}-{max(gaps)} frames)")
    if issues:
        return DiagnosticResult("timestamps", label, "warning", "; ".join(issues))
    return DiagnosticResult("timestamps", label, "clean",
                            f"{len(lines)} packets, {len(kf_idx)} keyframes, even spacing")


# ── Stream-copy compatibility ────────────────────────────────────────────────

_ANNEXB_FILTERS = {"hevc": "hevc_mp4toannexb", "h264": "h264_mp4toannexb"}


def build_streamcopy_test_cmd(ff: str, path: str, out_path: str, sample_secs: float) -> list:
    return [ff, "-y", "-v", "error", "-i", str(path), "-map", "0:v:0",
           "-c", "copy", "-t", f"{max(0.5, sample_secs):.2f}", str(out_path)]


def build_annexb_test_cmd(ff: str, path: str, out_path: str, sample_secs: float,
                          codec: str) -> Optional[list]:
    """None when the codec has no known annex-B bitstream filter — skip that
    part of the check rather than fail it for an unrelated reason."""
    bsf = _ANNEXB_FILTERS.get((codec or "").lower())
    if bsf is None:
        return None
    return [ff, "-y", "-v", "error", "-i", str(path), "-map", "0:v:0",
           "-c", "copy", "-bsf:v", bsf, "-t", f"{max(0.5, sample_secs):.2f}",
           "-f", "mpegts", str(out_path)]


def parse_streamcopy_result(copy_ok: bool, copy_stderr: str,
                            annexb_ok: Optional[bool], annexb_stderr: str) -> DiagnosticResult:
    label = _CHECKS_BY_ID["streamcopy"].label
    if not copy_ok:
        return DiagnosticResult("streamcopy", label, "problem",
                                (copy_stderr or "stream copy failed").strip()[-300:])
    if annexb_ok is False:
        return DiagnosticResult("streamcopy", label, "warning",
                                "Bitstream-filter check failed: " + (annexb_stderr or "").strip()[-300:])
    return DiagnosticResult("streamcopy", label, "clean", "Stream-copies cleanly into the merge pipeline.")


# ── Decode scans (quick sample + full) ───────────────────────────────────────

def sample_windows(duration: float, window_secs: float = 5.0) -> list:
    """[(start, length), ...] for start/middle/end sampled windows, clamped
    to the clip's own duration — a short clip just gets fewer/overlapping
    windows rather than erroring."""
    if duration <= 0:
        return [(0.0, window_secs)]
    length = min(window_secs, duration)
    mid = max(0.0, duration / 2 - length / 2)
    end = max(0.0, duration - length)
    starts = sorted({round(s, 2) for s in (0.0, mid, end)})
    return [(s, length) for s in starts]


def build_decode_scan_cmd(ff: str, path: str, start: Optional[float] = None,
                          length: Optional[float] = None) -> list:
    cmd = [ff, "-v", "warning", "-err_detect", "+crccheck+bitstream+buffer"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(path), "-map", "0:v:0"]
    if length is not None:
        cmd += ["-t", f"{length:.3f}"]
    cmd += ["-f", "null", "-"]
    return cmd


def parse_decode_scan_results(check_id: str, window_results: list) -> DiagnosticResult:
    """`window_results` is [(start, stderr_text), ...] — one entry per
    sampled window (quick scan) or a single (None, stderr_text) (full scan)."""
    label = _CHECKS_BY_ID[check_id].label
    findings = []
    for start, stderr in window_results:
        lines = [ln for ln in (stderr or "").splitlines() if ln.strip()]
        if lines:
            where = f"@{start:.0f}s: " if start is not None else ""
            findings.append(f"{where}{lines[0][:150]}")
    if findings:
        verdict = "problem" if check_id == "fulldecode" else "warning"
        return DiagnosticResult(check_id, label, verdict,
                                f"{len(findings)} decode issue(s) — " + "; ".join(findings[:3]))
    n = len(window_results)
    scope = "the entire clip" if check_id == "fulldecode" else f"{n} sampled window(s)"
    return DiagnosticResult(check_id, label, "clean", f"Decoded {scope} cleanly.")
