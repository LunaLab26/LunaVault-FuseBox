"""Generate synthetic A/V sync clips: a white flash and a 1 kHz beep at the same instant,
in many container/codec/fps/audio variants. Two events per clip (1.0 s and 3.0 s)."""
import os
FF = os.environ.get("LV_FFMPEG", "ffmpeg")
FP = os.environ.get("LV_FFPROBE", "ffprobe")
import subprocess, sys
from pathlib import Path

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
DUR = 4.5
EV = "between(t,1,1.1)+between(t,3,3.1)"

# name, w, h, fps, vcodec args, ext, ar, ac, acodec args
VARIANTS = [
    ("SYN_20260920_100001_4k2997_hevc10_aac48s", 3840, 2160, "30000/1001",
     ["-c:v", "libx265", "-preset", "ultrafast", "-pix_fmt", "yuv420p10le", "-tag:v", "hvc1", "-x265-params", "log-level=error"], "mp4", 48000, 2, ["-c:a", "aac", "-b:a", "192k"]),
    ("SYN_20260920_100101_1080p60_h264_aac44m", 1920, 1080, "60",
     ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p"], "mp4", 44100, 1, ["-c:a", "aac"]),
    ("SYN_20260920_100201_720p25_h264_aac48s", 1280, 720, "25",
     ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p"], "mp4", 48000, 2, ["-c:a", "aac"]),
    ("SYN_20260920_100301_1080p30_vp9_aac44s", 1920, 1080, "30",
     ["-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-b:v", "4M", "-pix_fmt", "yuv420p"], "mp4", 44100, 2, ["-c:a", "aac"]),
    ("SYN_20260920_100401_1080p23976_prores_pcm24", 1920, 1080, "24000/1001",
     ["-c:v", "prores_ks", "-profile:v", "2"], "mov", 48000, 2, ["-c:a", "pcm_s24le"]),
    ("SYN_20260920_100501_1440p24_hevc8_aac51", 2560, 1440, "24",
     ["-c:v", "libx265", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-tag:v", "hvc1", "-x265-params", "log-level=error"], "mp4", 48000, 6, ["-c:a", "aac"]),
    ("SYN_20260920_100601_portrait1080x1920_h264_aac48s", 1080, 1920, "30",
     ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p"], "mp4", 48000, 2, ["-c:a", "aac"]),
    ("SYN_20260920_100701_1080p120_h264_aac48s", 1920, 1080, "120",
     ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"], "mp4", 48000, 2, ["-c:a", "aac"]),
    ("SYN_20260920_100801_4k2997_h264full_aac48s", 3840, 2160, "30000/1001",
     ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuvj420p"], "mp4", 48000, 2, ["-c:a", "aac"]),
]

for name, w, h, fps, vargs, ext, ar, ac, aargs in VARIANTS:
    out = OUT / f"{name}.{ext}"
    if out.exists():
        continue
    layout = {1: "mono", 2: "stereo", 6: "5.1"}[ac]
    vf = (f"testsrc2=s={w}x{h}:r={fps}:d={DUR},eq=brightness=-0.3,"
          f"drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='{EV}'")
    af = (f"aevalsrc='0.01*(random(0)*2-1)+if({EV},0.7*sin(2*PI*1000*t),0)':s={ar}:d={DUR},"
          f"aformat=channel_layouts={layout}")
    cmd = [FF, "-v", "error", "-y", "-f", "lavfi", "-i", vf, "-f", "lavfi", "-i", af,
           *vargs, *aargs, "-shortest", str(out)]
    print(name, flush=True)
    subprocess.run(cmd, check=True)

# Variable-frame-rate screen-recording style clip (90 kHz timebase, uneven frame times)
out = OUT / "screen-20260920-100901-vfr.mp4"
if not out.exists():
    vf = (f"testsrc2=s=960x2142:r=60:d={DUR},eq=brightness=-0.3,"
          f"drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='{EV}',"
          "select='not(eq(mod(n,7),3))*not(eq(mod(n,11),5))'")
    af = f"aevalsrc='0.01*(random(0)*2-1)+if({EV},0.7*sin(2*PI*1000*t),0)':s=44100:d={DUR}"
    subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", vf, "-f", "lavfi", "-i", af,
                    "-fps_mode", "vfr", "-video_track_timescale", "90000",
                    "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-ac", "2", "-shortest", str(out)], check=True)
    print(out.name)
