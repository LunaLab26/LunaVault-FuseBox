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
    hms_to_seconds, build_mux_cmd, build_concat_cmd, build_concat_reencode_cmd,
    build_whatsapp_cmd, build_preview_cmd, build_thumbnail_cmd,
    MixSpec, OutputPlan, OutputTrack, build_mux_cmd_plan,
    build_archival_concat_cmd, build_final_archival_mux_cmd, build_wav_archival_mux_cmd,
    build_lrv_archival_mux_cmd,
    transcode_vf_parts, ConformSpec, _video_encoder_args, build_clip_sample_cmd,
    _override_fill,
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


def test_transcode_vf_parts_src_override_ignores_clip_conflicts():
    # No conflicts at all (a clip that matches the baseline) — without an
    # override this would return no filters; the LRV-proxy override forces
    # scale/pad unconditionally since clip.conflicts describes the CLIP's own
    # spec, not the proxy's.
    clip = _clip([], w=3840, h=2160)   # empty conflicts — "matches baseline"
    parts = transcode_vf_parts(clip, "pad", src_width=1280, src_height=720)
    joined = ",".join(parts)
    assert "force_original_aspect_ratio=decrease" in joined and "pad=3840:2160" in joined


def test_transcode_vf_parts_src_override_crop_mode_uses_square_check_on_override_dims():
    clip = _clip([], w=3840, h=2160)   # clip itself isn't square
    parts = transcode_vf_parts(clip, "crop", src_width=720, src_height=720)   # proxy IS square
    assert "crop=" in parts[0]


def test_transcode_vf_parts_no_override_unaffected():
    # Same clip, no override params — falls back to the existing conflict-based path.
    clip = _clip([], w=3840, h=2160)
    assert transcode_vf_parts(clip, "pad") == []


def test_encoder_args_switch_codec():
    assert "libx265" in _video_encoder_args(ConformSpec(codec="hevc"))
    h264 = _video_encoder_args(ConformSpec(codec="h264", pix_fmt="yuv420p"))
    assert "libx264" in h264 and "hvc1" not in h264 and "yuv420p" in h264


def test_encoder_args_bt709_default_uses_same_value_for_all_three_color_args():
    # Historical behaviour, still correct for bt709: colorspace/primaries/trc
    # all legitimately share the identifier "bt709", unlike bt2020 variants.
    args = _video_encoder_args(ConformSpec(codec="hevc"))
    assert args.count("bt709") == 3
    i = args.index("-colorspace")
    assert args[i:i + 6] == ["-colorspace", "bt709", "-color_primaries", "bt709",
                              "-color_trc", "bt709"]


def test_encoder_args_bt2020_uses_distinct_primaries_and_trc_not_the_matrix_value():
    # Real bug (found via a real Pixel HDR clip during battle-testing): ffprobe
    # reports color_space="bt2020nc" (matrix coefficients), color_primaries=
    # "bt2020", color_transfer="arib-std-b67" (HLG) as three DIFFERENT fields —
    # feeding "bt2020nc" into -color_primaries/-color_trc makes libx265 reject
    # the command outright ("Unable to parse "color_primaries" option value
    # "bt2020nc""). Each ffmpeg arg must get its own probed value.
    args = _video_encoder_args(ConformSpec(codec="hevc", color_space="bt2020nc",
                                           color_primaries="bt2020",
                                           color_transfer="arib-std-b67"))
    i = args.index("-colorspace")
    assert args[i:i + 6] == ["-colorspace", "bt2020nc", "-color_primaries", "bt2020",
                              "-color_trc", "arib-std-b67"]


def test_encoder_args_color_primaries_and_transfer_fall_back_to_color_space_when_unset():
    # A caller that only sets color_space (e.g. an older code path, or a clip
    # that only probed a matrix value) must not regress to an invalid pairing —
    # falls back to the one shared value, matching pre-fix behaviour.
    args = _video_encoder_args(ConformSpec(codec="h264", color_space="bt2020c"))
    i = args.index("-colorspace")
    assert args[i:i + 6] == ["-colorspace", "bt2020c", "-color_primaries", "bt2020c",
                              "-color_trc", "bt2020c"]


def test_encoder_args_hw_encoder_off_by_default_even_with_ff_given():
    # Default ConformSpec.hw_encoder == "off" — passing `ff` must not matter.
    args = _video_encoder_args(ConformSpec(codec="hevc"), ff="ffmpeg")
    assert "libx265" in args


def test_encoder_args_no_ff_never_engages_hw_even_if_requested():
    # `ff` is needed to run the detection probe; without it "auto"/"nvenc" etc.
    # must fall back to software rather than crash.
    args = _video_encoder_args(ConformSpec(codec="hevc", hw_encoder="auto"), ff=None)
    assert "libx265" in args


