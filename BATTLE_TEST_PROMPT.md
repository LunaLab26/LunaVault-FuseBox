# LunaVault FuseBox — Comprehensive Battle Test

You are battle-testing **LunaVault FuseBox**, a PySide6 desktop app for merging
multi-camera video+audio footage into a single master, recovering clips back
out of a master, and reviewing masters before/after. You have no prior context
on this app — everything you need is below. Read all of it before starting.

## 0. Environment facts you must know before touching anything

- **Project root**: `/home/deck/Downloads/Luna Ultra Video Merge v1-4`
- **Run from source**: `cd` there, `source venv/bin/activate`, then
  `python3 src/main.py`. The venv's `python3` is a symlink to `/usr/bin/python3`.
- **Sandboxing quirk (check this FIRST)**: if `/.flatpak-info` exists and shows
  `name=com.visualstudio.code`, your shell is running inside the VS Code
  Flatpak sandbox, NOT the real SteamOS host — this sandbox has a *different*
  glibc version and a *different*, more minimal `/usr/lib` than the real
  system (e.g. it lacks the Mesa VAAPI driver the real host has). Symptoms:
  `DBUS_SESSION_BUS_ADDRESS` pointing at `/run/flatpak/bus`, PipeWire/PulseAudio
  connection failures, `/dev/dri/*` owned by `nfsnobody`. None of that is a
  real app bug — it's this shell. Use `flatpak-spawn --host <command>` to run
  anything that needs to see real host state: GPU/VAAPI behavior, PyInstaller
  builds (building inside the sandbox produces a binary that segfaults on the
  real host with a GLIBC version mismatch — confirmed the hard way), `pacman`,
  etc. Anything testing pure application logic (pytest, the app running
  headless/offscreen) is fine directly in this shell.
- **Existing automated test suite**: `tests/` — currently 510 pytest tests,
  run with `source venv/bin/activate && python3 -m pytest tests/ -q`. All must
  stay green; a battle test that breaks existing tests is not done.
- **Existing correctness-matrix harness**: `tests/md5_matrix_test.py` — drives
  the real headless MergeTab through 8 configurations
  (baseline_only/archival_shared/archival_percpip/optimize_youtube/
  compat_h264/compat_prores_proxy/compat_prores_std/compat_prores_hq) against
  a real source folder, MD5-verifying recovered clips against originals.
  Usage: `QT_QPA_PLATFORM=offscreen python tests/md5_matrix_test.py
  <source_folder> <work_dir> --tag <name>`. **Known quirk**: its own log
  sometimes reports "timeout" even when the merge actually completed and
  verified successfully (a harness signal-timing issue) — never trust that
  status line alone. Always check each cell's own `<test_id>.verify.log` for
  the line `Result: X / X clips verified byte-identical to their originals.`
  This is your foundation — extend it, don't reinvent it, for the
  Merge-tab correctness matrix below.
- **Real footage on this machine**:
  - `/home/deck/Videos/multicam video archive test.zip` — 9 short real clips
    (12–72s, ~2GB total) from multiple camera specs: Pixel HEVC 30fps, Pixel
    HEVC 120fps slow-motion, a Pixel `.LS` variant, and two 4K action-cam
    groups (h264 and hevc). Extract it somewhere and use it as your quick,
    default real-footage source for most matrix cells.
  - `/home/deck/Videos/20260707 - Lola and Popops return from Gran Canaria
    1714130726 - recovered clips/` — real Pixel-sourced clips (long, some
    multi-GB) that carry a hidden `bin_data` ("text"-tagged) metadata stream
    (Pixel motion-photo/telemetry). Use these specifically for the bin_data
    regression case below; they're too large/long for routine matrix cells.
- **GPU on this machine**: AMD Radeon 680M via VAAPI. `core/gpu_encode.py`'s
  `system_vaapi_ffmpeg()`/`vaapi_render_device()` detect whether a real
  hardware pipeline is available at all — if this machine (or the one you're
  actually running on) has no working VAAPI, every "hardware" test case must
  gracefully fall back to software and PASS, not skip or error.

## 1. Objective

Battle-test the **Merge**, **Extract and Recover**, and **Review** tabs across
the full practical combination of user-facing options and a representative
spread of real-world input footage. Two pass criteria apply depending on the
scenario (see §5). Three sections of coverage are required:

