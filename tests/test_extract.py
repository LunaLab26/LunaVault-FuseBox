"""Tests for core/extract.py — manifest-driven clip recovery planning.
Runs under pytest and standalone (`python tests/test_extract.py`)."""

import sys
import tempfile
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.manifest import ClipEntry, Manifest, spec_signature  # noqa: E402
from core.extract import (  # noqa: E402
    compute_baseline_offsets, build_recovery_plan,
    build_recover_clip_cmd, build_recover_wav_cmd, recovered_filenames,
    build_generic_recovery_plans, build_generic_recover_clip_cmd,
    generic_recovered_filename, build_recover_wav_archival_cmd,
    build_generic_recover_wav_cmd, GenericRecoveryPlan,
    build_recover_lrv_archival_cmd,
)
from probe import ChapterInfo  # noqa: E402


def _mixed_manifest() -> Manifest:
    """Clip 0 conforms (baseline chapter 0, 10s); clip 1 conforms (chapter 1,
    6s); clip 2 is a lone archival original (own track, 4s); clip 3+4 are a
    concatenated archival pair (shared track, 3s then 5s)."""
    return Manifest(
        master_filename="master.mov",
        baseline_audio_tracks={"camera": 0, "wav": 1},
        clips=[
            ClipEntry(source_filename="A.mp4", duration=10.0, conform_status="ok",
                      baseline_chapter_index=0, has_camera_audio=True, has_wav=True),
            ClipEntry(source_filename="B.mp4", duration=6.0, conform_status="ok",
                      baseline_chapter_index=1, has_camera_audio=True, has_wav=False),
            ClipEntry(source_filename="C.mp4", duration=4.0, conform_status="transcode",
                      baseline_chapter_index=2, has_camera_audio=True, has_wav=True,
                      archival_track=1, archival_audio_stream=2,
                      in_track_start=0.0, in_track_duration=4.0),
            ClipEntry(source_filename="D.mp4", duration=3.0, conform_status="transcode",
                      baseline_chapter_index=3, has_camera_audio=True, has_wav=False,
                      archival_track=2, archival_audio_stream=3,
                      in_track_start=0.0, in_track_duration=3.0),
            ClipEntry(source_filename="E.mp4", duration=5.0, conform_status="transcode",
                      baseline_chapter_index=4, has_camera_audio=False, has_wav=False,
                      archival_track=2, archival_audio_stream=None,
                      in_track_start=3.0, in_track_duration=5.0),
        ],
    )


def test_baseline_offsets_are_cumulative_across_every_clip():
    offs = compute_baseline_offsets(_mixed_manifest())
    assert offs[0] == (0.0, 10.0)
    assert offs[1] == (10.0, 6.0)
    assert offs[2] == (16.0, 4.0)     # archival clips still occupy a baseline chapter
    assert offs[3] == (20.0, 3.0)
    assert offs[4] == (23.0, 5.0)


def test_conforming_clip_recovers_from_baseline_video_and_audio_and_wav():
    m = _mixed_manifest()
    plan = build_recovery_plan(m, m.clips[0])
    assert plan.video_stream == 0 and (plan.video_start, plan.video_duration) == (0.0, 10.0)
    assert plan.audio_stream == 0        # baseline "camera" track
    assert plan.wav_stream == 1 and (plan.wav_start, plan.wav_duration) == (0.0, 10.0)
    assert plan.bit_exact is True


def test_recovery_plan_carries_wav_archival_stream_when_preserved():
    m = _mixed_manifest()
    m.clips[0].wav_archival_stream = 5   # "preserve WAV in full" was ticked for this clip
    plan = build_recovery_plan(m, m.clips[0])
    assert plan.wav_archival_stream == 5


def test_recovery_plan_wav_archival_stream_none_when_not_preserved():
    m = _mixed_manifest()
    plan = build_recovery_plan(m, m.clips[1])
    assert plan.wav_archival_stream is None