def test_encoder_args_explicit_vendor_skips_detection_probe():
    # Requesting a vendor by name (not "auto") must use it directly without
    # calling detect_best_hw — verified by making detect_best_hw explode if hit.
    import core.gpu_encode as ge
    real_detect = ge.detect_best_hw
    ge.detect_best_hw = lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not probe"))
    try:
        args = _video_encoder_args(ConformSpec(codec="hevc", pix_fmt="yuv420p10le",
                                               hw_encoder="qsv"), ff="ffmpeg")
    finally:
        ge.detect_best_hw = real_detect
    assert "hevc_qsv" in args and "libx265" not in args
    assert "p010le" in args   # 10-bit conform maps to the p010le hw surface format


def test_encoder_args_auto_falls_back_to_software_when_no_gpu_works():
    import core.gpu_encode as ge
    real_detect = ge.detect_best_hw
    ge.detect_best_hw = lambda ff, codec: None
    try:
        args = _video_encoder_args(ConformSpec(codec="hevc", hw_encoder="auto"), ff="ffmpeg")
    finally:
        ge.detect_best_hw = real_detect
    assert "libx265" in args


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


def test_wav_archival_mux_appends_streams_stream_copy_non_default():
    cmd = build_wav_archival_mux_cmd(
        "ffmpeg", Path("master.mov"), [Path("c1.wav"), Path("c2.wav")], existing_audio_count=2,
        output=Path("out.mov"), progress_file=PF, extra_out_args=["-metadata", "k=v"])
    assert cmd.count("-i") == 3   # master + 2 wav files
    map_targets = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]
    # explicit video+audio, NOT a blanket "0" — avoids pulling in a hidden
    # chapter-text stream that conflicts with this file's own regenerated chapters
    assert map_targets[0] == "0:v" and map_targets[1] == "0:a?"
    assert "1:a:0" in map_targets and "2:a:0" in map_targets
    assert "copy" in cmd and "-map_chapters" in cmd
    # new streams (indices 2, 3 given existing_audio_count=2) explicitly non-default;
    # the master's own existing audio streams are untouched (no disposition override).
    assert cmd[cmd.index("-disposition:a:2") + 1] == "0"
    assert cmd[cmd.index("-disposition:a:3") + 1] == "0"
    assert "-disposition:a:0" not in cmd and "-disposition:a:1" not in cmd
    assert cmd[-1] == "out.mov"


def test_lrv_archival_mux_appends_video_and_audio_non_default():
    cmd = build_lrv_archival_mux_cmd(
        "ffmpeg", Path("master.mov"), [Path("c1.lrv"), Path("c2.lrv")],
        existing_video_count=1, existing_audio_count=2,
        output=Path("out.mov"), progress_file=PF)
    assert cmd.count("-i") == 3   # master + 2 lrv files
    map_targets = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]
    assert map_targets[0] == "0:v" and map_targets[1] == "0:a?"
    assert "1:v?" in map_targets and "1:a?" in map_targets
    assert "2:v?" in map_targets and "2:a?" in map_targets
    assert "copy" in cmd
    # new streams non-default; existing baseline streams untouched
    assert cmd[cmd.index("-disposition:v:1") + 1] == "0"
    assert cmd[cmd.index("-disposition:v:2") + 1] == "0"
    assert cmd[cmd.index("-disposition:a:2") + 1] == "0"
    assert cmd[cmd.index("-disposition:a:3") + 1] == "0"
    assert "-disposition:v:0" not in cmd and "-disposition:a:0" not in cmd
    assert cmd[-1] == "out.mov"


def test_wav_archival_mux_no_files_is_a_no_op_pass_through():
    cmd = build_wav_archival_mux_cmd(
        "ffmpeg", Path("master.mov"), [], existing_audio_count=2,
        output=Path("out.mov"), progress_file=PF)
    assert cmd.count("-i") == 1
    assert "-disposition:a:0" not in cmd


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


def test_concat_reencode_cmd_rebuilds_a_clean_playable_baseline():
    # The fix for broken-splice playback: the baseline concat must RE-ENCODE the
    # video into one continuous, widely-compatible stream (8-bit H.264), not
    # stream-copy independently-encoded segments (which severs reference
    # continuity at the joins -> green frames/freezes/static, differently per
    # player). Audio stays a stream copy (concat-safe for playback).
    cmd = build_concat_reencode_cmd("ffmpeg", Path("list.txt"), Path("ch.txt"),
                                    Path("out.mov"), PF, crf=20)
    s = " ".join(cmd)
    assert "-f concat" in s                       # still the concat demuxer as input
    assert "-c:v libx264" in s                     # video RE-ENCODED, not copied
    assert "-pix_fmt yuv420p" in s                 # 8-bit for maximum device support
    assert "-profile:v high" in s
    assert "-crf 20" in s
    assert "-c:a copy" in s                        # audio still copied (playback-safe)
    assert "-c copy" not in s                       # crucially NOT a blanket stream copy
    assert "+faststart" in s                        # moov atom up front for streaming
    assert "-map_metadata 1" in s                   # chapters preserved