- **§2 Known regressions (must-pass)** — specific bugs found and fixed in this
  app's recent history. These are not optional; a failure here is a real
  regression, not a new finding.
- **§3 Full option/input matrix** — systematic combinatorial coverage for
  fresh functional bugs.
- **§7 Visual & UX assessment** — a real, on-screen visual review of the GUI
  itself (readability, layout, theming, navigation, contrast, aesthetics) —
  distinct from functional correctness, and requiring actual screenshots, not
  code inspection.

Review-tab *playback* testing is **programmatic only** (§6) — you cannot
verify pixels reliably enough to judge A/V correctness from a headless
session. The visual/UX assessment (§7) is different: it explicitly requires
real screenshots across the whole app, not just Review, and is not something
you can substitute code-reading for.

## 2. Known regressions — must-pass section

Confirm each of these explicitly and report each as its own pass/fail line,
not folded into the general matrix.

1. **VAAPI dual hardware-device warning is cosmetic, not a failure.** When
   both hardware decode AND hardware encode are requested together, ffmpeg
   prints `There are 2 hardware devices... Set hardware device explicitly
   with the filter_hw_device option if device vaapi1 is not usable for
   filters.` This is informational only — the encode must still complete
   (exit 0) and produce valid, playable output. Test: a transcoding clip
   through `core.ffmpeg_cmd.build_mux_cmd_plan` with
   `ConformSpec(hw_decode="vaapi", hw_encoder="vaapi")`, actually run the
   generated command (not just inspect it), confirm exit 0 and a valid
   output file.

2. **The `bin_data`/metadata-track crash.** Camera-sourced clips (confirmed on
   real Pixel footage — see the Gran Canaria folder above) can carry a hidden
   data stream (codec id 98314, codec tag "text" — motion-photo/telemetry,
   `codec_type=data`, `codec_name=bin_data`). `build_concat_cmd` and
   `build_concat_reencode_cmd` in `core/ffmpeg_cmd.py` must map ONLY video +
   audio (`-map 0:v -map 0:a?`), never a blanket `-map 0` — a blanket map
   tries to copy that stream into the `.mov` output, which the muxer rejects
   outright (`Tag text incompatible with output codec id '98314'`, "Could not
   write header", the whole merge dies). Test this in BOTH software encode
   and hardware (VAAPI) encode — confirm both succeed on a real clip from the
   Gran Canaria folder that has this stream (`ffprobe -show_entries
   stream=codec_type,codec_name` to confirm the stream is present before you
   start).

3. **Corrupted/broken ffprobe must be surfaced distinctly, not silently
   treated as "no chapters."** `probe.py`'s `probe_chapters_safe()` returns
   `(chapters, error)` — a crashed/non-zero-exit ffprobe must produce a
   non-None error string, and `extract_tab.py`'s `_apply_extract_mode()` must
   show a message saying the probe itself failed, NOT "No chapter markers
   found in this file" (that message must be reserved for a probe that
   genuinely succeeded and found zero chapters). Test: point `ExtractTab` at
   a master with a deliberately broken ffprobe path (e.g. a 0-byte or
   non-executable stand-in binary) and confirm the distinct error message
   appears; then repeat with a real working probe against a file that
   genuinely has no chapters, and confirm THAT shows the plain "no chapters"
   message, not an error.

4. **The 4K+/10-bit/HEVC hardware-decode crash guard must still auto-engage,
   and the Developer-panel bypass must still require confirmation.**
   `review_playback.is_risky_hw_decode_profile()` must return `True` for a
   4K+ (≥3840×2160), 10-bit, HEVC/H265 profile. `review_tab.py`'s
   `_maybe_force_safe_decode()` must force `HybridPlaybackEngine` (software)
   and disable the "Software decode" checkbox for such a file when
   `dev_review_allow_risky_hw_decode` is off. With that dev-panel option on,
   the checkbox must stay enabled but its tooltip must be
   `_SOFTWARE_DECODE_RISKY_OVERRIDE_TOOLTIP` (warns of a whole-system crash,
   not just "playback may freeze"). Also confirm in `dev_panel.py` that
   ticking `dev_review_allow_risky_hw_decode` ON triggers a confirmation
   dialog (`BoolOpt.confirm`) and that declining it reverts the checkbox
   without persisting, while ticking it OFF never prompts.

