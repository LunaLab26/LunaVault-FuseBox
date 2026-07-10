"""core/seam_diag.py — classify WHY a mid-track clip's recovered video decodes
differently from its original (Task 86).

The verify log can say a mid-track window "decodes DIFFERENTLY … nothing to
explain it". Two known mechanisms produce exactly that:

  1. SEAM DAMAGE — stream-copied concat of independently-encoded HEVC severs
     frame-reference continuity (POC/RPS) at each join, so the first GOP(s)
     decoded after a seam come out wrong; the rest of the clip decodes fine.
  2. WINDOW ROUNDING — the recovery window (cumulative durations, ms-precision
     chapters) lands a frame or two early/late; every frame decodes fine but
     the window is shifted.

Per-frame decode hashes (ffmpeg's framemd5) distinguish them conclusively:
take the original's first N frame-hashes and search for them inside a widened
decode of the master around the clip's start. A full match at a shifted offset
= rounding; a match whose first X frames differ but whose remainder aligns =
seam damage confined to X frames; no alignment = genuine divergence.

Pure module: framemd5 parsing + alignment/classification only — the ffmpeg
invocations live in tools/diagnose_midtrack_decode.py.
"""

from dataclasses import dataclass, field
from typing import Optional

VERDICT_MATCH = "match"              # window decodes identically at the expected offset
VERDICT_OFFSET = "window-offset"     # decodes identically, but at a shifted offset
VERDICT_SEAM = "seam-damage"         # head frames decode wrong, remainder aligns
VERDICT_DIVERGENT = "divergent"      # no alignment found — genuinely different content
VERDICT_NO_DATA = "no-data"          # one side produced no frames


def parse_framemd5(text: str) -> list:
    """Frame hashes from `ffmpeg -f framemd5` output, in decode order.
    Lines look like `0,  0,  0,  1,   518400, <md5>`; comments start with #."""
    hashes = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 6 and parts[-1]:
            hashes.append(parts[-1])
    return hashes


@dataclass
class SeamVerdict:
    verdict: str
    offset: Optional[int] = None        # where the original aligned in the master window (frames)
    expected_offset: int = 0            # where it SHOULD align (the lead-in length)
    damaged_frames: int = 0             # leading frames that decode differently (seam damage)
    matched_frames: int = 0             # frames that align exactly
    total_frames: int = 0               # original frames compared
    notes: list = field(default_factory=list)

    @property
    def shift_frames(self) -> Optional[int]:
        """How far the window is off, in frames (0 = exactly where modelled)."""
        return None if self.offset is None else self.offset - self.expected_offset


def _suffix_match_len(orig: list, master: list, o: int) -> int:
    """Longest run of frames matching from the TAIL of `orig` against master
    aligned at offset `o` (master[o+i] ↔ orig[i])."""
    n = len(orig)
    run = 0
    for i in range(n - 1, -1, -1):
        j = o + i
        if 0 <= j < len(master) and master[j] == orig[i]:
            run += 1
        else:
            break
    return run


def classify_window(orig: list, master: list, expected_offset: int,
                    min_tail_fraction: float = 0.5) -> SeamVerdict:
    """Explain how `orig` (the original clip's first N frame hashes) relates to
    `master` (frame hashes of the master decoded from `expected_offset` frames
    BEFORE the clip's modelled start, through its head).

    `min_tail_fraction`: a seam-damage verdict requires at least this fraction
    of the original frames to align after the damaged head — anything less is
    reported as divergent rather than over-explained."""
    n, m = len(orig), len(master)
    if n == 0 or m == 0:
        return SeamVerdict(VERDICT_NO_DATA, expected_offset=expected_offset,
                           total_frames=n,
                           notes=["one side produced no decodable frames"])

    # 1. exact subsequence match anywhere?
    full = [o for o in range(0, m - n + 1) if master[o:o + n] == orig]
    if expected_offset in full:
        return SeamVerdict(VERDICT_MATCH, offset=expected_offset,
                           expected_offset=expected_offset,
                           matched_frames=n, total_frames=n)
    if full:
        o = min(full, key=lambda x: abs(x - expected_offset))
        return SeamVerdict(VERDICT_OFFSET, offset=o, expected_offset=expected_offset,
                           matched_frames=n, total_frames=n,
                           notes=[f"decodes identically {o - expected_offset:+d} frame(s) "
                                  "from the modelled window position"])

    # 2. best "damaged head, intact tail" alignment.
    best_o, best_run = None, 0
    for o in range(0, max(1, m - n + 1)):
        run = _suffix_match_len(orig, master, o)
        if run > best_run:
            best_o, best_run = o, run
    if best_o is not None and best_run >= max(1, int(n * min_tail_fraction)):
        damaged = n - best_run
        return SeamVerdict(VERDICT_SEAM, offset=best_o, expected_offset=expected_offset,
                           damaged_frames=damaged, matched_frames=best_run, total_frames=n,
                           notes=[f"first {damaged} frame(s) decode differently; the remaining "
                                  f"{best_run} align exactly"
                                  + (f" ({best_o - expected_offset:+d} frame window shift)"
                                     if best_o != expected_offset else "")])

    return SeamVerdict(VERDICT_DIVERGENT, expected_offset=expected_offset,
                       matched_frames=best_run, total_frames=n,
                       notes=["no usable alignment — the decoded content genuinely differs "
                              "beyond a damaged head or a shifted window"])


def describe(stem: str, v: SeamVerdict, fps: float = 29.97) -> str:
    """One human-readable block for the report."""
    lines = [f"{stem}: {v.verdict.upper()}"]
    if v.verdict == VERDICT_MATCH:
        lines.append(f"   window decodes identically at the modelled position "
                     f"({v.matched_frames}/{v.total_frames} frames)")
    elif v.verdict == VERDICT_OFFSET:
        ms = (v.shift_frames / fps) * 1000 if fps > 0 else 0
        lines.append(f"   MECHANISM 2 (window rounding): decodes identically but the window "
                     f"is off by {v.shift_frames:+d} frame(s) ≈ {ms:+.0f}ms")
    elif v.verdict == VERDICT_SEAM:
        ms = (v.damaged_frames / fps) * 1000 if fps > 0 else 0
        lines.append(f"   MECHANISM 1 (seam damage): {v.damaged_frames} frame(s) ≈ {ms:.0f}ms "
                     f"at the clip's head decode wrong; the remaining {v.matched_frames} are "
                     f"pixel-identical")
    for nte in v.notes:
        lines.append(f"   {nte}")
    return "\n".join(lines)