def test_concat_reencode_cmd_appends_extra_out_args_before_output():
    cmd = build_concat_reencode_cmd("ffmpeg", Path("l.txt"), Path("c.txt"), Path("out.mov"), PF,
                                    extra_out_args=["-metadata", "k=v"])
    assert cmd[-1] == "out.mov"
    assert cmd.index("k=v") < cmd.index("out.mov")


def test_archival_concat_maps_only_video_and_audio():
    # NOT a blanket "-map 0" — real camera files can carry extra data streams
    # (e.g. a Pixel phone's "mett" motion-photo/telemetry track) that a blanket
    # map would pull in and the MOV muxer can't stream-copy.
    cmd = build_archival_concat_cmd("ffmpeg", Path("grp.txt"), Path("arch.mov"))
    assert "-f" in cmd and "concat" in cmd and "-c" in cmd and "copy" in cmd
    map_targets = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]
    assert map_targets == ["0:v:0", "0:a:0?"]
    assert cmd[-1] == "arch.mov"


def test_final_archival_mux_maps_and_dispositions():
    cmd = build_final_archival_mux_cmd(
        "ffmpeg", Path("base.mov"), [Path("a1.mov"), Path("a2.mov")], Path("out.mov"), PF,
        extra_out_args=["-metadata", "k=v"])
    # baseline + 2 archival inputs
    assert cmd.count("-i") == 3
    # baseline maps explicit video+audio (NOT a blanket "0" — that would also
    # pull in the hidden chapter-text data stream MOV chapters create, which
    # breaks with a codec tag/id conflict when copied into a file that also
    # carries chapters — see the source comment / DEVELOPMENT.md).
    map_targets = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]
    assert map_targets[:2] == ["0:v", "0:a?"]   # "?" — a baseline with no audio tracks must not hard-error
    assert "0" not in map_targets   # no blanket baseline map
    assert "1:v" in map_targets and "1:a?" in map_targets
    assert "2:v" in map_targets and "2:a?" in map_targets
    # baseline video default, archival videos not
    assert cmd[cmd.index("-disposition:v:0") + 1] == "default"
    assert "-disposition:v:1" in cmd and "-disposition:v:2" in cmd
    # copy + metadata/chapters carried, extra args before output
    assert "copy" in cmd and "-map_chapters" in cmd
    assert cmd[-1] == "out.mov"
    assert cmd.index("k=v") < cmd.index("out.mov")


def test_final_archival_mux_audio_only_baseline_omits_video_map():
    # Advanced output -> video unchecked, Archival master still on: the
    # baseline has zero video streams, so "0:v" must be optional ("0:v?"), and
    # the "default" disposition must land on the first archival file's video
    # (output v:0) instead of the nonexistent baseline video (the old
    # hardcoded v:0/v:1.. indices assumed the baseline always owned v:0 —
    # confirmed as a real crash: "Stream map '' matches no streams" on a real
    # audio-only + Archival master export).
    cmd = build_final_archival_mux_cmd(
        "ffmpeg", Path("base.mov"), [Path("a1.mov"), Path("a2.mov")], Path("out.mov"), PF,
        base_has_video=False)
    map_targets = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]
    assert map_targets[:2] == ["0:v?", "0:a?"]
    assert "1:v" in map_targets and "2:v" in map_targets
    assert cmd[cmd.index("-disposition:v:0") + 1] == "default"
    assert cmd[cmd.index("-disposition:v:1") + 1] == "0"
    assert "-disposition:v:2" not in cmd   # only 2 archival videos -> output v:0,v:1


def test_final_archival_mux_audio_only_no_archival_video_has_no_disposition():
    # No odd-spec clips at all (archival_files empty) + no baseline video:
    # there's no video stream anywhere in the output, so no -disposition:v:*
    # should be emitted (there's nothing for it to reference).
    cmd = build_final_archival_mux_cmd(
        "ffmpeg", Path("base.mov"), [], Path("out.mov"), PF, base_has_video=False)
    assert "-disposition:v:0" not in cmd


def test_preview_and_thumbnail_single_frame():
    p = build_preview_cmd("ffmpeg", "in.mp4", "00:00:02", None, "p.jpg")
    t = build_thumbnail_cmd("ffmpeg", "in.mp4", 2.0, None, "t.jpg")
    assert "-frames:v" in p and p[-1] == "p.jpg"
    assert "-frames:v" in t and t[-1] == "t.jpg"