def test_recover_wav_archival_cmd_is_a_plain_stream_copy():
    m = _mixed_manifest()
    m.clips[0].wav_archival_stream = 5
    plan = build_recovery_plan(m, m.clips[0])
    cmd = build_recover_wav_archival_cmd("ffmpeg", "master.mov", plan, "out.wav")
    assert "-map" in cmd and cmd[cmd.index("-map") + 1] == "0:a:5"
    assert "-c" in cmd and cmd[cmd.index("-c") + 1] == "copy"
    assert "-ss" not in cmd and "-t" not in cmd   # the whole standalone track, untrimmed
    assert cmd[-1] == "out.wav"


def test_recovery_plan_carries_lrv_archival_tracks_when_preserved():
    m = _mixed_manifest()
    m.clips[0].lrv_video_archival_track = 2
    m.clips[0].lrv_audio_archival_track = 4
    plan = build_recovery_plan(m, m.clips[0])
    assert plan.lrv_video_archival_track == 2
    assert plan.lrv_audio_archival_track == 4


def test_recovery_plan_lrv_archival_tracks_none_when_not_preserved():
    m = _mixed_manifest()
    plan = build_recovery_plan(m, m.clips[1])
    assert plan.lrv_video_archival_track is None
    assert plan.lrv_audio_archival_track is None


def test_recover_lrv_archival_cmd_maps_video_and_audio():
    m = _mixed_manifest()
    m.clips[0].lrv_video_archival_track = 2
    m.clips[0].lrv_audio_archival_track = 4
    plan = build_recovery_plan(m, m.clips[0])
    cmd = build_recover_lrv_archival_cmd("ffmpeg", "master.mov", plan, "out.mov")
    map_targets = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]
    assert map_targets == ["0:v:2", "0:a:4"]
    assert "-c" in cmd and cmd[cmd.index("-c") + 1] == "copy"
    assert cmd[-1] == "out.mov"


def test_recover_lrv_archival_cmd_omits_audio_map_when_proxy_had_none():
    m = _mixed_manifest()
    m.clips[0].lrv_video_archival_track = 2
    # lrv_audio_archival_track left None — a video-only proxy
    plan = build_recovery_plan(m, m.clips[0])
    cmd = build_recover_lrv_archival_cmd("ffmpeg", "master.mov", plan, "out.mov")
    map_targets = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]
    assert map_targets == ["0:v:2"]


def test_conforming_clip_without_wav_has_no_wav_plan():
    m = _mixed_manifest()
    plan = build_recovery_plan(m, m.clips[1])
    assert plan.video_start == 10.0
    assert plan.wav_stream is None


def test_lone_archival_clip_is_bit_exact_and_uses_its_own_track():
    m = _mixed_manifest()
    plan = build_recovery_plan(m, m.clips[2])
    assert plan.video_stream == 1 and plan.audio_stream == 2
    assert (plan.video_start, plan.video_duration) == (0.0, 4.0)
    assert plan.bit_exact is True
    # WAV still comes from the baseline, at this clip's OWN chapter offset (16s),
    # not from the archival track.
    assert plan.wav_stream == 1 and plan.wav_start == 16.0


def test_concat_group_first_member_is_bit_exact_second_is_not():
    m = _mixed_manifest()
    plan_d = build_recovery_plan(m, m.clips[3])   # starts its shared track at 0
    plan_e = build_recovery_plan(m, m.clips[4])   # starts at offset 3.0 -> concat boundary
    assert plan_d.bit_exact is True
    assert plan_e.bit_exact is False
    assert plan_e.video_stream == 2 and plan_e.video_start == 3.0
    assert plan_e.audio_stream is None   # clip E had no camera audio to begin with


def test_recover_clip_cmd_maps_video_and_audio_with_input_side_seek():
    m = _mixed_manifest()
    plan = build_recovery_plan(m, m.clips[0])
    cmd = build_recover_clip_cmd("ffmpeg", "master.mov", plan, "A.mp4")
    assert cmd[cmd.index("-ss") + 1] == "0.000"
    assert cmd.index("-ss") < cmd.index("-i")   # INPUT-side seek (keyframe snap), not output-side
    map_targets = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]
    assert "0:v:0" in map_targets and "0:a:0" in map_targets   # TYPE-relative, not absolute
    assert "copy" in cmd and cmd[-1] == "A.mp4"


