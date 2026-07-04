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
)


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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("running real ffmpeg integration...")
    _integration_real_recovery()
    print("test_extract: all tests passed")