def test_preview_and_thumbnail_skip_to_nearest_keyframe():
    # Measured directly on a real 4K 10-bit HEVC clip: 1.9s-7.4s per frame
    # (worse deeper into the file) without -skip_frame nokey, a flat ~0.7s
    # with it — these are both rough preview references (the before/after
    # pane and the live-render/software-decode-playback frame), not
    # precision readings, so the same trade thumbnails already use applies.
    p = build_preview_cmd("ffmpeg", "in.mp4", "00:00:02", None, "p.jpg")
    assert "-skip_frame" in p and p[p.index("-skip_frame") + 1] == "nokey"
    assert p.index("-skip_frame") < p.index("-i")

    t = build_thumbnail_cmd("ffmpeg", "in.mp4", 2.0, None, "t.jpg")
    assert "-skip_frame" in t and t[t.index("-skip_frame") + 1] == "nokey"
    assert t.index("-skip_frame") < t.index("-i")


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


def test_plan_primary_override_mix_plus_native_mix_track_uses_split_pads():
    # Real bug (found via a real app crash on real footage — a "Failed" dialog
    # with "Output with label 'mix' does not exist in any defined filter
    # graph, or was already used elsewhere"): a clip's Primary-slot override
    # AND a separately-enabled Mixed Audio track can BOTH resolve to "mix" for
    # the same clip. ffmpeg filtergraph output labels are single-use — mapping
    # the same [mix] pad twice crashed with exactly that error. Each mix
    # consumer now gets its own asplit pad instead.
    plan = OutputPlan(tracks=[OutputTrack("camera"), OutputTrack("wav"), OutputTrack("mix")])
    clip = _ok_clip(with_wav=True)
    clip.primary_override = "mix"   # forces slot 0 (native "camera") to ALSO carry mix
    cmd = build_mux_cmd_plan("ffmpeg", clip, Path("o.mov"), PF, plan, "crop")
    s = " ".join(cmd)
    assert "asplit=2" in s
    assert "-map [mix0]" in s and "-map [mix1]" in s
    assert "-map [mix]" not in s   # the old single-use label must not be referenced twice
    assert "-c:a:0 aac" in s and "-c:a:2 aac" in s   # both mix-filled slots still encode correctly


def test_plan_single_mix_consumer_still_uses_the_plain_mix_label():
    # No override in play — exactly one slot resolves to "mix" — must NOT
    # regress to the split form unnecessarily.
    plan = OutputPlan(tracks=[OutputTrack("camera"), OutputTrack("wav"), OutputTrack("mix")])
    cmd = build_mux_cmd_plan("ffmpeg", _ok_clip(with_wav=True), Path("o.mov"), PF, plan, "crop")
    s = " ".join(cmd)
    assert "-map [mix]" in s
    assert "asplit" not in s and "[mix0]" not in s


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


def test_plan_no_wav_backup_falls_back_to_camera_audio():
    # A clip with camera audio but no WAV must NOT go silent on the WAV-backup
    # slot — if the "primary" choice points at that slot (file-wide default),
    # this clip would otherwise play silent even though real audio exists on
    # the camera slot. Mirrors the existing wav_aac fallback in the other
    # direction: the WAV slot falls back to the camera audio, re-encoded ALAC.
    clip = _ok_clip(with_wav=False)          # cam_audio="aac" by default → has_cam=True
    plan = OutputPlan(tracks=[OutputTrack("camera"), OutputTrack("wav")])
    cmd = build_mux_cmd_plan("ffmpeg", clip, Path("o.mov"), PF, plan, "crop")
    s = " ".join(cmd)
    assert "anullsrc" not in s                       # no silence needed — camera audio covers it
    assert s.count("-map 0:a:0") == 2                # both slots pull from the same camera input
    assert "-c:a:0 copy" in s                         # camera slot: stream copy
    assert "-c:a:1 alac" in s and "-sample_fmt:a:1 s32p" in s   # wav slot: re-encoded lossless
    assert "Backup Audio (from Camera)" in s


# ── Per-clip Primary override (_override_fill / ClipInfo.primary_override) ──

def test_override_fill_camera_into_alac_slot():
    clip = _ok_clip(with_wav=True)
    assert _override_fill("camera", "alac", clip) == ("cam_alac", "alac", "Backup Audio (from Camera)")


def test_override_fill_wav_into_aac_slot():
    clip = _ok_clip(with_wav=True)
    assert _override_fill("wav", "aac", clip) == ("wav_aac", "aac", "Primary Audio (from WAV)")


def test_override_fill_unavailable_source_returns_none():
    clip = _ok_clip(with_wav=False, cam_audio="")   # neither camera nor WAV
    assert _override_fill("camera", "aac", clip) is None
    assert _override_fill("wav", "alac", clip) is None
    assert _override_fill("mix", "aac", clip) is None