def test_recover_clip_cmd_uses_archival_track_for_odd_spec_clip():
    m = _mixed_manifest()
    plan = build_recovery_plan(m, m.clips[2])   # archival_track=1, archival_audio_stream=2
    cmd = build_recover_clip_cmd("ffmpeg", "master.mov", plan, "C.mp4")
    map_targets = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]
    assert "0:v:1" in map_targets and "0:a:2" in map_targets


def test_recover_wav_cmd_decodes_to_pcm():
    m = _mixed_manifest()
    plan = build_recovery_plan(m, m.clips[0])
    cmd = build_recover_wav_cmd("ffmpeg", "master.mov", plan, "A.wav", bit_depth=24)
    assert "pcm_s24le" in cmd and cmd[-1] == "A.wav"
    assert f"0:a:{plan.wav_stream}" in cmd


def test_recovered_filenames():
    m = _mixed_manifest()
    assert recovered_filenames(m.clips[0]) == ("A.mp4", "A.wav")
    assert recovered_filenames(m.clips[1]) == ("B.mp4", None)


# ── No-manifest (chapter-based) fallback ────────────────────────────────────

def test_generic_recovery_plans_use_chapter_title_as_filename_stem():
    chapters = [
        ChapterInfo(start=0.0, end=10.0, title="VID_20260703_130055_00_004"),
        ChapterInfo(start=10.0, end=16.0, title="PXL_20260703_115902653"),
    ]
    plans = build_generic_recovery_plans(chapters, audio_track_indices=[0, 1])
    assert plans[0].title == "VID_20260703_130055_00_004"
    assert plans[0].start == 0.0 and plans[0].duration == 10.0
    assert plans[1].title == "PXL_20260703_115902653"
    assert plans[1].start == 10.0 and plans[1].duration == 6.0
    # Camera identity is guessed from the filename pattern via camera_id's own
    # cascade — Insta360 VID_ and Pixel PXL_ clips must land in different groups.
    assert plans[0].camera_id != plans[1].camera_id


def test_generic_recovery_plans_untitled_chapter_gets_positional_fallback():
    chapters = [ChapterInfo(start=0.0, end=5.0, title="")]
    plans = build_generic_recovery_plans(chapters, audio_track_indices=[])
    assert plans[0].title == "chapter_001"
    assert plans[0].audio_stream is None   # no audio tracks available to guess from


def test_generic_recovery_plans_assumes_first_audio_track_is_camera():
    chapters = [ChapterInfo(start=0.0, end=5.0, title="clip_a")]
    plans = build_generic_recovery_plans(chapters, audio_track_indices=[2, 3])
    assert plans[0].audio_stream == 2   # first of the given tracks, not a raw index guess


def test_generic_recover_clip_cmd_trims_and_maps_baseline_streams():
    chapters = [ChapterInfo(start=12.5, end=20.0, title="clip_a")]
    plan = build_generic_recovery_plans(chapters, audio_track_indices=[0])[0]
    cmd = build_generic_recover_clip_cmd("ffmpeg", "master.mov", plan, "clip_a.mov")
    assert "-ss" in cmd and cmd[cmd.index("-ss") + 1] == "12.500"
    assert "-t" in cmd and cmd[cmd.index("-t") + 1] == "7.500"
    map_targets = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]
    assert "0:v:0" in map_targets and "0:a:0" in map_targets
    assert cmd[-1] == "clip_a.mov"


def test_generic_recover_clip_cmd_omits_audio_map_when_none_available():
    chapters = [ChapterInfo(start=0.0, end=5.0, title="clip_a")]
    plan = build_generic_recovery_plans(chapters, audio_track_indices=[])[0]
    cmd = build_generic_recover_clip_cmd("ffmpeg", "master.mov", plan, "clip_a.mov")
    map_targets = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]
    assert map_targets == ["0:v:0"]


