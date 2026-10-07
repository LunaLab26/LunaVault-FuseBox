# Session 3 handover (Steam Deck, 6–7 Oct 2026): v1.4.009 → v1.4.019

Written by Steam-Claude for Windows Claude. Branch `overnight-2026-10-06` sits on top of
`overnight-2026-09-24` (v1.4.008–009), which itself sits on `fix/mpegts-concat-splice-corruption`.
`main` is still v1.4.005, and **none of this is merged**. Every app change bumped v1.4.NNN and added a
`dev_history.py` entry; commits use `--author "Jonny <jdm525@gmail.com>"` (git config is never changed).

## What changed (one commit per version)

| Ver | Fix | Platform notes |
|---|---|---|
| 010 | **A/V drift at every join.** Each segment is cut to its *video* length (`ClipInfo.video_duration`, new `StreamInfo.video_duration`), every audio slot is padded to it, all slots travel as **ALAC** in the per-clip files, and camera/mix slots are AAC-encoded **once** at the join (`final_audio_encode_args`). A late-starting WAV uses `adelay`, not `-itsoffset`. Chapters advance by measured video length. Manifest `audio_lossless` is False for baseline camera audio. | pure ffmpeg; same on Windows |
| 011 | **Short clips (<5 s) skipped WAV sync** (`analyze_sync`): now one window over the whole overlap, with the WAV window widened by ±MAX_TAU. | pure Python |
| 012 | **VP9/AV1 offered as a baseline** (MOV can't hold them): `enumerate_specs` maps them to H.264 (8-bit) or HEVC (10-bit). | same |
| 013 | **Duplicate DTS at joins**: `probe_frame_exact_duration` snaps concat `duration` to nb_frames/fps. | same |
| 014 | Converted clips cut to whole output frames. | superseded in part by 019 |
| 015 | **Compatible playback master froze or dropped camera clips**: the compat re-encode now reads the TS-joined master (`joined.mov`), not the per-clip concat. | same; check NVENC/QSV/AMF paths of `build_concat_reencode_cmd` on Windows |
| 016 | UI: `widgets/nav_tabs.py` (`NavTabs` replaces the `QTabWidget` bar; primary / tools / ⋯ menu), centred empty shelf, About page capped column, status bar hidden unless ffmpeg is missing, "Activity log". | check fonts/DPI on Windows |
| 017 | UI: Merge page folds archival/quality/verify/compat/GPU/preview into "ARCHIVE, QUALITY & CHECKS" with a summary; clip table sizing; status chips; Android "ISO Media … Google" → "Google Pixel"; output defaults to `<source>/Kept by FuseBox`. | |
| 018 | `-movflags +faststart+use_metadata_tags` (the manifest embed was cancelling faststart). | |
| 019 | **Each segment's streams trimmed/padded inside the filters**: audio `aresample=async=1:first_pts=0,apad,atrim=end=vdur`; converted video `trim=end_frame=N` (no `-frames:v`, which ended the segment before the audio was padded); `-t` = vdur + half a frame as a backstop. Fixes Pixel audio that starts 20–43 ms late. | same |

Tests: 566 → 580, all passing (`python -m pytest -q`). New e2e tests in `tests/test_ts_concat_e2e.py`,
`tests/test_sync_short_clip_e2e.py`, `tests/test_verify.py` need a working `bin/ffmpeg(.exe)`.

**Never enable hardware decode for 4K 10-bit HEVC** (it crashed the whole OS on both Windows and the Deck).

## How it was tested (repeat this on Windows)

Tools are in `tools/session3/`. Set `LV_WORK` to a scratch folder and point `LV_FFMPEG` / `LV_FFPROBE`
at `bin\ffmpeg.exe` / `bin\ffprobe.exe` (default: whatever is on PATH).

1. **Build the test sets** in `%LV_WORK%\sets\`:
   - `python tools/session3/make_sync_set.py %LV_WORK%/sets/SYNC`: 10 synthetic clips (4K HEVC 10-bit 29.97,
     1080p60 mono 44.1 k, 720p25, VP9 1080p, ProRes 23.976 PCM24, 1440p24 5.1, portrait, 1080p120,
     4K full-range H.264, VFR screen-style), each with a white flash + 1 kHz beep at 1.0 s and 3.0 s.
   - `python tools/session3/make_sync_wav_set.py %LV_WORK%/sets/SYNC_W`: 4 clips + WAV backups that start
     early / late / stop early.
   - Real footage, trimmed by stream copy (`ffmpeg -i in.mp4 -t 6 -map 0:v -map 0:a? -c copy out.mp4`; WAVs
     cut to video length + ~0.48 s so the real pre-roll survives):
     `R1_luna` (4 Luna Ultra HEVC 10-bit + WAVs), `R2_cattle_mixed` (Luna, Go3S H.264 4K, Pixel VP9 1080p,
     Pixel 4K120, screen recording), `R3_multicam_july` (the 9-clip "multicam video archive test" zip, which is
     also on Windows at `G:\Claude cowork\`), `B_mixed` (synthetic edge cases: copy or regenerate).
2. **Run the matrix** (each merge drives the real `MergeTab` headlessly in a fresh process):
   `python tools/session3/matrix.py run <repo> <outdir> [filter]`. It runs 17 cases (defaults, baseline
   1080p/720p/1080p60, archival + per-clip + MD5, optimize-YouTube, compat H.264 / ProRes, blur fill). Every
   output gets a full decode, stream durations, and for SYNC sets the flash-vs-beep offset on every audio track.
   Results go to `results.jsonl`, and every ffmpeg command is logged to `<case>.cmds.txt`.
   Run it once on the old version (a `git worktree` of v1.4.009) and once on the new one.
3. **Per-join audit on real footage**: `python tools/session3/audit_joins.py master.mov <source_dir>`
   cross-correlates each clip's own audio inside the master against its manifest `concat_start`. Expect
   0 ms at every join, except Pixel clips, which keep their own recorded audio start offset (+18/+20/+43 ms).
4. **WAV truth**: `true_offsets.py <folder>` measures camera↔WAV offsets by GCC; the app's `analyze_sync`
   should match within ~2 ms.
5. **YouTube**: HP-Claude uploads (unlisted). Download back with a CURRENT yt-dlp (HP's 2024 build only sees
   storyboards), then decode-check, compare durations, and run `av_events.py` on the SYNC master.
   `yt_sim.py` is the local stand-in.

## Results on the Deck (v1.4.009 → v1.4.019, same 17 cases)
- v1.4.009: 2 merges failed (VP9 baseline), 2 had decode errors (duplicate DTS), sync up to 329 ms,
  ProRes compat sound −980 ms, compat masters frozen/missing clips.
- v1.4.019: 17/17 merged, 0 decode errors, sync ≤4 ms (compat H.264 23 ms, under one frame); real joins 0 ms.
- YouTube: R1/R2/R2-compat/R3 served at 2160p VP9, SYNC at 1080p AV1; 0 decode errors; sync ≤3.7 ms.

## Windows-specific things to watch
- `core/gpu_encode.py`: VAAPI is Linux-only; Windows uses NVENC/QSV/AMF. The ALAC-intermediate audio change
  and the filter-side trims don't touch the video encoders, but run the compat + optimize cases with the
  GPU on.
- Harness: `matrix.py` uses `sys.executable` for its children; run it from the project venv. Qt runs offscreen.
  The harness patches `QMessageBox`/`_CameraNamingDialog` so nothing blocks.
- The harness writes to the app's real `export_log.json`/`settings.json` (last_merge_* paths). Clean them
  afterwards, or back them up first.
- Paths with spaces and `\` in the concat lists: `write_concat_list` already normalises to `/`.
- Not done: the Review page's "Software decode" wording (blocked by a permission check on the Deck); Review
  and Recover pages haven't had the simplification pass.