def test_override_fill_mix_requires_conform_and_both_sources():
    clip = _ok_clip(with_wav=True)
    assert _override_fill("mix", "aac", clip) == ("mix", "aac", "Combined Mix (Camera + WAV 50/50)")
    assert _override_fill("mix", "alac", clip) == ("mix_alac", "alac", "Combined Mix (Camera + WAV, Lossless)")
    clip.stream.status = "transcode"
    assert _override_fill("mix", "aac", clip) is None


def test_plan_primary_override_forces_wav_into_default_camera_slot():
    # Global primary = camera (slot 0 = AAC), but this clip overrides Primary
    # to WAV — the disposition-default slot must carry the WAV, not the camera
    # audio, even though camera audio is available too.
    clip = _ok_clip(with_wav=True)
    clip.primary_override = "wav"
    plan = OutputPlan(tracks=[OutputTrack("camera"), OutputTrack("wav")])
    cmd = build_mux_cmd_plan("ffmpeg", clip, Path("o.mov"), PF, plan, "crop")
    s = " ".join(cmd)
    assert "-c:a:0 aac" in s and "Primary Audio (from WAV)" in s
    assert "-disposition:a:0 default" in s
    # The WAV-backup slot (index 1) is untouched by the override — still its
    # own normal lossless WAV fill, a separate concern from Primary.
    assert "-c:a:1 alac" in s and "Backup WAV (Lossless)" in s


def test_plan_primary_override_forces_camera_into_default_wav_slot():
    # Global primary = wav (slot 0 = ALAC); override forces Camera instead.
    clip = _ok_clip(with_wav=True)
    clip.primary_override = "camera"
    plan = OutputPlan(tracks=[OutputTrack("wav"), OutputTrack("camera")])
    cmd = build_mux_cmd_plan("ffmpeg", clip, Path("o.mov"), PF, plan, "crop")
    s = " ".join(cmd)
    assert "-c:a:0 alac" in s and "-sample_fmt:a:0 s32p" in s
    assert "Backup Audio (from Camera)" in s
    assert "-disposition:a:0 default" in s


def test_plan_primary_override_auto_is_unaffected():
    clip_auto = _ok_clip(with_wav=True)
    clip_none = _ok_clip(with_wav=True)
    clip_auto.primary_override = "auto"
    plan = OutputPlan(tracks=[OutputTrack("camera"), OutputTrack("wav")])
    cmd_auto = " ".join(build_mux_cmd_plan("ffmpeg", clip_auto, Path("o.mov"), PF, plan, "crop"))
    cmd_none = " ".join(build_mux_cmd_plan("ffmpeg", clip_none, Path("o.mov"), PF, plan, "crop"))
    assert cmd_auto == cmd_none


def test_plan_primary_override_ignored_when_source_unavailable():
    # Clip has no WAV at all — overriding Primary to WAV can't be honoured, so
    # this must fall back to Auto (camera copy) rather than forcing silence.
    clip = _ok_clip(with_wav=False)
    clip.primary_override = "wav"
    plan = OutputPlan(tracks=[OutputTrack("camera"), OutputTrack("wav")])
    cmd = build_mux_cmd_plan("ffmpeg", clip, Path("o.mov"), PF, plan, "crop")
    s = " ".join(cmd)
    assert "-c:a:0 copy" in s          # Auto: camera audio copied, not silence


def test_plan_primary_override_skipped_for_slowmo():
    # Forcing "copy" (un-stretched camera audio) onto a slow-motion clip's
    # default slot would desync it from the pitch-corrected, time-stretched
    # video — the override must be ignored and Auto (stretch) used instead.
    clip = ClipInfo(path=Path("s.mp4"),
                    stream=StreamInfo(status="ok", width=3840, height=2160,
                                      duration=40.0, audio_codec="aac"))
    clip.wav_path = Path("s.wav")
    clip.wav_duration = 10.0
    clip.primary_override = "camera"
    cmd = build_mux_cmd_plan("ffmpeg", clip, Path("o.mov"), PF, OutputPlan(), "crop")
    s = " ".join(cmd)
    assert "atempo=" in s and "Synced Audio (WAV stretched to video)" in s


# ── Per-clip video-source override (force transcode / use LRV proxy) ────────

def test_plan_video_override_forces_transcode_on_a_matching_clip():
    clip = _ok_clip()   # status="ok" — would normally stream-copy
    clip.video_source_override = "transcode"
    cmd = build_mux_cmd_plan("ffmpeg", clip, Path("o.mov"), PF, OutputPlan(), "crop")
    s = " ".join(cmd)
    assert "-c:v copy" not in s
    assert "libx265" in s or "libx264" in s   # re-encoded, not stream-copied