## 3. Full option/input matrix

### 3a. Merge-tab option axes (from `core/ffmpeg_cmd.py` / `merge_tab.py`)

Cross these axes for the correctness matrix. Where an axis only has an effect
conditional on another (noted below), don't waste runs on meaningless
combinations (e.g. testing `compat_prores_profile` when `compat_codec="h264"`
does nothing) — but DO cover every combination that is actually reachable and
distinct in the real UI.

| Axis | Values | Conditional on |
|---|---|---|
| `hw_decode` | `off`, `auto` (hardware) | — |
| `hw_encoder` | `off`, `auto` (hardware) | — |
| Archival master | off, on+shared, on+per-clip | — |
| Compatible playback master | off, on (h264), on (prores/proxy), on (prores/standard), on (prores/hq) | prores profile only applies when codec=prores |
| Track plan | camera-only, wav-only, camera+wav+mix(lr), camera+wav+mix(5050) | — |
| Optimize baseline for delivery | off, on (archival), on (master), on (youtube), on (social) | quality preset only applies when optimize is on |
| Fill mode (aspect mismatch) | black, blur | only visible on clips needing a pad |
| Square/crop mode | crop, pad | — |
| MD5 verification | on (required for byte-exact cells), off (for a couple of crash-free-only cells) | — |