def test_generic_recovered_filename_uses_the_plan_title():
    chapters = [ChapterInfo(start=0.0, end=5.0, title="VID_001")]
    plan = build_generic_recovery_plans(chapters, audio_track_indices=[0])[0]
    assert generic_recovered_filename(plan) == "VID_001.mov"
    assert generic_recovered_filename(plan, container="mp4") == "VID_001.mp4"


# ── Manual-mode overrides (Extract tab: foreign masters with no manifest) ───

def test_generic_recovery_plans_camera_audio_override_wins_over_default():
    chapters = [ChapterInfo(start=0.0, end=5.0, title="clip_a")]
    plans = build_generic_recovery_plans(chapters, audio_track_indices=[0, 1],
                                         camera_audio_index=1)
    assert plans[0].audio_stream == 1


def test_generic_recovery_plans_wav_and_video_stream_overrides():
    chapters = [ChapterInfo(start=0.0, end=5.0, title="clip_a")]
    plans = build_generic_recovery_plans(chapters, audio_track_indices=[0, 1],
                                         camera_audio_index=0, wav_audio_index=1,
                                         video_stream_index=2)
    assert plans[0].wav_stream == 1
    assert plans[0].video_stream == 2


def test_generic_recovery_plans_default_has_no_wav_stream():
    chapters = [ChapterInfo(start=0.0, end=5.0, title="clip_a")]
    plans = build_generic_recovery_plans(chapters, audio_track_indices=[0])
    assert plans[0].wav_stream is None
    assert plans[0].rotation is None


def test_generic_recover_wav_cmd_maps_the_assigned_track():
    plan = GenericRecoveryPlan(title="clip_a", index=0, start=10.0, duration=5.0,
                               camera_id="", camera_label="", audio_stream=0, wav_stream=1)
    cmd = build_generic_recover_wav_cmd("ffmpeg", "master.mov", plan, "clip_a.wav")
    assert "-ss" in cmd and cmd[cmd.index("-ss") + 1] == "10.000"
    assert "-map" in cmd and cmd[cmd.index("-map") + 1] == "0:a:1"
    assert "pcm_s24le" in cmd
    assert cmd[-1] == "clip_a.wav"


def test_generic_recover_clip_cmd_no_rotation_override_is_untouched():
    chapters = [ChapterInfo(start=0.0, end=5.0, title="clip_a")]
    plan = build_generic_recovery_plans(chapters, audio_track_indices=[0])[0]
    cmd = build_generic_recover_clip_cmd("ffmpeg", "master.mov", plan, "clip_a.mov")
    assert "-metadata:s:v:0" not in cmd
    assert "use_metadata_tags" not in cmd


def test_generic_recover_clip_cmd_applies_rotation_override():
    plan = GenericRecoveryPlan(title="clip_a", index=0, start=0.0, duration=5.0,
                               camera_id="", camera_label="", video_stream=0,
                               audio_stream=None, rotation=90)
    cmd = build_generic_recover_clip_cmd("ffmpeg", "master.mov", plan, "clip_a.mov")
    assert "-metadata:s:v:0" in cmd
    assert cmd[cmd.index("-metadata:s:v:0") + 1] == "rotate=90"
    assert "use_metadata_tags" in cmd


def test_generic_recover_clip_cmd_rotation_override_can_be_zero():
    # rotation=0 is a deliberate "force to zero", distinct from None ("don't touch").
    plan = GenericRecoveryPlan(title="clip_a", index=0, start=0.0, duration=5.0,
                               camera_id="", camera_label="", rotation=0)
    cmd = build_generic_recover_clip_cmd("ffmpeg", "master.mov", plan, "clip_a.mov")
    assert cmd[cmd.index("-metadata:s:v:0") + 1] == "rotate=0"