def test_plan_video_override_auto_still_stream_copies():
    clip = _ok_clip()
    cmd = build_mux_cmd_plan("ffmpeg", clip, Path("o.mov"), PF, OutputPlan(), "crop")
    assert "-c:v copy" in " ".join(cmd)


def test_plan_video_override_lrv_maps_video_from_second_input():
    clip = _ok_clip(with_wav=True)
    clip.stream.duration = 300.0
    clip.lrv_path = Path("clip.lrv")
    clip.lrv_width, clip.lrv_height = 1280, 720
    clip.video_source_override = "lrv"
    cmd = build_mux_cmd_plan("ffmpeg", clip, Path("o.mov"), PF, OutputPlan(), "crop")
    s = " ".join(cmd)
    assert cmd.count("-i") == 3          # clip.path, clip.lrv_path, clip.wav_path
    assert str(clip.lrv_path) in cmd
    # video comes from input 1 (the LRV), via filter_complex since it's a non-zero input
    assert "[1:v:0]" in s
    assert "-map [v]" in s
    assert "-c:v copy" not in s
    # cut to the CLIP's own duration, not the proxy's own (they rarely match exactly)
    assert "-t" in cmd and cmd[cmd.index("-t") + 1] == f"{clip.duration:.3f}"


def _last_t_value(cmd: list) -> str:
    """The value following the LAST "-t" flag in the command — the final
    per-clip OUTPUT cutoff always comes last (any earlier "-t" belongs to an
    INPUT, e.g. the silence generator's own "-f lavfi -t {dur} -i anullsrc")."""
    idx = len(cmd) - 1 - cmd[::-1].index("-t")
    return cmd[idx + 1]


def test_plan_normal_clip_with_wav_gets_duration_cutoff():
    # A real, high-impact bug found this way: without an explicit -t on the
    # per-clip mux output, ffmpeg has no -shortest either, so the per-clip
    # temp file's container duration follows the LONGEST stream — not the
    # video — whenever the paired WAV runs longer than the clip's own video
    # (common: a WAV recorder often keeps rolling a beat past the camera
    # stopping; a clip-split WAV that still carries the NEXT clip's audio can
    # overrun by that clip's ENTIRE duration). The concat demuxer then
    # advances by the inflated duration, drifting every later clip's
    # presentation position and leaving the video decoder holding a frozen
    # last frame for the difference — confirmed directly on a real merge
    # (one clip's segment ran ~384s longer than its own video). This cutoff
    # used to exist ONLY for the LRV-proxy-swap path; it must apply to the
    # ordinary stream-copy case too.
    clip = _ok_clip(with_wav=True)
    clip.stream.duration = 60.0
    cmd = build_mux_cmd_plan("ffmpeg", clip, Path("o.mov"), PF, OutputPlan(), "crop")
    assert "-c:v copy" in " ".join(cmd)   # the ordinary conforming stream-copy path
    assert "-t" in cmd and _last_t_value(cmd) == f"{clip.duration:.3f}"


def test_plan_clip_without_wav_still_gets_duration_cutoff():
    # No WAV at all (camera audio only) is the simplest case, but the cutoff
    # must still be unconditional — nothing about this fix is WAV-specific.
    clip = _ok_clip(with_wav=False)
    clip.stream.duration = 60.0
    cmd = build_mux_cmd_plan("ffmpeg", clip, Path("o.mov"), PF, OutputPlan(), "crop")
    assert "-t" in cmd and _last_t_value(cmd) == f"{clip.duration:.3f}"


def test_plan_transcoding_clip_with_wav_gets_duration_cutoff():
    # Same fix, the TRANSCODE (non-conform) path — the cutoff must not be
    # accidentally tied to the stream-copy branch either.
    st = StreamInfo(status="transcode", width=1280, height=960, audio_codec="aac", duration=60.0)
    clip = ClipInfo(path=Path("clip.mp4"), stream=st)
    clip.wav_path = Path("clip.wav")
    cmd = build_mux_cmd_plan("ffmpeg", clip, Path("o.mov"), PF, OutputPlan(), "crop")
    assert "-c:v copy" not in " ".join(cmd)   # genuinely transcoding
    assert "-t" in cmd and _last_t_value(cmd) == f"{clip.duration:.3f}"


def test_plan_video_override_lrv_ignored_when_no_lrv_paired():
    clip = _ok_clip()
    clip.video_source_override = "lrv"   # no lrv_path set
    cmd = build_mux_cmd_plan("ffmpeg", clip, Path("o.mov"), PF, OutputPlan(), "crop")
    # falls back to Auto — already matches spec, so still stream-copied
    assert "-c:v copy" in " ".join(cmd)
    assert cmd.count("-i") == 1


