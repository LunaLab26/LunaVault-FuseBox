"""Tests for core.plan_report — the explanation must match what the builder does."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from probe import StreamInfo
from clip_model import ClipInfo
from core.ffmpeg_cmd import OutputPlan, OutputTrack, build_mux_cmd_plan
from core.plan_report import analyze_clip, analyze_merge

PF = Path("p.txt")
_AUDIO_SPECS = {"0:a:0", "1:a:0", "[mix]", "[s]"}


def _audio_map_count(cmd):
    n = 0
    for i, tok in enumerate(cmd):
        if tok == "-map" and i + 1 < len(cmd) and cmd[i + 1] in _AUDIO_SPECS:
            n += 1
    return n


def _clip(status="ok", cam="aac", wav=False, dur=60.0, wav_dur=0.0):
    c = ClipInfo(path=Path("c.mp4"),
                 stream=StreamInfo(status=status, width=3840, height=2160,
                                   duration=dur, audio_codec=cam))
    if wav:
        c.wav_path = Path("c.wav")
        c.wav_duration = wav_dur
    return c


def _agree(clip, plan):
    cmd = build_mux_cmd_plan("ffmpeg", clip, Path("o.mov"), PF, plan, "crop")
    rep = analyze_clip(clip, plan)
    assert len(rep.audio) == _audio_map_count(cmd), \
        f"report {len(rep.audio)} vs builder {_audio_map_count(cmd)}: {[a.label for a in rep.audio]}"


def test_report_matches_camera_wav():
    _agree(_clip(cam="aac", wav=True), OutputPlan())


def test_report_matches_no_camera():
    _agree(_clip(cam="", wav=True), OutputPlan())


def test_report_matches_mix_on():
    _agree(_clip(cam="aac", wav=True),
           OutputPlan(tracks=[OutputTrack("camera"), OutputTrack("wav"), OutputTrack("mix")]))


def test_report_matches_slowmo():
    _agree(_clip(cam="", wav=True, dur=48.0, wav_dur=12.0), OutputPlan())


def test_report_matches_video_off():
    _agree(_clip(cam="aac", wav=True), OutputPlan(include_video=False))


def test_report_notes_explain_slowmo():
    rep = analyze_clip(_clip(cam="", wav=True, dur=48.0, wav_dur=12.0), OutputPlan())
    assert rep.is_slowmo
    joined = " ".join(rep.notes).lower()
    assert "slow-motion" in joined and "stretched" in joined and "pitch" in joined


def test_report_notes_explain_missing_camera():
    rep = analyze_clip(_clip(cam="", wav=True, dur=60, wav_dur=58), OutputPlan())
    assert any("no camera audio" in n.lower() for n in rep.notes)


def test_merge_report_totals_positive():
    clips = [_clip(cam="aac", wav=True, dur=30), _clip(cam="", wav=True, dur=48, wav_dur=12)]
    for i, c in enumerate(clips):
        c.order_idx = i
    rep = analyze_merge(clips, OutputPlan())
    assert rep.total_bytes >= 0 and rep.worst_secs >= rep.best_secs >= 0
    assert rep.n_slowmo == 1


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"\n{len(fns)} plan_report tests passed.")
