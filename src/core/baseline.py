"""core/baseline.py — detect the distinct clip specs and recommend a baseline.

The merge baseline (the spec every clip conforms to) is chosen by the user from
the specs actually present in the footage; its clips stream-copy, the rest
transcode to it. This module enumerates the distinct specs and recommends one —
the *duration-weighted best quality of the majority*, without upscaling: pick the
resolution that covers the most footage-time, then the best bit-depth / codec at
that resolution. Pure (no ffprobe): callers pass ClipSpec built from probe.

Example (the real 4-cam test): recommends the Luna Ultra spec — HEVC 3840×2160
10-bit — so its clips stream-copy and everything else conforms up to it.
"""

from dataclasses import dataclass

# Codec efficiency rank (higher = smaller baseline at equal quality).
_CODEC_RANK = {"av1": 3, "hevc": 2, "h265": 2, "vp9": 2, "h264": 1, "avc": 1}


def _codec_rank(codec: str) -> int:
    return _CODEC_RANK.get((codec or "").lower(), 0)


@dataclass
class ClipSpec:
    """One clip's spec, as fed in from probe.StreamInfo."""
    codec: str = ""
    width: int = 0
    height: int = 0
    fps: str = ""          # normalised fps string (probe.StreamInfo.fps_str)
    pix_fmt: str = ""
    bit_depth: int = 8
    color_space: str = ""
    duration: float = 0.0
    color_transfer: str = ""
    color_primaries: str = ""


@dataclass
class SpecGroup:
    """A distinct spec + how much footage shares it. Doubles as the chosen
    baseline target."""
    codec: str
    width: int
    height: int
    fps: str
    pix_fmt: str
    bit_depth: int
    color_space: str
    clip_count: int = 0
    total_duration: float = 0.0
    color_transfer: str = ""
    color_primaries: str = ""

    def key(self) -> tuple:
        return (self.codec.lower(), self.width, self.height, self.fps, self.pix_fmt.lower())

    def label(self) -> str:
        return f"{(self.codec or '?').upper()} {self.width}×{self.height} {self.bit_depth}-bit {self.fps}fps"


def enumerate_specs(clip_specs: list) -> list:
    """Group ClipSpecs into distinct SpecGroups (by codec/res/fps/pix_fmt),
    preserving first-seen order, tallying count + total duration."""
    groups: dict = {}
    for cs in clip_specs:
        k = (cs.codec.lower(), cs.width, cs.height, cs.fps, (cs.pix_fmt or "").lower())
        g = groups.get(k)
        if g is None:
            g = SpecGroup(codec=cs.codec, width=cs.width, height=cs.height, fps=cs.fps,
                          pix_fmt=cs.pix_fmt, bit_depth=cs.bit_depth, color_space=cs.color_space,
                          color_transfer=cs.color_transfer, color_primaries=cs.color_primaries)
            groups[k] = g
        g.clip_count += 1
        g.total_duration += max(0.0, cs.duration)
    return list(groups.values())


def recommend_baseline(groups: list):
    """Recommend a SpecGroup as the baseline: the majority resolution (by total
    duration — so the bulk of footage isn't upscaled), then the best bit-depth /
    codec / duration at that resolution. Returns None for an empty list."""
    if not groups:
        return None
    res_duration: dict = {}
    for g in groups:
        res_duration[(g.width, g.height)] = res_duration.get((g.width, g.height), 0.0) + g.total_duration
    # Majority resolution: most footage-time, tie-break to the larger frame.
    majority_res = max(res_duration, key=lambda r: (res_duration[r], r[0] * r[1]))
    candidates = [g for g in groups if (g.width, g.height) == majority_res] or list(groups)
    # Best quality at that resolution: bit-depth, then codec efficiency, then duration.
    candidates.sort(key=lambda g: (g.bit_depth, _codec_rank(g.codec), g.total_duration), reverse=True)
    return candidates[0]