def test_plan_video_override_lrv_scales_using_proxys_own_dimensions():
    clip = _ok_clip()
    clip.lrv_path = Path("clip.lrv")
    clip.lrv_width, clip.lrv_height = 1280, 720
    clip.video_source_override = "lrv"
    cmd = build_mux_cmd_plan("ffmpeg", clip, Path("o.mov"), PF, OutputPlan(), "crop")
    s = " ".join(cmd)
    assert "pad=3840:2160" in s or "scale=3840:2160" in s


def test_plan_no_audio_no_wav_silent_uniform_tracks():
    # No camera, no WAV → silent tracks so the layout matches other clips.
    clip = _ok_clip(with_wav=False, cam_audio="")
    plan = OutputPlan(tracks=[OutputTrack("camera"), OutputTrack("wav")])
    cmd = build_mux_cmd_plan("ffmpeg", clip, Path("o.mov"), PF, plan, "crop")
    s = " ".join(cmd)
    assert "0:v:0" in s and "-c:v copy" in s
    assert "anullsrc" in s
    assert "-c:a:0 aac" in s and "-c:a:1 alac" in s   # silent AAC + silent ALAC
    assert "-sample_fmt:a:1 s32p" in s   # silence-filled ALAC still gets a forced sample format


def test_plan_alac_sample_format_is_forced_and_matches_real_and_silent_fills():
    # Root-caused directly: ffmpeg's ALAC encoder auto-picks a bit depth from
    # whatever it's fed. A real WAV backup (often 24-in-32-bit source) and the
    # anullsrc silence filler used for a clip with no WAV default to DIFFERENT
    # bit depths (24-bit vs 16-bit) — concatenating segments that declare
    # different ALAC bit depths corrupts the merged track at the seam (confirmed
    # directly: decoding threw hundreds of "invalid element channel count" /
    # "invalid zero block size" errors). Every ALAC-coded slot, whether backed by
    # a real WAV or silence, must force the SAME -sample_fmt so every clip's
    # segment in a merge declares identical parameters and concatenates cleanly.
    plan = OutputPlan(tracks=[OutputTrack("camera"), OutputTrack("wav")])

    with_wav = build_mux_cmd_plan("ffmpeg", _ok_clip(with_wav=True), Path("o.mov"), PF, plan, "crop")
    s_with_wav = " ".join(with_wav)
    assert "-c:a:1 alac" in s_with_wav
    assert "-sample_fmt:a:1 s32p" in s_with_wav

    no_wav = build_mux_cmd_plan("ffmpeg", _ok_clip(with_wav=False, cam_audio=""),
                                Path("o.mov"), PF, plan, "crop")
    s_no_wav = " ".join(no_wav)
    assert "-c:a:1 alac" in s_no_wav      # silence-filled, but still ALAC for track-layout consistency
    assert "-sample_fmt:a:1 s32p" in s_no_wav

    # the two clips must declare the IDENTICAL sample format for their ALAC
    # track, whichever source fed it — that's what keeps a merge's concatenated
    # WAV-backup track decodable regardless of which clips have a real backup.
    fmt = lambda s: s.split("-sample_fmt:a:1 ")[1].split()[0]
    assert fmt(s_with_wav) == fmt(s_no_wav) == "s32p"


def test_camera_title_onboard_without_wav():
    # No WAV → camera audio is labelled the on-board mic.
    plan = OutputPlan(tracks=[OutputTrack("camera")])
    cmd = build_mux_cmd_plan("ffmpeg", _ok_clip(with_wav=False), Path("o.mov"), PF, plan, "crop")
    s = " ".join(cmd)
    assert "Camera Audio (On-board mic)" in s
    assert "Bluetooth" not in s


def test_camera_title_aac_with_wav():
    plan = OutputPlan(tracks=[OutputTrack("camera"), OutputTrack("wav")])
    cmd = build_mux_cmd_plan("ffmpeg", _ok_clip(with_wav=True), Path("o.mov"), PF, plan, "crop")
    s = " ".join(cmd)
    assert "Camera Audio (AAC)" in s
    assert "Bluetooth" not in s


def test_plan_preset_matches_camera_mix():
    plan = OutputPlan.preset("camera", mix_enabled=True, mix_kind="lr",
                             mix_make_default=False, mix_match_levels=False)
    kinds = [t.kind for t in plan.tracks]
    assert kinds == ["camera", "wav", "mix"]
    plan2 = OutputPlan.preset("wav", mix_enabled=True, mix_kind="lr",
                              mix_make_default=True, mix_match_levels=False)
    assert plan2.tracks[0].kind == "mix"     # promoted to default