def _integration_real_recovery() -> bool:
    """Build a tiny real 2-clip archival master (one conforming, one archival
    lone clip) and prove the recovery commands actually recover it correctly."""
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from core.binaries import get_ffmpeg
    from core.ffmpeg_cmd import build_archival_concat_cmd, build_final_archival_mux_cmd
    from core.manifest import metadata_embed_args, read_manifest, assign_archival_locations

    ff, fp = get_ffmpeg()
    if not Path(ff).exists():
        print("  (skipped integration: ffmpeg not found)")
        return True
    d = Path(tempfile.mkdtemp())

    conform = d / "conform.mp4"    # will BE the baseline's only chapter
    odd = d / "odd.mp4"            # odd-spec original, lone archival clip
    subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi",
                    "-i", "testsrc=size=640x360:rate=30:duration=2",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(conform)], check=True)
    subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi",
                    "-i", "testsrc2=size=1280x720:rate=30:duration=1",
                    "-f", "lavfi", "-i", "sine=frequency=660:duration=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(odd)], check=True)

    baseline = d / "baseline.mov"
    chapters = d / "ch.txt"
    chapters.write_text(";FFMETADATA1\n\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=2000\ntitle=conform\n\n")
    subprocess.run([ff, "-y", "-v", "error", "-i", str(conform), "-i", str(chapters),
                    "-map_metadata", "1", "-map", "0:v", "-map", "0:a",
                    "-c", "copy", str(baseline)], check=True)

    m = Manifest(master_filename="master.mov", baseline_audio_tracks={"camera": 0})
    e_conform = ClipEntry(source_filename="conform.mp4", duration=2.0, conform_status="ok",
                          baseline_chapter_index=0, has_camera_audio=True)
    e_odd = ClipEntry(source_filename="odd.mp4", duration=1.0, conform_status="transcode",
                      has_camera_audio=True,
                      spec_group=spec_signature("h264", 1280, 720, "30/1", "yuv420p"))
    m.clips = [e_conform, e_odd]
    assign_archival_locations([[e_odd]], base_video_count=1, base_audio_count=1)

    embed = metadata_embed_args(m, is_mov=True)
    master = d / "master.mov"
    cmd = build_final_archival_mux_cmd(ff, baseline, [odd], master, d / "p.txt", extra_out_args=embed)
    subprocess.run(cmd, check=True)

    got = read_manifest(fp, str(master))
    assert got is not None
    plan_conform = build_recovery_plan(got, got.clips[0])
    plan_odd = build_recovery_plan(got, got.clips[1])

    rec_conform = d / "rec_conform.mp4"
    rec_odd = d / "rec_odd.mp4"
    subprocess.run(build_recover_clip_cmd(ff, str(master), plan_conform, str(rec_conform)), check=True)
    subprocess.run(build_recover_clip_cmd(ff, str(master), plan_odd, str(rec_odd)), check=True)

    def dmd5(p, s):
        return subprocess.run([ff, "-v", "error", "-i", str(p), "-map", f"0:{s}", "-f", "md5", "-"],
                              capture_output=True, text=True).stdout.strip()

    assert dmd5(conform, "v:0") == dmd5(rec_conform, "v:0")
    assert dmd5(conform, "a:0") == dmd5(rec_conform, "a:0")
    assert dmd5(odd, "v:0") == dmd5(rec_odd, "v:0")
    assert dmd5(odd, "a:0") == dmd5(rec_odd, "a:0")
    print("  real recovery (baseline + archival clip): bit-exact video+audio for both — OK")
    return True


def test_wav_window_prefers_measured_concat_position():
    """Task 85: with measured concat positions in the manifest, the WAV-backup
    window comes from them — not from the modelled video offsets, which drift
    when any audio segment doesn't run exactly as long as its video."""
    m = _mixed_manifest()
    e = m.clips[0]
    e.concat_start = 0.0
    e.wav_track_duration = 9.98          # WAV segment slightly shorter than video
    plan = build_recovery_plan(m, e)
    assert (plan.wav_start, plan.wav_duration) == (0.0, 9.98)
    # video window is untouched — still the modelled chapter offsets
    assert (plan.video_start, plan.video_duration) == (0.0, 10.0)

    # a mid-track clip: measured start (10.04, from real file durations) wins
    # over the modelled 10.0
    m2 = _mixed_manifest()
    m2.clips[1].has_wav = True
    m2.clips[1].concat_start = 10.04
    m2.clips[1].wav_track_duration = 5.9
    plan2 = build_recovery_plan(m2, m2.clips[1])
    assert (plan2.wav_start, plan2.wav_duration) == (10.04, 5.9)


