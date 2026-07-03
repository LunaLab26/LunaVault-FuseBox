"""Tests for camera_id.py + clip_model pairing/ordering helpers. Runs under
pytest and standalone (`python tests/test_camera_id.py`)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from camera_id import identify_camera
from clip_model import _clip_key, _pair_wav


def test_device_metadata_wins():
    k1, l1 = identify_camera(device="Google Pixel 9 Pro", filename="PXL_123.mp4")
    assert l1 == "Google Pixel 9 Pro" and k1.startswith("dev:")
    # Same device, different filename → same camera key (all Pixel clips group).
    k2, _ = identify_camera(device="Google Pixel 9 Pro", filename="PXL_999.LS.mp4")
    assert k1 == k2


def test_handler_as_device_groups_insta360():
    # Insta360 exposes 'Ambarella' via handler_name (probe._extract_device).
    k, l = identify_camera(device="Ambarella", filename="VID_20260703_130055_00_004.mp4")
    assert k == "dev:ambarella" and l == "Ambarella"


def test_generic_device_falls_back_to_filename():
    # Luna Ultra: generic handler → device "" → filename family "VID".
    k, l = identify_camera(device="", filename="VID_20260703_130115_011.mp4")
    assert k == "file:VID"


def test_insta360_and_luna_do_not_collide():
    insta = identify_camera(device="Ambarella", filename="VID_20260703_130055_00_004.mp4")[0]
    luna = identify_camera(device="", filename="VID_20260703_130115_011.mp4")[0]
    assert insta != luna   # both 'VID_' but device presence separates them


def test_clip_key_pairs_cross_brand():
    # Insta360 audio named after the LRV proxy, video named VID with a different index.
    assert _clip_key("VID_20260703_130055_00_004") == _clip_key("LRV_20260703_130055_01_004.lrv")


def test_pair_wav_insta360_and_luna():
    wavs = {
        "LRV_20260703_130055_01_004.lrv": Path("a.WAV"),        # Insta360 (cross-brand key)
        "VID_20260703_130115_011_backup": Path("b.wav"),        # Luna (prefix)
    }
    assert _pair_wav("VID_20260703_130055_00_004", wavs) == Path("a.WAV")
    assert _pair_wav("VID_20260703_130115_011", wavs) == Path("b.wav")
    assert _pair_wav("PXL_20260703_115902653", wavs) is None    # phone: no separate WAV


def test_assign_and_group_by_camera():
    from clip_model import ClipInfo, assign_cameras, group_clips_by_camera
    from probe import StreamInfo

    def mk(name, device):
        c = ClipInfo(path=Path(name))
        c.stream = StreamInfo(device=device)
        return c

    clips = [mk("PXL_1.mp4", "Google Pixel 9 Pro"), mk("PXL_2.mp4", "Google Pixel 9 Pro"),
             mk("VID_130055_00_004.mp4", "Ambarella"), mk("VID_130115_011.mp4", "")]
    assign_cameras(clips)
    assert clips[0].camera_label == "Google Pixel 9 Pro" and clips[0].camera_id == clips[1].camera_id
    groups = group_clips_by_camera(clips)
    assert len(groups) == 3   # Pixel, Ambarella, generic-VID (Luna)
    assert len(groups[clips[0].camera_id]) == 2


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("test_camera_id: all tests passed")