def test_clip_sample_defaults_to_software_x264():
    cmd = build_clip_sample_cmd("ffmpeg", "in.mp4", 2.0, 5.0, "out.mp4")
    s = " ".join(cmd)
    assert "-hwaccel" not in s, "no GPU decode unless asked"
    assert "-c:v libx264 -preset veryfast" in s
    assert "scale=-2:160" in s


def test_clip_sample_fast_uses_ultrafast():
    cmd = build_clip_sample_cmd("ffmpeg", "in.mp4", 2.0, 2.0, "out.mp4", fast=True)
    assert "-preset ultrafast" in " ".join(cmd)


def test_clip_sample_hw_decode_prepends_hwaccel_before_input():
    cmd = build_clip_sample_cmd("ffmpeg", "in.mp4", 2.0, 5.0, "out.mp4", hw_decode=True)
    assert cmd[:4] == ["ffmpeg", "-y", "-hwaccel", "auto"]
    # -hwaccel must come before the input it applies to
    assert cmd.index("-hwaccel") < cmd.index("-i")


def test_clip_sample_gpu_vendor_swaps_encoder():
    cmd = build_clip_sample_cmd("ffmpeg", "in.mp4", 2.0, 5.0, "out.mp4", gpu_vendor="nvenc")
    s = " ".join(cmd)
    assert "h264_nvenc" in s and "libx264" not in s
    assert "-pix_fmt nv12" in s
    # gpu encode overrides the fast (libx264) preset path
    cmd2 = build_clip_sample_cmd("ffmpeg", "in.mp4", 2.0, 5.0, "out.mp4",
                                 gpu_vendor="qsv", fast=True)
    assert "h264_qsv" in " ".join(cmd2) and "ultrafast" not in " ".join(cmd2)


def _integration_wav_overrun_is_trimmed() -> bool:
    """Real ffmpeg, no mocking: build a clip whose paired WAV genuinely runs
    longer than its own video (the exact real-world shape of the bug — a
    clip-split WAV still carrying the next clip's audio, or simply a WAV
    recorder rolling a beat past the camera stopping) and prove the per-clip
    mux OUTPUT is bounded to the clip's own video duration, not the WAV's.
    Before this fix: the container duration followed the longer WAV stream —
    reproduced directly and confirmed as the root cause of a real "frozen
    frame" report (see DEVELOPMENT.md)."""
    import subprocess
    import tempfile
    from core.binaries import get_ffmpeg

    ff, fp = get_ffmpeg()
    if not Path(ff).exists():
        print("  (skipped integration: ffmpeg not found)")
        return True
    d = Path(tempfile.mkdtemp())

    video_secs, wav_secs = 3.0, 6.0   # WAV overruns the video by the full 3s difference
    video_path = d / "clip.mp4"
    wav_path = d / "clip.wav"
    # Give the synthetic clip a real (short) camera-audio track too — the
    # StreamInfo below declares audio_codec="aac" (has_camera_audio() ->
    # True), so build_mux_cmd_plan maps 0:a:0 for it; a video-only file would
    # make that map target nonexistent and fail for an unrelated reason.
    subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi",
                   "-i", f"testsrc=size=640x360:rate=30:duration={video_secs}",
                   "-f", "lavfi", "-i", f"sine=frequency=220:duration={video_secs}",
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                   str(video_path)], check=True)
    subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi",
                   "-i", f"sine=frequency=440:duration={wav_secs}",
                   "-c:a", "pcm_s24le", str(wav_path)], check=True)

    st = StreamInfo(status="ok", width=640, height=360, audio_codec="aac", duration=video_secs)
    clip = ClipInfo(path=video_path, stream=st)
    clip.wav_path = wav_path

    out = d / "mux.mov"
    cmd = build_mux_cmd_plan(str(ff), clip, out, Path(d / "progress.txt"), OutputPlan(), "crop")
    subprocess.run(cmd, check=True, capture_output=True)

    def _fmt_duration(path) -> float:
        r = subprocess.run([fp, "-v", "error", "-show_entries", "format=duration",
                           "-of", "default=nw=1:nk=1", str(path)],
                          capture_output=True, text=True, check=True)
        return float(r.stdout.strip())

    got = _fmt_duration(out)
    assert abs(got - video_secs) < 0.1, (
        f"per-clip mux container duration was {got:.3f}s, expected ~{video_secs:.3f}s "
        f"(the clip's own video duration) — the {wav_secs:.0f}s WAV must NOT be allowed "
        f"to inflate it")
    print(f"  real per-clip mux: {wav_secs:.0f}s WAV correctly trimmed to "
         f"{video_secs:.0f}s video duration (got {got:.3f}s) — OK")
    return True


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"\n{len(fns)} ffmpeg_cmd tests passed.")
    print("running real ffmpeg integration...")
    _integration_wav_overrun_is_trimmed()
    print("test_ffmpeg_cmd: all tests passed")