def test_wav_window_falls_back_to_video_offsets_on_old_manifest():
    """An older manifest (concat_start absent → None) keeps the historical
    video-offset behaviour byte-for-byte."""
    m = _mixed_manifest()
    e = m.clips[0]
    assert e.concat_start is None
    plan = build_recovery_plan(m, e)
    assert (plan.wav_start, plan.wav_duration) == (0.0, 10.0)
    # measured start alone (no measured wav duration) must NOT half-apply
    e.concat_start = 0.02
    e.wav_track_duration = None
    plan = build_recovery_plan(m, e)
    assert (plan.wav_start, plan.wav_duration) == (0.0, 10.0)


def test_video_window_prefers_measured_concat_position():
    """Task 87: baseline clips take their VIDEO window from measured concat
    positions — start from this clip's, length from the gap to the next clip's
    (the modelled cumulative durations drift ±1 frame at boundaries)."""
    m = _mixed_manifest()
    m.clips[0].concat_start = 0.0
    m.clips[1].concat_start = 10.033        # measured: 33ms past the modelled 10.0
    plan0 = build_recovery_plan(m, m.clips[0])
    assert plan0.video_measured is True
    assert (plan0.video_start, plan0.video_duration) == (0.0, 10.033)
    plan1 = build_recovery_plan(m, m.clips[1])
    assert plan1.video_start == 10.033, "measured start must beat the modelled 10.0"
    # clip 2 is unmeasured → clip 1's window length falls back to its modelled duration
    assert plan1.video_duration == 6.0
    assert plan1.video_measured is True


def test_video_window_unmeasured_and_archival_clips_unchanged():
    m = _mixed_manifest()
    plan = build_recovery_plan(m, m.clips[1])          # no concat_start anywhere
    assert plan.video_measured is False
    assert (plan.video_start, plan.video_duration) == (10.0, 6.0)
    # archival-track clips never use concat positions — their own track is exact
    m.clips[2].concat_start = 99.9
    plan2 = build_recovery_plan(m, m.clips[2])
    assert plan2.video_measured is False
    assert (plan2.video_start, plan2.video_duration) == (0.0, 4.0)


def test_recover_cmd_applies_copy_seek_guard_only_when_measured():
    """Stream-copy recovery seeks SEEK_EPS late on a MEASURED window (keyframe
    snap is at-or-before the target, so rounding below the clip's own IDR would
    otherwise fall a whole GOP back into the previous clip). Modelled windows
    keep the exact historical command."""
    from core.extract import SEEK_EPS
    m = _mixed_manifest()
    plan = build_recovery_plan(m, m.clips[1])
    cmd = build_recover_clip_cmd("ffmpeg", "master.mov", plan, "out.mp4")
    assert f"{10.0:.3f}" in cmd, "unmeasured window must keep the historical seek"

    m.clips[1].concat_start = 10.033
    plan_m = build_recovery_plan(m, m.clips[1])
    cmd_m = build_recover_clip_cmd("ffmpeg", "master.mov", plan_m, "out.mp4")
    assert f"{10.033 + SEEK_EPS:.3f}" in cmd_m, "measured window must seek EPS late"


def test_measured_fields_survive_manifest_round_trip():
    from core.manifest import to_json, from_json
    m = _mixed_manifest()
    m.clips[0].concat_start = 12.345
    m.clips[0].wav_track_duration = 9.87
    m2 = from_json(to_json(m))
    assert m2.clips[0].concat_start == 12.345
    assert m2.clips[0].wav_track_duration == 9.87
    # absent in old JSON → None (tolerant load)
    import json as _json
    d = _json.loads(to_json(m))
    for c in d["clips"]:
        c.pop("concat_start", None)
        c.pop("wav_track_duration", None)
    old = from_json(_json.dumps(d))
    assert old.clips[0].concat_start is None
    assert old.clips[0].wav_track_duration is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("running real ffmpeg integration...")
    _integration_real_recovery()
    print("test_extract: all tests passed")
