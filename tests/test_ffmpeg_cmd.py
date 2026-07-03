"""Tests for the ffmpeg command builders in core.ffmpeg_cmd.

No subprocess is run — we assert the argument lists contain the right flags so a
refactor can't silently change what ffmpeg is told to do.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from probe import StreamInfo
from clip_model import ClipInfo
from core.ffmpeg_cmd import (
    hms_to_seconds, build_mux_cmd, build_concat_cmd,
    build_whatsapp_cmd, build_preview_cmd, build_thumbnail_cmd,
    MixSpec, OutputPlan, OutputTrack, build_mux_cmd_plan,
    build_archival_concat_cmd, build_final_archival_mux_cmd,
    transcode_vf_parts, ConformSpec, _video_encoder_args,
)

PF = Path("progress.txt")


def _clip(conflicts, w=1280, h=960, rotation=0):
    st = StreamInfo(width=w, height=h, rotation=rotation)
    st.conflicts = conflicts
    c = ClipInfo(path=Path("x.mp4"))
    c.stream = st
    return c


def test_transcode_pads_not_stretches_odd_aspect():
    parts = transcode_vf_parts(_clip(["1280×960"]), "pad")
    joined = ",".join(parts)
    assert "force_original_aspect_ratio=decrease" in joined and "pad=3840:2160" in joined
    assert "scale=3840:2160:flags" not in joined   # never a bare stretch


def test_transcode_fits_rotated_clip_even_at_matching_res():
    # 4K but rotated 270° → display dims swap, so it still needs fitting.
    parts = transcode_vf_parts(_clip(["h264"], w=3840, h=2160, rotation=270), "pad")
    assert any("pad=3840:2160" in p for p in parts)


def test_transcode_targets_custom_baseline():
    parts = transcode_vf_parts(_clip(["1280×720", "30fps"], w=1280, h=720), "pad",
                               ConformSpec(width=1920, height=1080, fps="25"))
    joined = ",".join(parts)
    assert "pad=1920:1080" in joined and "fps=25" in joined


def test_blur_fill_uses_overlay_graph():
    parts = transcode_vf_parts(_clip(["1080×1920"], w=1080, h=1920), "pad",
                               ConformSpec(fill="blur"))
    assert "split=2" in parts[0] and "overlay=" in parts[0]


def test_encoder_args_switch_codec():
    assert "libx265" in _video_encoder_args(ConformSpec(codec="hevc"))
    h264 = _video_encoder_args(ConformSpec(codec="h264", pix_fmt="yuv420p"))
    assert "libx264" in h264 and "hvc1" not in h264 and "yuv420p" in h264


def _ok_clip(with_wav=False, wav_offset=0.0, square=False, cam_audio="aac"):
    stream = StreamInfo(status="ok", width=(100 if square else 3840),
                        height=(100 if square else 2160), audio_codec=cam_audio)
    clip = ClipInfo(path=Path("clip.mp4"), stream=stream)
    if with_wav:
        clip.wav_path = Path("clip.wav")
        clip.wav_offset = wav_offset
    return clip


def test_hms_to_seconds():
    assert hms_to_seconds("00:01:30") == 90.0
    assert hms_to_seconds("01:00") == 60.0
    assert hms_to_seconds("12") == 12.0
    assert hms_to_seconds("garbage") == 0.0


def test_mux_no_wav_is_stream_copy():
    cmd = build_mux_cmd("ffmpeg", _ok_clip(), Path("out.mov"), PF, "camera", "crop")
    assert "-c:v" in cmd and "copy" in cmd
    assert "alac" not in cmd          # no WAV → no lossless backup track
    assert cmd[-1] == "out.mov"


def test_mux_camera_mode_tracks():
    cmd = build_mux_cmd("ffmpeg", _ok_clip(with_wav=True), Path("out.mov"), PF, "camera", "crop")
    s = " ".join(cmd)
    assert "-c:a:0 copy" in s          # camera primary, stream-copied (lossless)
    assert "-c:a:1 alac" in s          # WAV backup → ALAC (lossless)
    assert "amix" not in s


def test_mux_wav_mode_default_is_wav():
    cmd = build_mux_cmd("ffmpeg", _ok_clip(with_wav=True), Path("out.mov"), PF, "wav", "crop")
    s = " ".join(cmd)
    assert "-c:a:0 alac" in s          # WAV primary (lossless)
    assert "-c:a:1 copy" in s          # camera secondary (lossless copy)


def test_mux_mixed_mode_has_three_tracks():
    cmd = build_mux_cmd("ffmpeg", _ok_clip(with_wav=True), Path("out.mov"), PF, "mixed", "crop")
    s = " ".join(cmd)
    assert "amix=inputs=2" in s
    assert "-c:a:2 alac" in s          # third track is the lossless WAV


def test_mux_transcode_path():
    clip = ClipInfo(path=Path("c.mp4"),
                    stream=StreamInfo(status="transcode", width=1920, height=1080,
                                      conflicts=["1920×1080", "25fps"]))
    cmd = build_mux_cmd("ffmpeg", clip, Path("out.mov"), PF, "camera", "crop")
    s = " ".join(cmd)
    assert "libx265" in s
    assert "scale=3840:2160" in s
    assert "fps=30000/1001" in s


def test_whatsapp_cmd_no_grade():
    cmd = build_whatsapp_cmd("ffmpeg", "in.mp4", "00:00:01", "00:00:05",
                             Path("out.mp4"), None, PF)
    s = " ".join(cmd)
    assert "libx264" in s and "-crf 26" in s
    assert "scale=1280:720" in s
    assert "+faststart" in s


def test_concat_cmd_is_copy():
    cmd = build_concat_cmd("ffmpeg", Path("list.txt"), Path("ch.txt"),
                           Path("out.mov"), PF)
    s = " ".join(cmd)
    assert "-f concat" in s and "-c copy" in s


def test_concat_cmd_appends_extra_out_args_before_output():
    cmd = build_concat_cmd("ffmpeg", Path("l.txt"), Path("c.txt"), Path("out.mov"), PF,
                           extra_out_args=["-movflags", "use_metadata_tags", "-metadata", "k=v"])
    assert cmd[-1] == "out.mov"
    assert cmd.index("use_metadata_tags") < cmd.index("out.mov")


def test_archival_concat_is_stream_copy_all_streams():
    cmd = build_archival_concat_cmd("ffmpeg", Path("grp.txt"), Path("arch.mov"))
    s = " ".join(cmd)
    assert "-f concat" in s and "-map 0" in s and "-c copy" in s
    assert cmd[-1] == "arch.mov"


def test_final_archival_mux_maps_and_dispositions():
    cmd = build_final_archival_mux_cmd(
        "ffmpeg", Path("base.mov"), [Path("a1.mov"), Path("a2.mov")], Path("out.mov"), PF,
        extra_out_args=["-metadata", "k=v"])
    # baseline + 2 archival inputs
    assert cmd.count("-i") == 3
    # all baseline streams, then each archival's video + optional audio
    assert "-map" in cmd and "0" in cmd
    assert "1:v" in cmd and "1:a?" in cmd and "2:v" in cmd and "2:a?" in cmd
    # baseline video default, archival videos not
    assert cmd[cmd.index("-disposition:v:0") + 1] == "default"
    assert "-disposition:v:1" in cmd and "-disposition:v:2" in cmd
    # copy + metadata/chapters carried, extra args before output
    assert "copy" in cmd and "-map_chapters" in cmd
    assert cmd[-1] == "out.mov"
    assert cmd.index("k=v") < cmd.index("out.mov")


def test_preview_and_thumbnail_single_frame():
    p = build_preview_cmd("ffmpeg", "in.mp4", "00:00:02", None, "p.jpg")
    t = build_thumbnail_cmd("ffmpeg", "in.mp4", 2.0, None, "t.jpg")
    assert "-frames:v" in p and p[-1] == "p.jpg"
    assert "-frames:v" in t and t[-1] == "t.jpg"


def test_mix_lr_uses_join_not_amix():
    mix = MixSpec(kind="lr")
    cmd = build_mux_cmd("ffmpeg", _ok_clip(with_wav=True), Path("out.mov"), PF,
                        "camera", "crop", mix=mix)
    s = " ".join(cmd)
    assert "join=inputs=2" in s and "map=0.0-FL|1.0-FR" in s
    assert "amix" not in s
    # lossless mics preserved, mix is the 3rd (aac) track, default stays on a:0
    assert "-c:a:0 copy" in s and "-c:a:1 alac" in s and "-c:a:2 aac" in s
    assert "-disposition:a:0 default" in s and "-disposition:a:2 0" in s


def test_mix_5050_uses_amix():
    mix = MixSpec(kind="5050")
    cmd = build_mux_cmd("ffmpeg", _ok_clip(with_wav=True), Path("out.mov"), PF,
                        "camera", "crop", mix=mix)
    s = " ".join(cmd)
    assert "amix=inputs=2" in s and "join=inputs=2" not in s


def test_mix_make_default_promotes_track2():
    mix = MixSpec(kind="lr", make_default=True)
    cmd = build_mux_cmd("ffmpeg", _ok_clip(with_wav=True), Path("out.mov"), PF,
                        "camera", "crop", mix=mix)
    s = " ".join(cmd)
    assert "-disposition:a:2 default" in s and "-disposition:a:0 0" in s


def test_mix_drift_and_polarity_and_levels():
    mix = MixSpec(kind="lr", match_levels=True, drift_ratio=1.000183, polarity_inverted=True)
    cmd = build_mux_cmd("ffmpeg", _ok_clip(with_wav=True), Path("out.mov"), PF,
                        "camera", "crop", mix=mix)
    s = " ".join(cmd)
    assert "atempo=1.000183" in s          # drift correction on WAV side
    assert "volume=-1.0" in s              # polarity flip
    assert s.count("dynaudnorm") == 2      # level match on both branches


def test_mix_no_drift_filter_when_ratio_one():
    mix = MixSpec(kind="lr", drift_ratio=1.0)
    cmd = build_mux_cmd("ffmpeg", _ok_clip(with_wav=True), Path("out.mov"), PF,
                        "camera", "crop", mix=mix)
    assert "atempo" not in " ".join(cmd)


def test_mix_ignored_without_wav():
    # No WAV → falls back to plain stream copy, no mix track.
    cmd = build_mux_cmd("ffmpeg", _ok_clip(with_wav=False), Path("out.mov"), PF,
                        "camera", "crop", mix=MixSpec())
    s = " ".join(cmd)
    assert "join" not in s and "amix" not in s and "-c:a:2" not in s


def test_plan_default_two_lossless_tracks():
    plan = OutputPlan(tracks=[OutputTrack("camera"), OutputTrack("wav"),
                              OutputTrack("mix", enabled=False)])
    cmd = build_mux_cmd_plan("ffmpeg", _ok_clip(with_wav=True), Path("o.mov"), PF, plan, "crop")
    s = " ".join(cmd)
    assert "-c:a:0 copy" in s and "-c:a:1 alac" in s
    assert "[mix]" not in s
    assert "-disposition:a:0 default" in s


def test_plan_mix_appended():
    plan = OutputPlan(tracks=[OutputTrack("camera"), OutputTrack("wav"), OutputTrack("mix")])
    cmd = build_mux_cmd_plan("ffmpeg", _ok_clip(with_wav=True), Path("o.mov"), PF, plan, "crop")
    s = " ".join(cmd)
    assert "join=inputs=2" in s and "-c:a:2 aac" in s
    assert "-disposition:a:0 default" in s   # camera still default


def test_plan_mix_first_is_default():
    plan = OutputPlan(tracks=[OutputTrack("mix"), OutputTrack("camera"), OutputTrack("wav")])
    cmd = build_mux_cmd_plan("ffmpeg", _ok_clip(with_wav=True), Path("o.mov"), PF, plan, "crop")
    s = " ".join(cmd)
    assert "-map [mix]" in s and "-c:a:0 aac" in s
    assert "-disposition:a:0 default" in s


def test_plan_video_disabled():
    plan = OutputPlan(include_video=False, tracks=[OutputTrack("wav")])
    cmd = build_mux_cmd_plan("ffmpeg", _ok_clip(with_wav=True), Path("o.mov"), PF, plan, "crop")
    s = " ".join(cmd)
    assert "0:v:0" not in s and "-c:v" not in s
    assert "-c:a:0 alac" in s


def test_plan_disable_wav_leaves_camera():
    plan = OutputPlan(tracks=[OutputTrack("camera"), OutputTrack("wav", enabled=False),
                              OutputTrack("mix", enabled=False)])
    cmd = build_mux_cmd_plan("ffmpeg", _ok_clip(with_wav=True), Path("o.mov"), PF, plan, "crop")
    s = " ".join(cmd)
    assert "-c:a:0 copy" in s and "alac" not in s and "[mix]" not in s


def test_plan_transcode_drops_mix_keeps_audio():
    clip = ClipInfo(path=Path("c.mp4"),
                    stream=StreamInfo(status="transcode", width=1920, height=1080,
                                      conflicts=["1920×1080"], audio_codec="aac"))
    clip.wav_path = Path("c.wav")
    plan = OutputPlan(tracks=[OutputTrack("camera"), OutputTrack("wav"), OutputTrack("mix")])
    cmd = build_mux_cmd_plan("ffmpeg", clip, Path("o.mov"), PF, plan, "crop")
    s = " ".join(cmd)
    assert "libx265" in s and "scale=3840:2160" in s
    assert "[mix]" not in s                  # mix dropped on transcode
    assert "-c:a:0 copy" in s and "-c:a:1 alac" in s


def test_atempo_chain_slows_to_target():
    import re
    from core.ffmpeg_cmd import atempo_chain
    chain = atempo_chain(12.0 / 48.0)          # 0.25 → must split (atempo ≥ 0.5)
    factors = [float(x) for x in re.findall(r"atempo=([0-9.]+)", chain)]
    prod = 1.0
    for f in factors:
        prod *= f
    assert abs(prod - 0.25) < 1e-4
    assert all(0.5 <= f <= 2.0 for f in factors)


def test_slowmo_builds_stretched_primary():
    clip = ClipInfo(path=Path("s.mp4"),
                    stream=StreamInfo(status="ok", width=3840, height=2160,
                                      duration=48.0, audio_codec=""))
    clip.wav_path = Path("s.wav")
    clip.wav_duration = 12.0
    cmd = build_mux_cmd_plan("ffmpeg", clip, Path("o.mov"), PF, OutputPlan(), "crop")
    s = " ".join(cmd)
    assert "atempo=" in s and "[1:a:0]" in s and "[s]" in s
    assert "-c:a:0 aac" in s            # stretched WAV is the primary track
    assert "-c:a:1 alac" in s           # original WAV preserved (lossless)
    assert "Synced Audio (WAV stretched to video)" in s
    assert "-disposition:a:0 default" in s


def test_slowmo_uniform_two_slots():
    # Uniform layout: slow-mo fills the primary slot with the stretched WAV; the
    # WAV backup slot stays ALAC. (The default plan has 2 enabled audio slots.)
    clip = ClipInfo(path=Path("s.mp4"),
                    stream=StreamInfo(status="ok", width=3840, height=2160,
                                      duration=40.0, audio_codec="aac"))
    clip.wav_path = Path("s.wav")
    clip.wav_duration = 10.0
    cmd = build_mux_cmd_plan("ffmpeg", clip, Path("o.mov"), PF, OutputPlan(), "crop")
    s = " ".join(cmd)
    assert "atempo=" in s and "[s]" in s
    assert "-c:a:0 aac" in s and "-c:a:1 alac" in s and "-c:a:2" not in s


def test_normal_clip_is_not_slowmo():
    # WAV slightly longer than video (recorder ran longer) is NOT slow-mo.
    clip = _ok_clip(with_wav=True)
    clip.stream.duration = 100.0
    clip.wav_duration = 110.0
    cmd = build_mux_cmd_plan("ffmpeg", clip, Path("o.mov"), PF, OutputPlan(), "crop")
    assert "atempo" not in " ".join(cmd)


def test_plan_no_camera_uniform_slots():
    # MP4 with no audio + a WAV: primary slot uses the WAV (AAC), backup slot is
    # the WAV (ALAC), mix slot is silenced — slots stay uniform, no 0:a:0 map.
    clip = _ok_clip(with_wav=True, cam_audio="")
    plan = OutputPlan(tracks=[OutputTrack("camera"), OutputTrack("wav"), OutputTrack("mix")])
    cmd = build_mux_cmd_plan("ffmpeg", clip, Path("o.mov"), PF, plan, "crop")
    s = " ".join(cmd)
    assert "-map 0:a:0" not in s          # no camera audio to map
    assert "-c:a:0 aac" in s              # primary = WAV → AAC
    assert "-c:a:1 alac" in s             # backup = WAV → ALAC
    assert "anullsrc" in s                # mix slot silenced to keep the layout
    assert "-c:a:2 aac" in s


def test_plan_no_audio_no_wav_silent_uniform_tracks():
    # No camera, no WAV → silent tracks so the layout matches other clips.
    clip = _ok_clip(with_wav=False, cam_audio="")
    plan = OutputPlan(tracks=[OutputTrack("camera"), OutputTrack("wav")])
    cmd = build_mux_cmd_plan("ffmpeg", clip, Path("o.mov"), PF, plan, "crop")
    s = " ".join(cmd)
    assert "0:v:0" in s and "-c:v copy" in s
    assert "anullsrc" in s
    assert "-c:a:0 aac" in s and "-c:a:1 alac" in s   # silent AAC + silent ALAC


def test_camera_title_onboard_without_wav():
    # No WAV → camera audio is labelled the on-board mic.
    plan = OutputPlan(tracks=[OutputTrack("camera")])
    cmd = build_mux_cmd_plan("ffmpeg", _ok_clip(with_wav=False), Path("o.mov"), PF, plan, "crop")
    s = " ".join(cmd)
    assert "Camera Audio (On-board mic)" in s
    assert "Bluetooth" not in s


def test_camera_title_bluetooth_with_wav():
    plan = OutputPlan(tracks=[OutputTrack("camera"), OutputTrack("wav")])
    cmd = build_mux_cmd_plan("ffmpeg", _ok_clip(with_wav=True), Path("o.mov"), PF, plan, "crop")
    assert "Camera Audio (Bluetooth mic)" in " ".join(cmd)


def test_plan_preset_matches_camera_mix():
    plan = OutputPlan.preset("camera", mix_enabled=True, mix_kind="lr",
                             mix_make_default=False, mix_match_levels=False)
    kinds = [t.kind for t in plan.tracks]
    assert kinds == ["camera", "wav", "mix"]
    plan2 = OutputPlan.preset("wav", mix_enabled=True, mix_kind="lr",
                              mix_make_default=True, mix_match_levels=False)
    assert plan2.tracks[0].kind == "mix"     # promoted to default


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"\n{len(fns)} ffmpeg_cmd tests passed.")