That's a genuinely large space (low thousands of theoretical combinations).
Run the true full product on the axes most likely to interact — `hw_decode` ×
`hw_encoder` × Archival master × Compatible playback master (this is exactly
where this session's real bugs lived) — crossed with the input matrix below.
For the remaining lower-risk axes (track plan, quality preset, fill, crop
mode), cover every value at least once each, in combination with a
representative (not exhaustive) sample of the higher-risk axes. State
explicitly in your final report which combinations you ran the full product
on vs. representative coverage on, and roughly how many total merge
invocations that came to.

### 3b. Extract-tab option axes

- `extract_output_format`: native, mov, mp4
- Manifest present vs. absent (sidecar `.manifest.json` next to the master) —
  drives the manifest-based recovery path vs. the chapter-based generic
  fallback (`build_generic_recovery_plans`)
- "Ignore manifest" toggle, when a manifest IS present
- Per-clip manual overrides: audio-track role reassignment, video-source
  override (normal vs. LRV proxy, when an LRV file is present), rotation
  override

### 3c. Review-tab option axes (tested per §6, programmatic only)

- `review_software_decode`: on, off
- `dev_review_allow_risky_hw_decode`: on, off
- Fast preview (480p proxy): on, off
- Single audio-track playback vs. rendered mix playback

### 3d. Input clip matrix — synthetic + real

**Synthetic** (generate with `ffmpeg -f lavfi`, so you can exhaustively cross
these with the option matrix above without depending on footage availability):

- Codec: H.264, HEVC
- Bit depth / pixel format: 8-bit (`yuv420p`), 10-bit (`yuv420p10le`)
- Resolution: 1080p, 4K, and at least one non-4K odd resolution to force a
  transcode/conform path (e.g. 1280×960 like the real Pixel `.LS` variant)
- Frame rate: a standard rate (30fps) and a slow-motion-style rate (120fps)
- Color: SDR (bt709) and at least one HDR-tagged case (BT.2020 matrix +
  HLG/PQ transfer — this app has a documented history of HDR-specific bugs
  where one probed color value was wrongly reused across three distinct
  ffmpeg options; re-verify this doesn't regress)
- Audio layout: camera audio only (AAC), camera + separate WAV backup file
  (matching duration), camera + WAV backup where the WAV runs LONGER than the
  video (this app has a documented history of a freeze-frame bug here — the
  extra WAV duration used to silently stretch the video segment), no audio at
  all, multiple pre-existing audio tracks in one file
- Rotation: unrotated, and a clip carrying rotation/display-matrix metadata
  (simulating a portrait phone video)
- Multi-clip continuity: at least one "camera group" made of several
  sequential same-spec clips (simulating a camera's file-split behavior on
  long recordings) alongside singleton clips from other camera specs

**Real** (use for the specific cases synthetic clips can't represent):
- The multicam test zip (§0) for realistic multi-camera-spec merges
- The Gran Canaria clips (§0) specifically for the `bin_data` regression case
  and for any general test that benefits from genuinely long/large real
  footage
- An LRV-proxy-bearing clip if the multicam test zip's LRV files apply, to
  exercise Extract's video-source-override path

## 4. Test execution strategy

- Prefer extending `tests/md5_matrix_test.py`'s pattern (real headless
  `MergeTab`, drive it programmatically, not GUI automation) for the Merge-tab
  matrix — it's already proven, handles the "Merge complete!" modal-dialog
  trap, and writes results incrementally (`results.jsonl`, `progress.json`)
  so a long run is resumable/inspectable mid-flight.
- Run in the background; this matrix is large by design (you were asked to
  run the full cartesian product on the highest-risk axes) — budget for a run
  that may take hours. Do not shorten it by silently pruning without saying
  so in your report.
- For every cell, capture: the exact command(s) run, exit code, and (where
  applicable) the `.verify.log` result line — never trust a bare "success"
  status from any wrapper without checking the underlying verification
  output, per the timeout quirk noted in §0.
- Clean up large intermediate files as you go (temp merges, proxies) so you
  don't fill the disk mid-run; check `df -h` before starting and periodically
  during.

## 5. Pass/fail criteria

- **Byte-exact criterion** (use wherever MD5 verification is applicable —
  archival tracks, per-clip archival, any "verify_md5=on" cell): the
  recovered/archival clip must MD5-match its original exactly. This is the
  strong, data-loss-proof criterion.
- **Crash-free + sanity criterion** (use for everything MD5 verification
  doesn't apply to by design — e.g. the Compatible-playback-master re-encode,
  which is NEVER byte-exact since it deliberately re-encodes): the operation
  must exit 0, the output file must exist, be non-trivially sized, and
  actually probe as valid media (`ffprobe` succeeds and reports the expected
  stream layout — right codec, right track count, non-zero duration). Audio
  sync should be sanity-checked (no gross offset) where the input matrix
  includes a WAV-offset case.
- A cell may need BOTH criteria (e.g. an archival-master + compat-baseline
  merge: the archival track must be byte-exact AND the compat baseline must
  pass the sanity criterion).

## 6. Review-tab testing (programmatic only — no visual/pixel verification)

Do not attempt to watch actual video playback or take screenshots to verify
correctness — you cannot reliably do this from a headless/sandboxed session,
and it is explicitly out of scope here. Instead, for each Review-tab option
combination in §3c, construct a `ReviewTab` instance offscreen
(`QT_QPA_PLATFORM=offscreen`, matching the pattern in `tests/test_dev_panel.py`
and `tests/test_review_playback.py`) and assert on its internal state:

- The correct `PlaybackEngine` subclass (`QtPlaybackEngine` vs
  `HybridPlaybackEngine`) is instantiated for the given settings + content
  profile.
- The "Software decode" checkbox's `isEnabled()`/`isChecked()`/`toolTip()`
  match what §2 item 4 specifies for each of the four risky/override
  combinations.
- Loading a master with a manifest vs. without correctly populates (or
  doesn't) the archival-clip source picker.
- Track-selection methods (`set_audio_single`, `set_audio_mix_file`) report
  success/failure correctly given the tracks passed in.

## 7. Visual & UX assessment (whole-app GUI review)

Unlike §6 (Review-tab playback, deliberately programmatic-only), this section
requires ACTUALLY SEEING the app on screen — real screenshots, not headless
assertions or code inspection. If your environment genuinely cannot produce
real on-screen screenshots, say so explicitly in your report rather than
guessing or substituting a code-only review.

**Getting a real visual session on this machine**: if you're in the
sandboxed shell described in §0 with no direct display access, check whether
a Chrome Remote Desktop virtual X11 session is available (commonly display
`:20` on this machine) — `xdotool` can drive and screenshot that virtual
desktop even when it can't touch the real physical Wayland screen. Launch the
app (`venv/bin/python src/main.py`) with `DISPLAY=:20` against that session,
then use `xdotool` plus a screenshot tool (`import`, `scrot`,
`gnome-screenshot` — whichever is available) to capture real frames. If no
visual session is reachable at all, report that plainly as a gap rather than
faking this section.

For EVERY tab (Memories/Add, Merge, Extract and Recover, Review, Log, About)
and BOTH theme modes (Dark, Light — the corner toggle also has an "Auto"
option, worth a quick spot-check too), capture screenshots and assess:

- **Text readability**: font size legible at default window size; no text
  visibly clipped, truncated without ellipsis, or overlapping another
  element; tooltips and status labels readable against their background.
- **Layout spacing**: consistent padding/margins between cards, buttons, and
  sections across tabs (not just within one); no cramped or overlapping
  controls; no excessive dead space that pushes related controls far apart.
- **Window rescaling**: resize the main window across a representative range
  (the minimum viable size, a typical ~1280×800 size, and a large
  1920×1080+ size) and re-screenshot each tab at each size. Look for:
  overlapping widgets, controls that vanish or become unreachable below a
  certain size, scroll areas that fail to appear when content overflows (or
  appear when they shouldn't), and anything that doesn't reflow sensibly.
- **Dark mode AND light mode**: switch the theme toggle and re-check every
  tab in both modes specifically for: any hardcoded/literal color that
  doesn't adapt (a light-mode leftover showing through in dark mode or vice
  versa — this codebase's own convention, stated in several files'
  docstrings, is "no literal colours, always `theme.active_palette()`"; this
  is testing whether that discipline actually held everywhere), and any
  color pairing that becomes hard to read after switching even though it
  worked fine in the other mode.
- **Navigation**: is it clear how to move between tabs and reach every
  feature without prior knowledge? Note anything that relies on undocumented
  interaction (e.g. the triple-click-the-logo gesture that reveals the
  Legacy-mode toggle and Developer panel) — assess whether it's reasonably
  discoverable or a usability dead-end, not just whether it technically
  works once you already know the gesture.
- **Color contrast**: check text-against-background and
  icon/accent-against-background contrast in both themes, especially for the
  semantic colors (ok/warn/danger used in Pre-flight diagnostics, verify
  banners, disk-space warnings) — a warning that's barely distinguishable
  from a normal status line defeats its purpose. Flag any pairing that looks
  borderline; you don't need to compute exact WCAG ratios, but call out
  anything that would fail an obvious eyeball test.
- **Ease of use / overall aesthetics**: button labeling clarity, consistency
  of button/card styling across tabs, information density (is the
  Pre-flight/diagnostics view overwhelming or well-organized?), and your
  overall impression of a first-time user's experience landing on each tab
  cold.

**Report this section as prose findings with attached screenshots**, not
pass/fail booleans — e.g. "On the Extract tab at the minimum window size, the
per-clip audio-role dropdown is clipped and its label wraps awkwardly onto
the camera-name column (screenshot: extract_min_dark.png)." Rank findings by
how much they'd actually bother a real user, not by an arbitrary severity
scale.

## 8. Reporting format

At the end, produce a single structured report with:

1. **Known regressions (§2)** — one line each, PASS/FAIL, with the command
   and evidence (verify.log line, exit code, or assertion) for each.
2. **Merge-tab matrix (§3a × §3d)** — total cells run, pass count, fail count,
   and full detail (command + evidence) for every FAILURE only (not every
   pass — that would be unreadable at this scale). State clearly which axes
   got full-product coverage vs. representative coverage, per §3a.
3. **Extract-tab matrix (§3b × §3d)** — same shape as above.
4. **Review-tab programmatic checks (§3c/§6)** — same shape as above.
5. **Visual & UX assessment (§7)** — prose findings with attached
   screenshots, per tab and per theme mode, ranked by real-user impact; if no
   visual session was reachable, say so explicitly here instead of omitting
   the section.
6. **Any NEW bug found** that isn't in the known-regressions list — full
   repro steps, exact command, exact error, and your best assessment of root
   cause (don't just patch it silently; report it the same way the known
   regressions were originally found and documented in this app's history).
7. **Test suite status** — confirm `python3 -m pytest tests/ -q` is still
   100% green after everything above.

Do not fix anything you find without reporting it first — this is a battle
test, not a silent-repair pass. Flag genuine failures clearly so they can be
triaged before any code changes are made.
