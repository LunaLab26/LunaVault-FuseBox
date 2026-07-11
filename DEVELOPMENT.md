# Development notes — LunaVault FuseBox

Context for anyone (or any AI assistant) continuing work on this project.

## What it is
A **PySide6 (Qt Widgets) desktop app** wrapping a bundled `ffmpeg`. Two workflows:
- **Merge clips** — scan a folder of camera MP4s + their WAV backups, pair and
  time-order them, sync the WAV to the camera audio, and merge into one lossless
  `.mov` master (stream copy; mismatched clips are conformed).
- **WhatsApp clip** — trim a clip, optionally apply a `.cube` colour-grade LUT,
  export a 720p H.264 MP4.

Version 1.4. Brand: warm amber/gold/blue banner theme; light/dark/system toggle.

## Architecture
UI-agnostic logic lives in **`src/core/`** (pure Python, no Qt, unit-tested);
Qt worker threads and widgets sit on top.

- `src/main.py` — entry point, `MainWindow`, tabs, theme controller, update check.
- `src/theme.py` — `Palette` + `build_qss()` + `ThemeController` (dark/light/system).
- `src/merge_tab.py` — Merge tab: sectioned, **scrollable** UI (SOURCE/CLIPS/AUDIO/OUTPUT),
  clip table, collapsible audio options, pre-flight, live progress.
- `src/whatsapp_tab.py`, `src/log_tab.py`, `src/about_tab.py` — the other tabs.
- `src/review_tab.py` — Review tab: `ReviewSession` (position authority) + `ReviewTab`
  (owns the playback engine, workers, and the widgets below).
- `src/review_playback.py` — `PlaybackEngine` interface + `QtPlaybackEngine`
  (QMediaPlayer/QVideoSink/QAudioOutput) for the Review tab.
- `src/review_workers.py` — Review tab's background QThread workers: `TrackScanWorker`,
  `PeakScanWorker`, `SpectrogramWorker`, `MixRenderWorker`, `FrameFetchWorker`.
- `src/widgets/` — shared/Review-tab-specific widgets: `timeline.py` (`TimelineBase` +
  `TrimTimeline`, shared with the WhatsApp tab's timeline), `trackbar.py`
  (`OverviewTrackbar`), `video_view.py` (`ZoomableVideoView`), `jog_wheel.py`,
  `scopes_panel.py`, `audio_lanes.py`.
- `src/ffmpeg_runner.py` — QThread workers (`MergeWorker`, `WhatsAppWorker`, …) over core.
- `src/clip_model.py`, `src/probe.py`, `src/grade_manager.py`, `src/settings.py`,
  `src/log_manager.py` — data model, ffprobe wrapper, LUT registry, settings, JSON log.
- `src/crash_log.py`, `src/thread_utils.py` — faulthandler/excepthook crash logging and
  the `settle()` QThread-lifetime helper (see "v1.4 progress notes" below).
- Dialogs: `audio_sync_dialog.py` (Advanced/Batch sync), `audio_track_dialogs.py`
  (Custom audio / Advanced output), `preflight_dialog.py`, `audio_sample_player.py`.
- **`src/core/`**: `binaries.py`, `progress.py`, `sync.py` (legacy), `sync_advanced.py`
  (GCC-PHAT + drift), `ffmpeg_cmd.py` (all command builders, `OutputPlan`, `MixSpec`,
  `build_mux_cmd_plan`), `track_info.py`, `plan_report.py` (pre-flight + log reasoning),
  `encoders.py` (HW-encoder detection), `updates.py` (check-for-updates, disabled until
  `UPDATE_REPO` is set), `audio_peaks.py` (peak pyramids), `scopes.py` (histogram/
  waveform arrays), `spectrogram.py` (STFT + magma colour LUT), `review_media.py`
  (ffmpeg command builders for frame extraction/snapshots/mix render).
- `tests/` — 13 files, 110 tests. Each file runs standalone (`python tests/test_ffmpeg_cmd.py`) or via pytest.
- `docs/index.html` — landing page (GitHub Pages: main → /docs).
- `luts/` — 28 `.cube` LUTs. `bin/` — ffmpeg/ffprobe (gitignored; see below).

## Key behaviours to preserve
- **Uniform audio-track layout**: every clip emits the same audio slots (silence-filled
  where a source is missing) so the final `concat` is consistent regardless of clip order.
- **Slow-motion clips** (video ≫ WAV duration): the primary track is the WAV time-stretched
  (pitch-corrected via `atempo`) to the video length.
- **No camera audio** (e.g. Bluetooth mic off): camera track falls back to WAV, or silence.
- **Safe write**: per-clip temp files go on a fast LOCAL scratch dir; only the finished
  master is written to the output folder, then `os.replace`'d into place (atomic).
- Lossless tracks (camera copy, WAV→ALAC) are never resampled; drift correction applies
  only to the derived mix track.

## Running & testing
- Needs `ffmpeg` + `ffprobe` in `bin/` (NOT in the repo — download per platform; see README).
- Unit tests need no ffmpeg (they assert command strings): `python tests/test_ffmpeg_cmd.py` etc.
- Headless checks: `QT_QPA_PLATFORM=offscreen`. To inspect UI without a display, render a
  widget to PNG with `widget.grab().save(...)` and view it.
- **A cloud/headless session can edit code, run unit tests, and update docs/website, but
  cannot run the GUI or real ffmpeg merges** — those need a desktop (Windows PC or Steam Deck).

## Conventions
- End commit messages with a `Co-Authored-By:` trailer.
- `.gitignore` excludes `bin/` ffmpeg binaries, `dist/`, `build/`, `wheels/`, venvs, temp.
- `.gitattributes` forces LF (Linux/Deck shell scripts must stay LF); `.bat` stays CRLF.

## Roadmap (next steps)
1. **First Windows release** — build via `build.bat`, zip `dist/`, cut a GitHub Release so
   the landing-page Download button serves a real installer.
2. **Linux build + testing** on the Steam Deck (`run_linux.sh` / `build_linux.sh`); Flatpak later.
3. **macOS build** — planned approach: unsigned `.app` built by a free GitHub Actions macOS
   runner + "Open Anyway" instructions (no paid signing yet). Still needs a Mac to test.
4. **"Review" tab** (v1.4, shipped) — load a master `.mov`, play it with frame-step/jog
   scrubbing, per-track audio audition with tick-to-mix, waveform/spectral views, colour
   scopes, and a full-res snapshot button. Progress notes below.

Support: Buy Me a Coffee is the primary donation option (buymeacoffee.com/LunaVault);
crypto is secondary (behind a "Prefer crypto?" reveal in the About tab).

## Future ideas (not yet scoped — for discussion before any work starts)

*("WhatsApp clip" → "Extract and Share" — DONE: see tasks 52/57/58/59 above; renamed to
"Extract and Recover", chapter-based no-manifest extraction built, Share moved to Review.)*

- **Metadata preservation**: a dedicated conversation is wanted on what metadata the app
  should read/write/preserve and why it matters (e.g. audio-track title tags — see the
  "SoundHandler" finding in the Review-tab playback work below: masters currently carry no
  descriptive per-track titles, so nothing — Qt, another player, or a future "Extract and
  Share" — can label a track from the file itself). Ties into "labels" more generally;
  revisit once that conversation has happened.
- **Build history in the About tab** (task 65 — mentioned twice now): a section at the
  bottom of the About tab showing the app's development history — feature implementations,
  changes, testing, bug fixes — human-readable, with a summary view and expandable
  comprehensive detail. Needs a design discussion first: granularity (per-version vs
  per-change), and how it's generated/kept in sync (git log, commit messages, or sourced
  from/derived from this file) — before scoping.
- **Revisit GPU acceleration more broadly** (task 64): not just task 41's merge-encode
  toggle — rendering, transcoding, previews, and playback too, given this session's GPU-decode
  crash history (see the `HybridPlaybackEngine` software-decode fallback). **Investigated**:
  tried adding GPU-accelerated scaling (`scale_qsv`/`vpp_qsv`) alongside the existing
  hardware-encode path, since a real merge test showed 100% "3D" engine but 0% "Video
  Processing" engine utilization in Task Manager. Root-caused via isolation testing: this
  machine's GPU/driver can run the QSV *encoder's own internal* frame pool fine (that's why
  hardware encode already works), but the moment *any* filter (`hwupload`, `scale_qsv`,
  `vpp_qsv`) needs to allocate its own separate D3D11 hardware texture pool, texture creation
  fails outright (`Could not create the texture`) — reproduced identically whether the source
  frames came from software or hardware decode, and even with a completely bare
  decode→filter→encode chain with no other app code involved. Not fixable in this codebase;
  the 100%-3D/0%-Video-Processing split is this machine's real ceiling for `hevc_qsv` at
  `veryslow`, not a missed optimization. Closed for now — no code changes made.
- **Dark-mode clip-table row banding is hard to read** (task 74, found 2026-07-04): both the
  Merge tab and the Extract and Recover tab's clip tables show every other row with a bright/
  light background and low-contrast text in dark mode — looks like `QTreeWidget`'s
  alternating-row-color palette isn't adapted for the dark theme. Needs a fix in the theme/
  table styling shared by both tabs.
- **Extract tab: Spec column full spec summary** (task 66): codec, fps, resolution, bit
  depth, colour space — the manifest path is missing fps/colour-space today; the no-manifest
  generic path shows a placeholder with no real spec at all (easy fix: every chapter shares
  the master's own baseline video stream, so one probe covers all rows).
- **Extract tab: Camera column shows the specific camera name** (task 67), not the generic
  "Camera" fallback — can now read the same persisted `camera_labels` Settings map task 61
  built for the Merge tab, so a camera named there is recognised here too.
- **Extract tab: Duration column** (task 68) between Clip and Camera.
- **Extract tab: per-clip preview button** (task 69) — same feature as task 63, applied here too.
- **Extract tab: MOV vs. MP4-with-separated-tracks recovery option** (task 70) — e.g. reverting
  an ALAC backup track to a standalone WAV file instead of staying muxed in.
- **Extract tab: "Create Folder" button** (task 71) for the output location — suggests a name
  (from the master filename) and a default location (same directory as the master), user can
  override both.

## App-wide + merge-tab refinement backlog — ALL RESOLVED

Observations from the user while using the app on their desktop; all six items below shipped
as tasks 39–44 (see the dated write-ups further down this file for full detail on each):

1. Window rescaling / inaccessible content at small sizes → **Task 44** (also specifically
   verified under the user's real 150% Windows display scaling).
2. Merge table selection checkboxes → **Task 39**.
3. Merge table Timestamp column with filename-mismatch warning → **Task 39**.
4. Merge tab GPU (NVENC/QSV/AMF) transcode option → **Task 41**.
5. Merge table sortable column headers → **Task 40**.
6. Log tab export/save + auto-save-on-failure → **Task 42**.

## Review-tab design refinement backlog — ALL RESOLVED

The five items from this critique (item 4 was resolved earlier, during the thumbnail-filmstrip
work; the remaining four shipped as **Task 43** — see its write-up below for full detail):

1. Accent overuse (section titles now `text_mute`, not `accent`) — done.
2. Transport row grouped by function with dividers — done.
3. Waveform-style sub-toggle hierarchy + taller scope canvas — done.
4. Overview density — resolved earlier by the thumbnail-filmstrip restructure.
5. Status-line success prominence (brief `ok`-green flash) — done.

## Multicam merge overhaul — progress

A real 4-camera test folder (Pixel 9 Pro, Insta360 Go3s + X4, Luna Ultra) drives this epic;
full plan in the plan file. Decisions: user-chosen baseline spec (recommended = duration-
weighted best-quality-of-majority, no upscaling); pad-to-fit (black/blurred, per project);
archival grouped by spec **+ rotation**, concat-default with an optional per-clip (bit-exact)
mode; per-clip audio; order by `creation_time`; fps from `avg_frame_rate`; per-clip restore
records + log.

- **Phase 1 — correctness foundation (done, verified on the real folder)**:
  - `probe.py`: now exposes `creation_time`, `rotation` (0..359), `device` (make/model, or a
    sanitised handler_name — strips control-byte prefixes + codec suffixes, so Insta360 X4's
    `\x10INS.AVC` → `INS`, Go3s → `Ambarella`), and `is_vfr`. **fps now from `avg_frame_rate`**
    (honours `com.android.capture.fps`), fixing the Pixel VFR clip that misreported as 120fps
    (`r_frame_rate`) — it's correctly 30fps VFR. Conformance is float-tolerant + treats VFR as
    a conform trigger.
  - New pure `camera_id.py`: device→filename→spec cascade → stable key + editable label.
  - `clip_model.py`: `order_clips_by_time` (creation_time, filename fallback); `_pair_wav`
    gains a `(date,time,clip-number)` key so cross-brand names pair (Insta360
    `LRV_…_01_NNN.lrv.WAV` ↔ `VID_…_00_NNN.mp4`).
  - `core/manifest.py`: `spec_signature` now includes **rotation** so differently-rotated
    clips never share an archival track (they'd lose orientation on recovery).
  - **Verified on the real folder**: 4 cameras detected (Pixel×3, Ambarella/Go3s×3, Luna×2,
    INS/X4×1); Pixel VFR→30fps; rotations rot270/rot180 read; creation-time order; 5/9 WAVs
    auto-paired (the correct 5); Luna 10-bit clips classify `ok` (stream-copy), rest transcode.
    Tests: `test_camera_id.py` (new), `test_manifest.py` (rotation split), all suites green.
  - Next: Phase 2 (dynamic baseline spec + recommendation + pad/blur conform).
- **Phase 2 — dynamic baseline (backend done; UI pending)**:
  - `core/baseline.py` (new, pure): `ClipSpec`/`SpecGroup`, `enumerate_specs`,
    `recommend_baseline` (duration-weighted best-quality-of-majority, no upscaling). On the
    real folder it recommends the Luna spec (HEVC 4K 10-bit). `test_baseline.py`.
  - `probe.py`: `BaselineSpec` + `DEFAULT_BASELINE` + `apply_conformance(info, baseline)`
    (extracted from the old inline block) — conformance now measured against a chosen
    baseline, re-runnable without re-probing; default behaviour byte-identical.
  - `core/ffmpeg_cmd.py`: `ConformSpec` + `_video_encoder_args` + parameterised
    `transcode_vf_parts`/`build_mux_cmd_plan` — transcode targets the chosen baseline's
    codec/res/fps/pix_fmt; **aspect-preserving pad (never stretch)** with black-bar or
    **blurred** fill; rotated clips fitted (90/270 forces a fit); VFR→CFR via the fps filter.
    `MergeWorker` carries a `conform` (default = today's 4K/HEVC/10-bit).
  - Verified on the real folder: rotated Insta360 clip fits to 3840×2160 (black + blur, no
    distortion); flat 4K clip does a pure codec transcode (no needless rescale); default
    classification + all suites green (38 ffmpeg_cmd tests, baseline, manifest, camera_id).
  - **Baseline-spec chooser UI (done)**: the merge tab's old (dead — `_res_mode` was never
    called) resolution-mismatch panel is repurposed into a baseline chooser — after probing
    it lists the distinct spec groups as selectable buttons with the recommended one marked;
    picking one reclassifies every clip against it (`apply_conformance`) and repopulates the
    table, and `_start_merge` passes the resulting `ConformSpec` (+ black/blurred fill combo)
    to `MergeWorker`. Also wired the Phase-1 creation-time ordering into the probe-done flow.
    Headless-verified on the real folder: 4 spec groups, recommends Luna 4K-10bit (2 clips
    stream-copy, 7 transcode), and choosing the H.264 baseline flips the 4 Insta360 clips to
    stream-copy — the full dynamic path from UI selection to conformance. All suites green.
  - **Phase 2 complete.**
- **Phase 3 — camera-grouped merge UI (in progress)**:
  - **3a (done)**: `ClipInfo` gains `camera_id`/`camera_label`; `clip_model.assign_cameras`
    (runs `camera_id.identify_camera` after probe, preserving user overrides) +
    `group_clips_by_camera`. The merge table's **Camera column now shows the detected
    camera** (spec moved to the cell tooltip). **Drag-and-drop a folder** onto the merge tab
    loads it. Verified on the real folder: column shows Pixel 9 Pro / Ambarella / Camera(Luna)
    / INS(X4); grouping → 4 cameras; drag-drop enabled. `test_camera_id.py` extended.
  - **3b (done)**: the flat `QTableWidget` clip list is now a `_CameraGroupTree(QTreeWidget)`
    — one top-level item per detected camera (ordered by that camera's earliest clip),
    clips nested underneath in chronological order. **Double-click a camera header to
    rename it** (propagates to every clip in the group). **Drag a clip onto a different
    camera group to reassign it** — overrides `dropEvent` to emit `clip_reassign_requested`
    rather than let Qt reparent directly, so `MergeTab`'s data model stays authoritative and
    a full rebuild keeps the tree consistent; expand/collapse state is preserved across
    rebuilds. Up/↓ reorder still operates on the single GLOBAL chronological order (not
    visual tree position) — grouping is a presentation layer only, the merge timeline stays
    one sequential cross-camera order per the "sequential archive" decision. `_reset_order`
    now uses the Phase-1 `order_clips_by_time` instead of filename-only sort.
    Verified on the real folder: 4 groups shown with correct interleaved chronological `#`s;
    renaming a group relabels all its clips; dragging the lone X4 clip into the Go3s group
    merges them (4→3 groups); up/down reorder still swaps GLOBAL order_idx across camera
    boundaries; selection resolves correctly; empty-clips edge case doesn't raise. All
    suites green.
  - **3c (done)**: an "Assign…" button next to the unmatched-WAV banner opens `_WavAssignDialog`
    — a combo per orphan WAV listing every clip (plus "— unused —"), applying chosen pairings
    on Apply (resets `wav_offset`/`sync_done`, probes the new `wav_duration`). Double-clicking a
    clip's WAV cell opens a per-clip swap dialog listing every WAV in the source folder
    (not just orphans), so a wrongly auto-paired WAV can be corrected too; picking a WAV
    another clip already has steals it away (a WAV pairs with at most one clip). Verified:
    dialog construction/population, the assign flow, and the swap-steals-from-another-clip
    flow, all driven through the real methods (not re-implemented logic) via a patched
    `QDialog.exec`. **Phase 3 (camera-grouped merge UI) is now complete.**
- **App-wide + merge-tab refinement backlog logged** (window rescaling/reachability, a
  selection-checkbox column with fade-when-excluded, a Timestamp column highlighting
  creation-time vs filename-time differences) — see the dedicated backlog section below;
  not acted on yet.

## Archival master / "Extract and Share" — Phase 1 progress

The "Extract and Share" + "metadata preservation" future-ideas above are now being
built together. Design decisions locked with the user: archival layout = **one video
track per camera/spec** (concat a camera's same-spec originals onto one track, re-cut at
keyframe-aligned boundaries on extract); recovery fidelity = **content-lossless**
(pixel/sample-identical, remuxed into the original container/filename — not byte-identical
files). Architecture: baseline conformed-4K track stays track 1 (default); only the
**non-conforming** originals need embedding, since conforming clips are already
stream-copied into the baseline and original AAC/WAV audio is already preserved.

- **Spike (done — decision gate passed)**: `tools/spike_archival.py` (throwaway) built a
  master carrying a baseline 4K-HEVC track (track 1, default) **plus** a parallel
  stream-copied 1080p-H.264 original track + its audio, then extracted the original back.
  Verified via **decoded-frame md5** (not file bytes):
    - parallel archival-track round-trip: **content-lossless** (video + audio md5 match);
    - per-spec concat of two same-spec originals + boundary re-cut of clip 2:
      **content-lossless**;
    - `ffprobe` confirms two video tracks with track 1 flagged `disposition:default` and
      the archival track non-default.
  Interop note: track-1-default is the correct signal for external tools (which read only
  the default track); a real player/NLE check on the desktop is still worth doing before
  shipping the format. Keyframe caveat: boundary re-cut is clean because each original
  begins with a keyframe at its exact boundary, so the manifest must store exact
  cumulative in-track offsets.
- **Manifest groundwork (done)**: `src/core/manifest.py` (pure — `Manifest`/`ClipEntry`
  dataclasses, JSON round-trip, `spec_signature` grouping, `assign_in_track_offsets`,
  sidecar + embedded-metadata helpers, `read_manifest`) with `tests/test_manifest.py`
  (incl. an embed-then-reparse integration test proving a custom key survives the MOV mux
  via `-movflags use_metadata_tags`). `MergeWorker` now builds a per-clip provenance
  manifest during every merge and writes it **two ways, both additive** (no change to the
  master's A/V streams): a `<master-stem>.manifest.json` sidecar and an embedded global
  metadata tag (skipped only if it would approach the command-line length limit — the
  sidecar always carries the full copy). `build_concat_cmd` gained an `extra_out_args`
  param for the embed. Verified: manifest unit tests pass; a probed two-clip set maps into
  correct entries (codec/dims/bit-depth/conform-status/spec-group/chapter-index); the
  concat command shape keeps chapters **and** the manifest tag in the output MOV.
  Remaining for Phase 2: the archival-track fields (`archival_track`, `in_track_*`) are
  defined but unset until odd-spec originals are actually embedded.

### Audio model (Phase 2 spec — not yet built)

Each clip has **two independent audio sources**, preserved and recovered by different
mechanisms (grounded in the merge's existing `_slot_fill` / `build_mux_cmd_plan`):

1. **In-clip camera audio** — the audio stream inside the MP4 (on-board / Bluetooth mic).
2. **The paired WAV** — the external lossless recorder file.

**Preservation:**
- **WAV → ALAC (always lossless).** The "wav" audio slot encodes each clip's WAV to ALAC;
  decoding it returns the exact original PCM. Recoverable content-lossless from the
  baseline for every clip.
- **Camera audio, conforming clips** — rides in the baseline "camera" slot; `-c:a copy`
  (lossless passthrough) **when it's already AAC**. No archival copy needed.
- **Camera audio, odd-spec clips** — stream-copied **together with the original video onto
  the same archival track** (the spike did exactly this: `-map v -map a -c copy`, both
  md5-identical on round-trip). Guarantees the exact original audio bitstream regardless of
  codec.

**Recovery (manifest-driven), per clip → an MP4 (video + camera audio) and a standalone WAV:**
- conforming clip: video from V1 chapter + camera audio from the baseline AAC track;
- odd-spec clip: video + camera audio both from its archival track (one stream copy);
- WAV: decode that clip's ALAC segment back to `.wav`.

**Manifest fields to add in Phase 2** (alongside the existing video-location fields):
`has_camera_audio` (bool), `original_audio_codec`, `camera_audio_location`
(baseline-track vs archival-track + index), `wav_present`, `wav_track`, `wav_in_track_start`,
and an **`audio_lossless`** flag.

**The `audio_lossless` decision to make in Phase 2:** a clip that *conforms on video* (so it
gets no archival track) but whose camera audio is **not AAC** would only survive in the
baseline as a lossy AAC re-encode. The `audio_lossless` flag drives whether such a clip
still needs an archival **audio** stream to keep its original bitstream. Decide this
explicitly rather than assuming AAC-copy is always lossless.

**Caveat (same as video):** recovered `.wav`/`.mp4` are sample-for-sample identical but
re-wrapped, so files aren't byte-identical to the camera originals — the content is.

### Phase 2 progress (in flight)

- **Multi-archival-track spike (done — `tools/spike_archival_p2.py`, throwaway)**: proved
  the Phase-2-specific risks beyond Phase 1's single-track spike. Built a master with a
  baseline 4K-HEVC track (v:0, default) **plus two archival tracks from distinct spec
  groups** (1080p-H.264 + AAC, and 720p-H.264 + AAC), then extracted each odd-spec clip's
  **video + camera audio by stream index** — both **content-lossless** (decoded md5 match).
  Confirms multiple archival tracks coexist and are individually recoverable.
  **Schema implication:** extract addresses clips by absolute stream index; in the real
  pipeline the baseline's own audio (camera AAC, WAV ALAC, optional mix) occupies the first
  audio slots, so archival audio starts at `a:2+`. The manifest must record each odd-spec
  clip's computed video- and camera-audio stream indices + in-track offset (the builder
  knows the exact final layout). Next: the pure archival command-builders, then the
  manifest audio/location fields matching this, then wiring an opt-in "Archival master"
  mode into the merge.
- **Builders + manifest + merge wiring (done)**: `build_archival_concat_cmd` +
  `build_final_archival_mux_cmd` (`core/ffmpeg_cmd.py`, unit-tested + proven end-to-end);
  manifest extended with audio fields (`has_camera_audio`, `original_audio_codec`,
  `audio_lossless`, `has_wav`) + stream-location fields (`archival_track`,
  `archival_audio_stream`, in-track offsets) + `baseline_audio_tracks`, plus
  `assign_archival_locations` (tested). `MergeWorker` gained an opt-in `archival` flag:
  it builds the baseline as an intermediate, concats each odd-spec spec-group's originals
  (or uses a lone original **directly** — bit-exact, avoiding concat-demuxer AAC-priming
  perturbation), assigns manifest locations, and muxes baseline + archival tracks into the
  final master with the complete manifest embedded. A merge-tab **"Archival master"**
  checkbox exposes it (default off). The non-archival path is unchanged (its Popen loop was
  extracted into `_run_stage`, reused by all stages).
- **Verified (done)**: targeted end-to-end test of the archival path (stand-in baseline +
  two odd-spec originals) — correct 7-stream master, manifest embedded + read back with
  right stream indices (`baseline_audio_tracks={camera:0,wav:1}`, archival tracks at
  v:1/a:2 and v:2/a:3), and **indexed extraction bit-exact (video + audio)** for
  single-clip groups. All unit suites green. Still worth a real archival merge on the
  user's footage (like Phase 1) as the final confirmation, and a normal-merge check to
  confirm the `_run_stage` refactor didn't disturb the non-archival deliverable.
- **Remaining for Phase 3**: the Extract UI (rename WhatsApp tab → "Extract and Share",
  read the manifest, recover clips + WAVs). Extract should input-seek to each clip's start
  keyframe for frame-exact video; multi-clip-group audio stays near-exact (AAC priming).

### Phase 2 finding — concat re-cut is NOT bit-exact (layout decision to revisit)

Testing the archival builders end-to-end revealed that extracting an individual clip
from a **concatenated** archival track (the chosen "one track per camera/spec" layout) is
**not sample-identical**: cutting at concat boundaries gives off-by-a-frame video and
AAC-priming audio differences (content is present, but md5 differs). Phase 1's spike only
md5-checked *video* on the re-cut, so this was missed. The **one-track-per-clip** layout
extracts a whole track with no cut and IS bit-exact (video + audio) — proven in the Phase 2
spike. This reopens the archival-layout decision (per-camera/spec concat + re-cut vs.
one-track-per-clip); the pure builders (`build_archival_concat_cmd`,
`build_final_archival_mux_cmd`) work for either.

**Resolved (user, informed of the trade-off):** keep **per-camera/spec concat**; accept
**content-complete, near-exact** recovery (boundary clips may differ by a frame / a few AAC
priming samples from the camera original) rather than adding one track per clip. Extract
(Phase 3) should minimise the drift: input-seek to each clip's start keyframe makes *video*
frame-exact; the residual is AAC audio priming at boundaries, which is inherent and
documented. The manifest records, per clip, its archival track + in-track start/duration
and the master stream indices so Extract can cut there.

### Field bug (found + fixed): archival concat choked on a Pixel phone's data stream

A real archival merge on the 4-cam test folder failed on the very first archival group:
`Archiving originals (1/5) failed` — ffmpeg: `Cannot map stream #0:2 - unsupported type` /
`Error opening output file …\archive_0.mov`. Root cause: `build_archival_concat_cmd` used a
blanket `-map 0` (all streams) to concat a spec-group's originals. Google Pixel phones embed
a third stream per clip — `#0:2, codec_type=data, codec=mett` (Motion Photo / telemetry
metadata) — confirmed via `ffprobe -show_entries stream=index,codec_type,codec_name` on the
user's actual `PXL_*.mp4` files. `-map 0 -c copy` pulls that stream in too, and the MOV
muxer refuses to stream-copy an unknown data codec type.

**Fix:** `build_archival_concat_cmd` now maps `0:v:0` and `0:a:0?` explicitly (the `?`
makes audio optional, so video-only groups still work) instead of a blanket `-map 0` —
matching the pattern every other command in this pipeline already used; this one command
was the outlier. **Verified by exact reproduction**: the old `-map 0` command run directly
against the user's real failing clips reproduces the identical error and exit code; the
fixed command succeeds cleanly (176 MB output, no errors). Also reconfirmed the already-
documented near-exact-recovery behaviour above is unaffected by this fix (a duration-based
re-cut of the first clip in that same group: audio matched exactly at t=0, video did not —
expected concat-boundary drift, not a regression). All test suites green;
`test_archival_concat_maps_only_video_and_audio` replaces the old test that asserted the
blanket `-map 0`.

### Field bug #2 (found + fixed): final archival mux choked on the baseline's chapter-text track

After the `mett`-stream fix above, a full real archival merge on the actual 9-clip multicam
folder got past the archival-concat stage (both spec-groups succeeded) but then failed at
**Finalising archive**: `Tag text incompatible with output codec id '98314'` /
`Could not write header (incorrect codec parameters?): Invalid data found when processing
input`. Root cause, found by isolating each stage: `build_final_archival_mux_cmd` mapped the
baseline with a blanket `-map 0`. The baseline is built with chapters (`build_concat_cmd`'s
`-map_metadata 1` from an FFMETADATA file), and ffmpeg's MOV muxer represents those chapters
internally as a hidden **QuickTime "chapter text" data stream** (`bin_data`, codec_tag
`text`) — confirmed directly: probing the real baseline showed exactly this as its 5th
stream, and a minimal repro (concat 2 clips with vs. without a chapters input) proved the
stream appears if and only if chapters are attached, regardless of how the chapters input
itself is mapped/excluded. Re-muxing that already-tagged stream via `-c copy` into the FINAL
master — which also carries chapters and several other video tracks — hits a codec tag/id
conflict the MOV muxer can't resolve.

**Fix:** `build_final_archival_mux_cmd` now maps the baseline as `0:v` + `0:a` explicitly
(video + all audio, no blanket `0`) instead of relying on `-map_chapters 0` alone to carry
chapters through — which it already does independently of the data stream. The muxer then
freshly (and safely) generates its own chapter-text stream for the new output rather than
copying the pre-existing, now-conflicting one. **Verified against the real files**: rebuilt
a fast-but-real baseline (actual `build_mux_cmd_plan`/`build_concat_cmd` code, only the
video preset/resolution swapped for speed) confirming all 9 individual per-clip mux outputs
are clean (4 streams each) and the stray stream only appears after concat-with-chapters;
then ran the actual (fixed) `build_final_archival_mux_cmd` against the real baseline + real
archival files end-to-end — succeeded, produced a valid 2.5 GB, 6-video-track master with
all 9 chapters intact, and a lone-clip archival track (`PXL_*.LS.mp4`, which has its own
3 extra `mett` streams and a non-zero-indexed audio track) recovered **bit-exact** (video
+ audio md5 match) via the manifest's absolute stream indices. All test suites green;
`test_final_archival_mux_maps_and_dispositions` updated to assert the explicit `0:v`/`0:a`
mapping and the absence of a blanket baseline map.

**Together, these two field bugs mean a real archival merge on genuinely messy multicam
footage (Pixel motion-photo metadata, QuickTime chapter tracks, mixed camera specs) now
completes successfully end-to-end** — proven on the user's actual 9-clip, 4-camera folder,
not just synthetic test clips.

### Phase 4 — restore records + per-clip archival toggle (done)

- **Restore records**: `ClipEntry` gains `rotation`, `is_vfr`, `color_space`, `camera_label`,
  `creation_time` — all already captured by Phase 1's `probe.StreamInfo`/`assign_cameras`,
  now threaded into `_build_manifest`. `core/manifest.py` gains `write_restore_log` — a
  plain-English `<master-stem>.restore.log` beside the sidecar/embedded manifest,
  per-clip: camera, spec (with rotation/VFR noted), recording time, exactly where it
  recovers from (baseline chapter vs. archival track — and whether that track is bit-exact
  or near-exact, spelling out the boundary caveat inline), and camera-audio/WAV recovery
  notes. Not consumed by Extract — the manifest is authoritative; this is for a human to
  read. `MergeWorker.run()` writes it alongside the sidecar, best-effort.
- **Per-clip archival toggle**: a "One track per clip (bit-exact)" checkbox next to
  "Archival master" (hidden until that's ticked). When on, `_build_and_mux_archival` groups
  odd-spec clips by `(spec_group, clip_index)` instead of just `spec_group`, so every clip
  gets its own singleton archival group — the existing lone-clip direct-copy path (proven
  bit-exact) then applies to every clip, not just already-lone ones. No manifest schema
  change needed — recovery is entirely location-driven (`archival_track`/`in_track_start`),
  so Extract adapts automatically to whichever mode produced the master.
- **A robustness fix found during this verification**: `build_final_archival_mux_cmd`'s
  baseline audio map (`0:a`, from the Field bug #2 fix above) hard-errors if the baseline
  happens to have zero audio tracks (all `OutputPlan` audio slots disabled) — surfaced by a
  synthetic per-clip-toggle test using a no-audio stand-in baseline. Fixed to `0:a?`
  (optional), matching every other audio map in this pipeline.
- **Verified**: restore-log content tests (baseline vs. archival wording, bit-exact vs.
  near-exact caveat only shown when it applies); a real (synthetic, fast) end-to-end test
  proving the toggle produces the correct track count in each mode (1 shared track by
  default for two same-spec clips, 2 separate tracks in per-clip mode) — this also caught
  and confirmed the `0:a?` fix. All suites green.

### Phase 5 — Extract and Share tab (done)

The payoff of the whole epic: recovering original clips back out of an archival master.
The WhatsApp tab is renamed **"Extract and Share"** in the tab bar (kept the `WhatsAppTab`
class name internally — a bigger rename would touch many call sites for no functional
gain). A segmented toggle at the top switches between the unchanged **Share** UI (wrapped,
unmodified, into its own panel) and a new **Extract** panel; they operate on different
kinds of source file and don't share state.

- **`core/extract.py`** (new, pure): `RecoveryPlan` + `build_recovery_plan(manifest, entry)`
  — works out exactly where a clip's video/audio/WAV live in the master:
    - **video**: `archival_track` at `in_track_start`/`in_track_duration` if it has one (an
      odd-spec original), else the baseline's video stream (0) at its
      `baseline_chapter_index`'s computed offset;
    - **camera audio**: `archival_audio_stream` (same archival track/window) when set, else
      the baseline's own camera-audio track at the same chapter offset (every clip's camera
      audio is stream-copied into the baseline uniformly, regardless of whether its video
      conformed — see the Phase-2 audio model);
    - **WAV backup**: always from the baseline's ALAC track at the chapter offset — WAV
      never rides an archival track.
  `compute_baseline_offsets` sums preceding clips' durations (every clip — conforming or
  not — occupies a baseline chapter, back-to-back with no gaps) to get exact chapter start
  times, since the manifest only stores the chapter *index*, not its time range.
  `build_recover_clip_cmd`/`build_recover_wav_cmd` build the actual ffmpeg stream-copy/PCM-
  decode commands (input-side `-ss` for a keyframe-accurate seek).
  **A real bug caught by the module's own tests**: the first draft used absolute stream-
  index map specifiers (`0:N`); `archival_track`/`archival_audio_stream`/
  `baseline_audio_tracks` are actually TYPE-relative (`v:N`/`a:N`), per how
  `assign_archival_locations` already populates them (Phase 2) — fixed before it ever ran
  for real.
- **`extract_workers.py`** (new): `ManifestLoadWorker` (reads the manifest off the UI
  thread) + `ExtractWorker` (recovers a batch of clips — video+audio, then WAV if present
  — emitting progress/per-clip completion/error signals; a failed WAV doesn't fail the
  clip, since the video/audio already landed). Same cancel-flag + tracked-Popen discipline
  as `review_workers.py`.
- **`whatsapp_tab.py`**: the entire existing Share UI is wrapped unchanged into
  `self._share_panel` (only `_setup_ui`'s first few lines changed, to build a wrapper
  layout instead of building directly on `self`). New Extract panel: master path row
  (Browse + drag-drop), a status label, a camera-grouped `QTreeWidget` of recoverable
  clips (checkbox per clip, Select all/none, spec + rotation shown, "recovers as" naming
  the output file(s) and flagging near-exact concat-boundary clips), output folder row,
  progress bar, Extract/Cancel buttons. `main.py`'s tab label updated to "Extract and
  Share".
- **Verified end-to-end, three ways**: (1) `core/extract.py`'s pure logic against a
  synthetic 5-clip mixed manifest (conforming/lone-archival/concat-group-first/concat-
  group-second), covering every recovery-sourcing branch; (2) a real ffmpeg integration
  test building an actual 2-clip archival master and recovering both clips **bit-exact**
  (video + audio md5 match) via the manifest's own embedded data; (3) the **complete real
  UI flow** — constructing `WhatsAppTab`, toggling to Extract mode, loading a real master
  through `_load_extract_master`, confirming the camera-grouped tree populates correctly
  (2 groups), running `_start_extract()` for real, and confirming the recovered files on
  disk are bit-exact matches to their originals. Offscreen renders confirm both panels
  lay out correctly in both themes. All suites green.

**With Phase 5 done, the full archive-and-recover loop (Merge → Archival master → Extract)
is built, wired, and proven end-to-end** — camera-grouped merge with a chosen baseline,
lossless archival tracks, a human-readable restore log, and a working Extract tab that
recovers the original clips back out.

### Review-tab integration (Phase 2) — CANCELLED

~~The multi-video-track master serves both recovery and review. Alongside the archival
original tracks, Phase 2 can add a small review proxy track (e.g. 960×540 H.264, opt-in per
merge) for smooth crash-free playback + cheap thumbnails.~~ **Cancelled by the user** — no
review proxy track. Thumbnails are instead sourced by sparse on-demand JPEG extraction
directly from the master (see "Overview thumbnail filmstrip" below); smooth crash-free
playback stays on `HybridPlaybackEngine`'s existing software-decode fallback. The timeline
**timestamp ruler + clip markers** still come from chapters/manifest the master already
carries. The four *standalone* Review polish items (accent-drawn snapshot camera, timestamp
ruler, scroll/pinch preview zoom, audio-lanes crop-to-viewport) shipped independently of
this and are unaffected by the cancellation.

### Overview thumbnail filmstrip + scrub-vs-viewport-box fix (done)

Two things raised after using the Review tab: (1) a thumbnail filmstrip along the overview
timeline (decoupled from the cancelled proxy track — sourced by sparse low-res JPEG frame
extraction directly from the master, the same "extract a few small frames fast" pattern
`ffmpeg_runner.ThumbnailThread` already uses for the merge tab's live preview); (2) a real
bug — scrubbing the video timeline was unreliable because the amber viewport-selection box
"got in the way."

**Root cause (bug):** `widgets/trackbar.py`'s `_hit_test` treated *any* click between the
viewport's x0/x1 as "drag the viewport body," regardless of whether the viewport was
actually zoomed in. Since a freshly loaded master's viewport defaults to the FULL duration
(x0=left edge, x1=right edge), essentially the entire trackbar was being swallowed as
"viewport-body," and playhead scrubbing was only possible in the last ~10px at each edge —
exactly the reported symptom.

**Fix:** `_hit_test` now only engages viewport-body/edge dragging when the viewport is a
genuine sub-range (`view_t1 - view_t0 < duration`), and always gives priority to grabbing the
actual playhead when the click is near its current position, even inside a zoomed viewport.
Verified with all four scenarios directly: unzoomed clicks scrub correctly everywhere
(previously broken), a click on the playhead always wins, and genuine zoomed-in
viewport-edge/body dragging still works exactly as before (no regression).

**Thumbnail filmstrip:** new `core.review_media.build_thumbnail_strip_cmd` (single coarse
`-ss` + small `scale=160:-2` JPEG — speed over frame-exactness, unlike the scopes/snapshot
builders) + `review_workers.ThumbnailStripWorker` (sparse, cancelable, emits progressively so
the strip fills in as each tile lands) + `widgets/trackbar.OverviewTrackbar` gains a
dedicated thumbnail row (`set_thumbnail_count`/`set_thumbnail`), which is also the multi-row
timeline restructure flagged in the Review-tab backlog's item 4 (thumbnails / envelope+
viewport / ruler, rather than cramming a fourth layer into one strip) — `_TRK_Y` moved down
to make room, `_paint_viewport`'s top offset now sits flush under the thumbnail row instead
of overlapping it. `review_tab.py` kicks the worker off once `TrackScanWorker` reports
duration, cancels any in-flight one on a new `load_master()`, and (like `_current_mix_worker`)
relies on the existing tracked-worker `shutdown()` for lifecycle safety.

**Verified end-to-end, twice:** (1) a standalone widget test — real ffmpeg extraction against
an actual multicam clip → worker signal emission → widget slot filling → paint (`grab()`)
succeeds; (2) the real `ReviewTab.load_master()` path against the same clip — `TrackScanWorker`
→ `_on_tracks_ready` → `_start_thumbnail_strip` → `ThumbnailStripWorker` → real extractions →
`trackbar.set_thumbnail()` — 24 slots reserved, filling in progressively. Offscreen renders in
both themes confirm the three-row layout (thumbnails / waveform+viewport+playhead / ruler) has
no geometry overlap. All existing suites stay green (38 ffmpeg_cmd, baseline, camera_id,
manifest, theme).

### Task 39 — merge-tab selection checkboxes + timestamp column (done)

Two additions to the merge tab's clip table: (1) every clip row now has a checkbox (on the
Clip-name column) — unticked clips are excluded from the merge entirely, letting the user
drop a bad take without deleting the file or moving it out of the source folder; (2) a new
Timestamp column showing the clip's actual capture time (from container `creation_time`,
falling back to the filename timestamp when metadata is missing) — with a warning colour +
tooltip when it disagrees with the filename-parsed time (typically camera clock timezone/DST
drift), so the user can see at a glance which ordering the merge actually used.

`ClipInfo` gained `selected: bool = True`; `MergeTab._selected_clips()` filters by it and
feeds every downstream consumer — the size/time estimate, the pre-flight dialog, and
`MergeWorker` itself now only ever see ticked clips. Unticking every clip and pressing Start
shows an information dialog ("tick at least one to merge") instead of silently no-opping or
crashing on an empty clip list.

**Verified two ways**, after the background verification script from the same work session
was found to have hung indefinitely (see the "hung headless test" note below): a fresh,
tighter headless script exercising the real Qt code path — `setCheckState()` on a synthetic
clip row → `itemChanged` → `_on_clip_check_toggled` → `clip.selected` flips → `_selected_clips()`
count drops; unticking every clip then calling the real `_start_merge()` confirms no
`MergeWorker` gets created — and a pure logic check of `_fmt_timestamp_cell` covering all
three cases (creation_time matches filename, creation_time disagrees with filename → warning
tooltip naming both times, and creation_time absent → filename fallback).

**Hung headless test (found during this session, not an app bug):** the *original*
verification script for this task was left running in the background and, when checked back
on, turned out to have been alive for ~2.5 hours while burning under a second of CPU time —
a dead giveaway for "blocked, not working." Root cause: it called the real `_start_merge()`
with every clip unticked, which correctly pops a real `QMessageBox.information()` — a modal
dialog whose own `exec()` blocks until a user clicks it. With no click ever coming in an
offscreen/headless run, the process hangs forever. Not a defect in `_start_merge()` itself
(the guard behaves exactly as intended in the real app); a test-script gap. Any future
headless test that exercises this path needs `QMessageBox.information` stubbed out first —
the fresh verification script above does this (`QMessageBox.information = staticmethod(lambda
*a, **k: None)`) and completes in under a second.

### Task 40 — merge-tab sortable column headers (done)

Clicking the Timestamp, Duration, or Camera header now view-sorts the clip table (click again
to reverse; click "#" to return to the default). This is purely a display reorder —
`clip.order_idx`, the actual merge sequence, is never touched — so it's safe to use for
finding a clip even after manually reordering with the ↑/↓ buttons, and the two features
don't fight each other: the up/down buttons are disabled (with an explanatory tooltip)
whenever a view-sort is active, since manual reordering only makes sense against the real
chronological order it operates on.

Timestamp and Duration sort the clips *within* each camera group; Camera instead reorders
the groups themselves (alphabetically by label). New `_clip_time_sort_key(clip)` gives the
Timestamp column a real sortable value — `clip_model._iso_epoch(creation_time)` when
available, else `filename_ts` — matching the same source-of-truth preference
`_fmt_timestamp_cell` already displays, so sorting by Timestamp shows the clip's *true*
capture-time order even when a user has manually swapped `order_idx` away from it (the
scenario this is actually useful for).

Implementation: `_CameraGroupTree`'s header gets `setSectionsClickable(True)` +
`sectionClicked` wired to `_on_header_clicked`, which just sets `_view_sort_col`/
`_view_sort_asc` and calls the existing `_populate_table()` — no new rendering path, it's the
same rebuild `_populate_table` already did, now sorting `ordered_ids`/`members` by an
alternate key when a view-sort is active instead of always chronological. `_load_folder`
resets the sort state so a fresh folder always opens in chronological order.

**Verified** with three focused headless scripts against real (not mocked) `_populate_table`/
`_on_header_clicked`/`_add_clip_row` code paths: (1) Camera-header ascending/descending +
reset-via-"#", confirming group order changes and clip order within each group stays
untouched; (2) Duration-header sort within a group, confirming cross-group ordering is
unaffected and the ↑/↓ buttons disable with the sort active then re-enable on reset; (3) a
deliberately adversarial case — two clips whose `order_idx` disagrees with their real
`creation_time` (simulating a manual reorder) — confirming the Timestamp sort shows the true
capture order while `order_idx` itself is provably untouched throughout. Full regression
suite (baseline, camera_id, manifest, 42 ffmpeg_cmd, extract, gpu_encode) stays green.

### Task 41 — GPU hardware transcode option (done)

An opt-in "GPU encode" toggle on the merge tab: non-conforming clips (the ones that need a
real transcode against the chosen baseline) can now use a hardware encoder — NVENC, QSV, or
AMF — instead of always paying for `libx264`/`libx265` on the CPU.

**Detection is a real encode, not a feature list.** `ffmpeg -encoders` lists every codec the
binary was *compiled* with, regardless of whether this machine actually has a matching GPU —
a driver-less box still lists `hevc_nvenc`. New `core/gpu_encode.py` instead runs a tiny
2-frame `testsrc` → null-output encode per vendor and checks the real exit code, caching the
result per (ffmpeg path, codec) for the process lifetime. Confirmed on the dev machine itself:
`hevc_nvenc`/`hevc_amf`/`h264_amf` all *list* successfully but fail to open at encode time
(`Cannot load nvcuda.dll`, `Could not open encoder before EOF`) — no NVIDIA/AMD GPU present —
while `hevc_qsv`/`h264_qsv` both genuinely work (Intel Quick Sync). `detect_best_hw` tries
nvenc → qsv → amf in that order and returns the first that actually works, or `None`.

(A pre-existing, entirely unused `core/encoders.py` + `tests/test_encoders.py` from the
original v1.3 commit did something similar via a static `-encoders` listing rather than a real
functional probe — never wired into the app. Removed as dead code during the task 45 final
pass once this newer, better-verified module made it fully redundant; see that write-up.)

**Wiring:** `ConformSpec` gained `hw_encoder: str = "off"` ("off" | "auto" | "nvenc" | "qsv" |
"amf"). `core.ffmpeg_cmd._video_encoder_args` now takes an optional `ff` path — when
`hw_encoder` isn't "off" and `ff` is given, it resolves a vendor (via `detect_best_hw` for
"auto", or the named vendor directly) and asks `gpu_encode.hw_video_encoder_args` for the
vendor-specific args (NVENC: `-preset p6 -tune hq -rc vbr -cq 18`; QSV: `-preset veryslow
-global_quality 18`; AMF: `-rc cqp -qp_i 18 -qp_p 20`), quality-matched to the existing
`-crf 18` software default. 10-bit conforms map to the `p010le` hardware surface format (not
`yuv420p10le`, which none of the three vendors' encoders accept); `hvc1`/`main10` tagging is
preserved for HEVC exactly as the software path already did. Passing no `ff` (or `hw_encoder
="off"`) reproduces the exact previous software-only command — zero behaviour change for
existing callers/tests.

**Graceful fallback lives in `MergeWorker`, not just at build time.** A per-clip transcode
that requested GPU and still fails (VRAM exhausted, encoder session limit, a driver hiccup)
retries once in software before failing the whole merge — the per-clip run loop was factored
out into `_run_clip_proc` (returns `"ok"|"failed"|"cancelled"` instead of emitting `finished`
directly) so the retry could be layered on top of it without duplicating the progress/cancel
polling loop.

**UI:** a "GPU encode" checkbox next to the pad-fill combo (`merge_tab.py`), auto-detected
once per session via a small `_GpuProbeThread` (mirrors the existing `ProbeThread` pattern),
auto-checked and enabled only when a vendor actually probes as working, with a tooltip naming
the detected vendor — disabled with an explanatory tooltip when none does. Feeds
`ConformSpec.hw_encoder="auto"` through `_current_conform()` when ticked.

**A real bug this caught before it shipped:** the first wiring of `_start_gpu_probe` passed
`get_ffmpeg()`'s *second* tuple element (the `ffprobe` binary) to the encoder probe instead of
the first (`ffmpeg`) — `ffprobe` can't encode anything, so every vendor probe silently failed
and the checkbox always reported "no GPU detected" even with a working QSV encoder. Caught by
a headless offscreen test asserting `tab._gpu_vendors == ['qsv']` against the real bundled
ffmpeg; fixed by swapping which tuple element is used.

**Verified for real, end-to-end, three ways:** (1) `tests/test_gpu_encode.py` — pure arg-
building/vendor-priority/caching logic, plus a real-machine probe confirming `['qsv']`; (2)
extended `tests/test_ffmpeg_cmd.py` — `_video_encoder_args` stays software-only by default,
never engages hardware without an `ff` path, an explicit vendor request skips the detection
probe entirely, and "auto" falls back to software when nothing works (42 tests total, all
green); (3) two throwaway headless scripts (not part of the checked-in suite) proving real
ffmpeg behaviour: a `build_mux_cmd_plan` command with `hw_encoder="auto"` actually ran through
`hevc_qsv` and produced a valid 10-bit HEVC file on this machine, and a full `MergeWorker` run
with `hw_encoder="nvenc"` (guaranteed to fail here) still completed successfully — proving the
software-fallback retry actually engages end-to-end, not just in unit tests.

### Task 42 — Log tab export/save + auto-save-on-failure (done)

Three additions to the Log tab: (1) an "Export…" button that saves a plain-text rendering of
either the selected entry (if one's picked) or the whole log (if nothing is selected) to a
user-chosen `.txt` file; (2) every FAILED merge/export now automatically writes its own
timestamped `.txt` file to `<app dir>/failure_logs/`, so a diagnostic exists even if the user
never opens the Log tab; (3) an "Auto-save .txt on failure" checkbox to opt out, persisted to
settings (`auto_save_log_on_failure`, defaulting **on** — failures are exactly the moment a
user is least likely to remember to manually export a log, so the safer default captures it
automatically).

The three pieces around log content — the Log tab's detail pane, the new Export button, and
the auto-save file — all render from one new pure function, `log_manager.render_entry_text
(entry)`, extracted from what used to be `LogTab._render_detail`'s inline formatting so all
three can never drift out of sync with each other.

Wired at the single choke point both `log_merge()` and `log_whatsapp()` already funnel
through — `log_manager._append()` — rather than duplicated in `merge_tab.py` and
`whatsapp_tab.py`'s separate `_on_finished` handlers: after every entry is appended to
`export_log.json`, a `success: False` entry additionally gets `_write_failure_txt()` if the
setting is enabled. This means the auto-save behaviour automatically covers both the Merge
tab and the Extract/Share tab's exports without either tab needing its own copy of the logic.

**Verified**: `tests/test_log_manager.py` — `render_entry_text` for both entry types (merge/
whatsapp) including the failed-with-message case; `_write_failure_txt` writes a real file
with the right content to the real `failure_logs/` folder (cleaned up after); `_append`'s
gating logic confirmed three ways (success → never auto-saved, failure+enabled → auto-saved
exactly once, failure+disabled → not auto-saved) via a swap-and-restore patch of
`_write_failure_txt`/`_auto_save_enabled`/`_log_path`, avoiding any dependency on pytest's
`monkeypatch` fixture (this suite runs standalone too). A separate headless Qt test confirms
the Log tab's checkbox reflects and persists the real settings value, and that Export produces
a real `.txt` file via a stubbed `QFileDialog.getSaveFileName` (native dialogs can't be driven
headlessly). Full regression suite (baseline, camera_id, manifest, 42 ffmpeg_cmd, extract,
gpu_encode, crash_log) stays green.

### Task 43 — Review tab design refinement pass (done)

The four remaining items from the Review-tab design critique backlog (item 4, the overview
timeline, was already resolved earlier — see "Overview thumbnail filmstrip" above):

1. **Accent overuse.** Section titles (both tabs — `review_tab.py` and `merge_tab.py` use
   the identical pattern, so both were fixed together to keep the two tabs visually
   consistent) now render `text_mute` instead of `accent`. Accent is reserved for
   interactive/active states (toggles, the primary action, the playhead) — a plain,
   non-clickable label shouldn't wear the same colour as something you can click.
2. **Transport row grouped by function.** A thin `QFrame` vertical divider (new
   `_make_vline()` helper) now separates navigation (skip/step/play/step/skip) from the jog
   shuttle from the snapshot action, instead of seven controls sitting in one undifferentiated
   row — reinforces that the camera is an *action*, not transport.
3. **Waveform style hierarchy.** The Parade/RGB sub-toggle now sits behind an explicit
   "Waveform style:" label and renders visually subordinate (smaller font/padding, muted
   default colour vs. the main Histogram/Waveform toggle's full button styling) — it reads as
   a style of Waveform, not a third peer mode. The scope canvas also grew from a 110px to a
   170px minimum height, with the actual waveform render resolution (`out_h`) bumped from 96
   to 150 to match (avoiding a blurry upscale into the taller canvas).
4. **Status-line success prominence.** New `ReviewTab._flash_status_ok(message)` briefly
   tints the status line `ok`-green + bold for 1.8s on a successful snapshot save, then fades
   back to the normal muted style via `_unflash_status`, which checks the label's current text
   against what it expects to revert *from* — guarding against a delayed revert stomping a
   newer message (e.g. an error) that arrived in the meantime.

**Verified** with a headless offscreen script exercising all four real code paths — section
titles muted in both tabs, exactly 2 transport dividers present, canvas height + waveform
style label visibility toggling correctly with mode, and the flash/unflash/stale-guard status
line behaviour. **Two real Qt test-methodology gotchas hit and resolved while verifying, both
already-known patterns from earlier this session**: (1) `isVisible()` needs `app.
processEvents()` after `show()` to actually reflect layout state in an offscreen run; (2) the
scopes column's `isVisible()` is *always* False in a fresh, no-master-loaded tab (the outer
frame is deliberately hidden until something's loaded — real, intentional app behaviour, not a
bug) — worked around with `widget.isVisibleTo(scopes_panel)` to check the toggle's own
show/hide logic independent of that outer hidden state. Full regression suite (baseline,
camera_id, manifest, 42 ffmpeg_cmd, extract, gpu_encode, log_manager, crash_log, scopes,
theme) stays green.

### Task 44 — App-wide responsive rescaling pass (done)

Investigated specifically under the user's actual environment — Windows scaled to **150%** —
rather than just generic small-window resizing, per their explicit instruction.

**Audited every tab for the actual clipping mechanism.** `merge_tab.py`, `whatsapp_tab.py`,
`about_tab.py`, and `preflight_dialog.py` already wrap their content in a `QScrollArea`
(`setWidgetResizable(True)`) — so under a too-short window, Qt lets the user scroll instead of
either compressing content into unreadable minimums or refusing to shrink the window at all.
**`review_tab.py` had no such wrap** — a plain `QVBoxLayout(self)` holding several stacked,
non-trivial-height sections (Preview+Scopes, Audio tracks, Overview), each with real minimum
heights (video view 160×90, scopes canvas *just bumped from 110→170px in task 43*, audio
lanes 48px, plus the trackbar). On a physically small screen at 150% scaling — where the
*logical* (DIP) screen real estate shrinks by that same 1.5×, e.g. a 1366×768 laptop panel
effectively offering only ~910×512 logical pixels — this tab's summed minimum heights could
easily exceed the available window height with **no scrollbar and no way to reach the
clipped sections**, matching the user's originally-reported symptom exactly. Fixed by wrapping
`review_tab.py`'s content in the identical `QScrollArea` pattern already proven in
`merge_tab.py` — a one-line-comment-documented, structurally minimal change (renamed the
existing content layout variable into the scroll area's inner widget; every subsequent
`root.addWidget/addLayout` call needed no other edits).

**`log_tab.py` didn't need the same fix** — its content is a `QSplitter` (table + detail
pane), and both children already scroll internally on their own; a splitter has no
"combined minimum exceeds window" failure mode the way a stacked `QVBoxLayout` does.

**`main.py`'s DPI setup.** Removed the `AA_EnableHighDpiScaling`/`AA_UseHighDpiPixmaps` calls
(Qt5-era attributes; Qt6 always scales by default, so these were literal no-ops — the actual
source of the deprecation-warning spam seen in the app's own console output, not a real
behaviour difference). Replaced with an explicit
`QApplication.setHighDpiScaleFactorRoundingPolicy(PassThrough)`, set (as required) before
`QApplication` is constructed. Checked this PySide6 build (6.11.1): `PassThrough` is already
the Qt6 default, so this doesn't change current behaviour — but it's the one setting that
*would* matter for 150% specifically: any other rounding policy (`Round`, `Ceil`) snaps a
non-integer scale factor to the nearest whole number, meaning 150% could get treated as 200%
and oversize every widget — a plausible, scaling-factor-specific root cause distinct from the
generic small-window case. Pinning it explicitly means the app's behaviour at 150% can't
silently change if a future Qt/PySide6 version ships a different default.

**Verified**: a headless test confirms the rounding policy is really `PassThrough` after
`QApplication` construction, and that `review_tab.py`'s new `QScrollArea` is correctly wired
(`_scroll.widget() is _content`, `widgetResizable() is True`) with every existing section
still reachable and `_set_loaded_visible()` behaving identically to before. **The decisive
test** directly reproduces the reported symptom and proves the fix: load a (synthetic)
master so all sections are visible, resize the tab down to 300px tall (shorter than the
content's natural 511px), and confirm the scroll area's vertical scrollbar has real range
(0–211) reaching all the way to the Overview section — the one that would previously have
been silently clipped off with no way to reach it. Full regression suite (baseline,
camera_id, manifest, 42 ffmpeg_cmd, extract, gpu_encode, log_manager, crash_log, scopes,
theme) stays green; full `src/` tree compiles cleanly.

### Task 45 — Final verification pass + wrap-up (done)

Full regression pass across all 18 test files (up from the ~6-file subsets spot-checked after
each individual task) plus a full-tree `py_compile`, an end-to-end headless boot of the real
`MainWindow` (all 5 tabs construct and wire cleanly, including `LogTab(settings)`'s new
constructor signature from task 42), and this reorder-into-chronological-order pass over the
task 39–44 write-ups above (they'd landed in the order each task was picked up, not task
number, since each new write-up was inserted at "the current end of the list").

**A real duplicate-effort finding, caught only because this pass ran the FULL suite instead of
a hand-picked subset**: `tests/test_encoders.py` (5 passing tests, all green) tests a
`core/encoders.py` that turned out to be completely unused anywhere in the app — present since
the original v1.3 commit, long before this session's multicam/archival epic, and never wired
into `ffmpeg_cmd.py`/`ffmpeg_runner.py`/`merge_tab.py`. It did almost the same job as this
session's task 41 `core/gpu_encode.py`, but via a strictly weaker method: trusting
`ffmpeg -encoders`'s static list of *compiled* codecs rather than task 41's real functional
probe — the same gap task 41 explicitly worked around after empirically proving on this exact
machine that the static list lies (`hevc_nvenc` lists as available but fails to open with no
NVIDIA GPU present). Confirmed via `git log` that it predates all other work and via grep that
nothing imports it. Removed both files (with the user's explicit sign-off, prompted by the
safety system correctly flagging a pre-existing-file deletion as needing confirmation rather
than my own unilateral judgement) rather than leaving two parallel, differently-reliable GPU-
encoder-selection implementations in the codebase.

**Final state**: all 18 test files pass, the full `src/` tree compiles, the app boots
end-to-end with all 5 tabs constructing correctly. Both design-critique backlog sections
above (App-wide + merge-tab; Review-tab) are now marked all-resolved with pointers to their
task write-ups rather than left as stale open items. Nothing in this session has been
committed yet — left for the user to review first, per the standing git-safety rule of never
committing without being asked.

## Real-usage feedback round — tasks 46-60

After the v1.4/multicam-overhaul work above, the user actually used the app on their desktop
and reported a batch of real bugs and feature requests together. Presented as a to-do list
for review before any action (per their explicit request), then worked through bugs-first,
features-second (their stated priority).

### Task 46/47 — clip-loading hang / blank Status·Camera·Duration after header-click (done)

**The root-cause bug of this whole batch.** Dropping the real 9-clip multicam folder showed
"9 found" in the title but left the table empty for 10+ minutes; clicking the Camera header
then made rows appear, but with Status colours gone, Camera showing "unknown", and Duration
blank.

**Root cause:** `merge_tab.py`'s `_load_folder()` never called `_populate_table()` or
`_start_probe()` — those calls only existed inside `_open_wav_assign_dialog()` (the "assign
unmatched WAV files" dialog handler), reachable only when the folder has unmatched WAVs.
A folder with none (like this real one) left `self._clips` populated but never probed and
the table never built at all. Confirmed via `git show HEAD:src/merge_tab.py` that this bug
**predates this entire session** — identical at the last commit, so none of today's earlier
task 39-45 work introduced it. Clicking the Camera header (task 40's `_on_header_clicked`)
happened to call `_populate_table()` directly, which is why rows suddenly appeared — but
since probing had genuinely never run, every probe-dependent field (`clip.status`,
`clip.duration`, `clip.camera_id`) was still at its unprobed default.

**Fix:** `_load_folder()` now calls `_populate_table()` (so clips show immediately, even
before probing fills them in — safe, since every consumer already null-guards `clip.stream`)
and `_start_probe()` (so ffprobe actually runs) right after loading.

**Verified** directly against the real 9-clip folder: reproduced the raw probe pipeline
standalone first (all 9 clips probe in under 0.4s each — ruling out an ffprobe-level hang),
then a real headless `MergeTab` load confirmed camera groups now correctly resolve
(`Google Pixel 9 Pro`, `Ambarella`, `INS`, etc. — not "unknown"), probing completes in ~2.5s,
and every clip row shows a real duration + status badge widget.

### Task 50 — GPU-encode checkbox repositioned near Advanced/Pre-flight (done)

The checkbox (built in task 41) previously lived inside the baseline-spec chooser, which
never appeared while task 46's bug was live (no spec classification without probing) — likely
why the user never saw it. Verified it now reappears correctly once 46/47 were fixed, and
moved it to the main action bar next to Pre-flight/Start (was tucked in the pad-fill row)
for better discoverability regardless, per the user's explicit preference.

### Task 51 — loading progress bar above the clips table (done)

New progress bar + "N / total probed" label, shown while `ProbeThread` runs and hidden on
completion — previously the only feedback during probing was the static "N found" title text,
which is exactly what made task 46's hang look identical to normal (if slower) loading.
Verified against the real folder: reaches 100% and hides correctly; verifying this needed a
signal-driven capture rather than a polling loop, since polling can race ahead of the very
last queued signal before a QThread's `isRunning()` flips (a test-harness gotcha, not an app
bug — see task 48/49 below for the same class of gotcha, encountered again and resolved the
same way).

### Task 48 — Review tab overview thumbnails (done) — the real bug was speed, not brokenness

Reproduced directly: on a real 4K 10-bit HEVC clip, a single-frame ffmpeg extraction took
1.1s at 0.2s into the file, climbing to 5.2s at 9.5s in (measured directly) — the filmstrip
requests 24 of these, sequentially, so 30-100+ seconds total, worsening the later into the
file a thumbnail sits. That's indistinguishable from "broken" without the progress feedback
task 51 now provides elsewhere, and easily exceeds how long a user would wait before
concluding thumbnails "never appear."

**Fix:** `core.review_media.build_thumbnail_strip_cmd` now passes `-skip_frame nokey`,
telling the decoder to jump straight to the nearest keyframe instead of decoding every
intervening P-frame to reach the exact target — measured directly on the same clip, every
position dropped to a flat ~0.5s regardless of how far into the file it was. A thumbnail
tile is a rough filmstrip marker, not a precision reading, so landing up to one GOP away from
the exact requested timestamp is an easy trade for a ~10x speedup.

**Verified**: the same real clip that only delivered 13/24 thumbnails after 40 seconds (and
still climbing) before the fix now reliably delivers 24/24 in ~15 seconds after it.

### Task 49 — Review tab waveform/spectrogram on large masters (done — mechanism confirmed sound)

Could not reproduce the exact reported symptom ("no waveform/spectrogram on a large master")
against the real large files available for testing (a real 297s/1.9GB dual-audio-track file):
`PeakScanWorker` — which has no timeout at all, unlike the thumbnail worker — delivered both
tracks' peak pyramids correctly in under 3 seconds. A real ffmpeg-level timing check confirmed
the underlying PCM extraction itself takes under 1 second per track regardless of file size,
since it's audio-only extraction (ffmpeg doesn't decode video frames nothing maps to an
output), not a video-decode-bound operation like the thumbnail case.

**A genuine test-harness gotcha along the way, not an app bug**: an early version of this same
investigation appeared to show one track's `pyramid_ready` signal going missing — traced to a
polling loop that stopped calling `app.processEvents()` the instant `QThread.isRunning()`
flipped to `False`, racing ahead of the very last queued cross-thread signal actually being
dispatched. A real, continuously-running GUI event loop (as the shipped app has) never hits
this; adding a short grace period after the flip in the test script confirmed both signals
were correctly emitted and received all along.

Given the underlying mechanism is confirmed correct and fast on real large-file audio, and
the thumbnail slowness fix (task 48) addresses the most likely source of "the Review tab feels
broken/slow on a large master" as a *combined* loading experience, this is closed without a
further code change pending a reproduction with the user's own specific file if the symptom
persists.

### Task 56 — audio pipeline: does downsampling help? (answered — already implemented)

Checked directly: `core/audio_peaks.py`'s `DEFAULT_RATE` (used by both the waveform peak-scan
and the spectrogram) is already 8kHz mono — well below the source's native 44.1/48kHz, and the
downsampling happens *in ffmpeg itself* during extraction (not decoded at full rate then
downsampled in Python), so the CPU cost genuinely scales down with the target rate, not just
the output size. This already matches the user's own suggestion; there isn't a further
"sample at a lower resolution" lever to pull here without visibly harming spectrogram
frequency resolution. The actual bottleneck found this round (task 48's thumbnail extraction)
was video-decode-bound, not audio-resolution-bound — a different part of the pipeline than
where downsampling would help.

### Task 52 — Extract tab: adaptive chapter-based recovery for masters with no manifest (done)

Previously, loading a master with no manifest (embedded or sidecar) just refused: "No
manifest found... nothing to recover." Every master this app produces titles its chapters
with the *original clip's filename stem* (`ffmpeg_runner.run()`'s chapters_file writing) —
regardless of whether "Archival master" was ticked or whether a manifest survived — so the
chapter list alone is enough to recover each clip's time range, a sensible output filename,
and (per the user's chosen scope) a guessed camera grouping, all without a manifest.

**New pure logic** (`core/extract.py`): `GenericRecoveryPlan` + `build_generic_recovery_plans
(chapters, audio_track_indices)` — one plan per chapter, camera identity guessed via the same
`camera_id.identify_camera` cascade used at merge time (fed the chapter title as the
"filename"), assuming the master's first audio track is camera audio (this app's own
camera-then-WAV track-order convention; there's no manifest to confirm it either way).
`build_generic_recover_clip_cmd` trims the master's own baseline video/first-audio stream at
the chapter's start/duration via input-side `-ss` (frame-exact via keyframe-snap, same as the
manifest-driven path) + stream copy. `generic_recovered_filename` names the output from the
chapter title (or a positional `chapter_NNN` fallback for an untitled/third-party MOV).

**Wiring**: `ManifestLoadWorker` (`extract_workers.py`) now also probes chapters + audio
tracks unconditionally (one more cheap ffprobe call) so the UI can fall back immediately
without a second round-trip; new `GenericExtractWorker` mirrors `ExtractWorker`'s shape for
the actual recovery batch. `whatsapp_tab.py`'s `_on_extract_manifest_ready` now branches:
real manifest → existing behaviour unchanged; no manifest but real chapters → the new
fallback (with a status message explicitly explaining what's guessed vs. known); neither →
the original "nothing to recover" message.

**Verified end-to-end against real footage**, deliberately built to reproduce the exact
no-manifest scenario: merged two real clips into a master (chapters, camera-titled), then
stripped its embedded manifest tag and deleted its sidecar (`-map_metadata` set to blank the
tag while explicit `-map 0:v -map 0:a?` avoided reintroducing the same hidden chapter-text-
stream conflict as an earlier field bug this session) — loading it through the real
`WhatsAppTab` UI correctly showed "No manifest found — falling back to 2 chapter-marked
segments," correctly grouped both clips under their real camera label, and a real extraction
run recovered both clips with durations matching the originals to within a few milliseconds
(72.139s vs. 72.138733s original; 18.052367s vs. 18.051367s original).

### Tasks 53-55 — Review tab preview: 16:9 lock, drag zoom slider, full-res zoomed inspection (done)

Three related preview-quality requests, all in `widgets/video_view.py` + `review_tab.py`:

- **16:9 lock (53)**: `ZoomableVideoView` now computes an `_active_rect()` — the largest 16:9
  box centred within whatever rect the layout actually gives the widget — and every zoom/pan/
  paint calculation operates relative to that box, not the raw widget rect. Dead space outside
  the 16:9 box (when the panel's own shape isn't 16:9) reads as `input_dk`-toned letterbox
  bars, distinct from the player's own background, rather than looking like unstyled empty
  space.
- **Drag zoom slider (54)**: the Fit/1:1 presets stay in the Preview section header; the old
  numeric `QSpinBox` percent-entry is replaced with a vertical `QSlider` running the full
  height of the preview next to it — drag up to zoom in, down to out (Qt's own vertical-slider
  default already puts the minimum at the bottom), synced bidirectionally with wheel/pinch zoom
  exactly like the spinbox was.
- **Full-res frame on paused + zoomed (55)**: reuses the *same* exact-frame fetch the scopes
  panel already triggers on every pause/step (`FrameFetchWorker`, mode="frame") — no second
  ffmpeg call — and additionally swaps it into the video preview itself whenever
  `zoom_mode() != "fit"`, addressing the live/proxy frame (especially the software-decode
  fallback's periodic low-res extraction) looking soft once zoomed in. A new debounced timer
  (`_zoom_frame_timer`, 200ms, mirroring the existing `_spec_timer` pattern) re-triggers this
  as the zoom slider is dragged while paused, without spawning a new ffmpeg process on every
  intermediate slider tick; it's a no-op while playing (checked directly).

**Verified**: a real (non-null) 3840×2160 frame loaded into a 1000×1000 (deliberately
non-16:9) widget confirms the active rect is exactly 1000×562 (16:9, constrained by the
shorter dimension); the slider and `ZoomableVideoView.zoom_changed` stay in sync in both
directions and the Fit/1:1 buttons still reset it correctly; the debounce timer arms only
while paused, never while playing; and `_on_exact_frame` correctly leaves the preview frame
untouched in "fit" mode but swaps in a full 3840×2160 frame once zoomed past it.

### Task 59 — move "Share a clip" from Extract to Review (done)

Per the user's chosen scope: Share now lives in the Review tab; Extract keeps a shortcut
back for anyone used to the old combined layout.

Given the Share half was ~700 lines of tightly-coupled state/methods (preview scrubbing,
before/after grading, size estimation, export workers), a full code migration into
`review_tab.py` carried real regression risk for what's fundamentally a UI-placement
request, not a bug or new behaviour. Took the lower-risk path instead: `WhatsAppTab` still
builds its Share panel exactly as before (zero changes to any of that internal logic) via a
new `share_panel()` accessor, but no longer adds it to its own layout — `main.py` reparents
the same widget into a new collapsible "Share a clip" section in `ReviewTab`
(`embed_share_panel()`). `WhatsAppTab` becomes Extract-only, with the segmented mode-toggle
removed entirely and a "Share a clip →" button (`open_share_requested` signal) that switches
to the Review tab and expands the section (`reveal_share_panel()`).

Since the widget's underlying Python object and all its methods are unchanged and still
owned by the same `WhatsAppTab` instance — only its Qt *parent* widget changed — every
existing cross-tab wire-up (`merge_tab.merge_complete → whatsapp_tab.set_source`) keeps
working with no changes needed there either.

**Verified end-to-end**: the Share panel widget's real parent is confirmed to be
`ReviewTab`'s share-section body, not `WhatsAppTab`'s own layout; the section starts
collapsed; the old mode-toggle attributes are confirmed gone; the shortcut button correctly
switches the main window to the Review tab AND expands the section; and a simulated
`merge_complete` signal still correctly reaches the Share panel's source-file field despite
it now living inside a different tab's widget tree.

### Task 60 — merge tab: prompt to name camera groups on folder load (done)

New `_CameraNamingDialog` (mirrors the existing `_WavAssignDialog`'s Skip/Apply-style
pattern) shown exactly once, right after a fresh folder's clips finish probing and get
grouped by camera — one pre-filled, editable field per detected camera (count + guessed
label), so the user can confirm or rename each up front rather than only discovering the
existing double-click-to-rename affordance after the fact (which stays available for fixing
a name later).

Wired via a one-shot flag (`_pending_camera_naming_prompt`, set in `_load_folder`, consumed
in `_on_probe_done`) rather than tying the prompt to every `_start_probe()` call — probing
can legitimately re-run later (e.g. after the WAV-assign dialog re-syncs newly paired clips),
and re-prompting for camera names every time that happens would be annoying, not helpful.

**Verified against the real 9-clip multicam folder**, stubbing only the dialog's blocking
`exec()` (same pattern as stubbing `QMessageBox.information` elsewhere this session) so a
headless run doesn't hang on a real modal: the dialog is offered exactly once per fresh load,
with the real 4 detected camera groups and their real guessed labels (`Google Pixel 9 Pro`,
`Ambarella`, `Camera`, `INS`); applying custom names in the dialog correctly propagates to
every clip sharing that camera; choosing Skip leaves the auto-detected labels untouched; and
triggering a second `_start_probe()` run (simulating the WAV-assign-dialog's own re-probe)
does **not** show the prompt again.

### Task 72 — efficiency pass: keyframe-skip for preview/playback extraction (done)

Prompted by the user noticing CPU pegged and GPU idle, and wanting the app to feel more
responsive without reaching for GPU acceleration yet. Recognised the same shape of bug task
48 already fixed for thumbnails, in two places that never got the same treatment:
`build_preview_cmd` (the before/after pane, and — critically — every tick of
`HybridPlaybackEngine`'s software-decode playback "slideshow") and `build_thumbnail_cmd` (the
merge tab's live-render preview). Both were decoding forward from the nearest keyframe to
reach an exact target frame, same as thumbnails did.

**Measured directly** on the same real 4K 10-bit HEVC clip used for task 48: single-frame
extraction via these commands took 1.9s at 0.2s into the file, up to 7.4s at 9.5s in (worse
deeper into the file — the same pathological pattern task 48 found); with `-skip_frame nokey`
added, every position dropped to a flat ~0.7s. Since `HybridPlaybackEngine` only keeps one
frame request in flight at a time, the *real* effective playback rate before this fix was
throttled to one frame every several seconds deep into a clip — nowhere near its intended
~300ms slideshow cadence — which plausibly explains a meaningful share of the sluggish/
CPU-heavy feel during software-decode playback.

**Verified end-to-end**: a real `HybridPlaybackEngine.play()` session against the same clip
delivered 8 real frames over 8 seconds of wall-clock time (roughly 1fps) — a large, measured
improvement over the pre-fix cost profile, achieved with a pure software change and zero GPU
risk. Full regression suite + full-tree compile stay green.

**Next candidate for the same line of thinking (not yet done, discussed with the user):**
extraction resolution is currently a fixed 854×480 regardless of the preview widget's actual
on-screen size — for a small preview pane this is decoding/scaling more than is displayed;
for a maximized window it could be less than ideal. Making it proportional to the widget's
real size would need the playback engine to learn the video view's current size (a new small
coupling point) — flagged as the natural next test rather than built speculatively.

### Task 73 — real hardware-decode crash investigation + auto-force safe decode (done)

Prompted directly by a real user report: "try playing `G:\Jottacloud\test.mov` (real 4K
10-bit HEVC, 50.7s) under both decode modes for 5 seconds, I anticipate a crash or an
unresponsive laptop." Investigated as a genuine stress test — real (non-offscreen) windowed
process, external OS-level timeout watcher (a .NET `Process` handle + `WaitForExit` + a
forced `Stop-Process` if it didn't return), so a true system hang could be *detected* rather
than mistaken for a slow-but-working test.

**Software decode**: clean — 3 frames in 5s, nothing in the crash log.

**Hardware decode, 5s test**: no visible crash, actually *faster* than software (20 frames in
5s) — but Windows' own crash reporting caught a real, deterministic fault at shutdown every
time: `Application Error` (Event ID 1000), faulting module `Qt6Core.dll`, exception
`0xC0000409` (Windows fail-fast/abort), **identical code offset both times** — this is
exactly what fires when Qt's own `qFatal()` aborts on "QThread: Destroyed while thread is
still running," matching the crash-log line captured at the same instant. The app's own
`ReviewTab.shutdown()`/`QtPlaybackEngine.shutdown()` can't `settle()` this thread the way it
does its own worker QThreads, because it isn't one — it's Qt Multimedia's own internal
FFmpeg-backend decode thread, which the app has no public handle to wait on.

**Hardware decode, sustained ~48s test**: no hang either — but frame delivery **silently
stopped after ~5 seconds** and never resumed for the remaining ~43s, with zero errors
reported. Worse than a crash in one sense: nothing signals that anything's wrong, the
picture just freezes.

**Hardware decode, live-monitored test (window actually foregrounded/active — the closest of
the three to real usage)**: this is the one that mattered. The outer watcher had to
force-kill the process after 90+ seconds of no response — and even after the kill signal,
the process's own output files kept being written for **another ~56 seconds** before it
actually exited, consistent with being stuck in an uninterruptible kernel/GPU-driver wait,
not just a slow script. Windows' own logs proved the mechanism directly:
- `Microsoft-Windows-Resource-Exhaustion-Detector` (System log, Event 2004): *"python.exe
  (8712) consumed 15,393,058,816 bytes"* — **~14.3 GB** of virtual memory for a single
  50-second clip.
- `chromoting` (Chrome Remote Desktop's own host process), Application log: *"Client
  disconnected: [user]/..."* at the moment the memory blew up, followed by two *"Access
  denied"* reconnection failures over the following minutes.

This directly confirmed the user's own hypothesis, with hard evidence rather than a guess:
hardware-decoding this content class doesn't just risk crashing the app — it can exhaust
system memory badly enough to take down the very remote-desktop connection being used to
watch it. Likely cause: Qt Multimedia's internal FFmpeg-backend decode pipeline for this
codec/format has no backpressure between decode and presentation — if the compositor/GPU
is contended (e.g. by a remote-desktop capture competing for the same resources), decoded
frames (≈25MB each at 4K 10-bit) can pile up in memory far faster than they're consumed.

**Fix**: rather than trying to patch a leak inside Qt's own compiled internals, added a
defensive content-based guard. New `review_playback.is_risky_hw_decode_profile(video_info)`
flags the exact confirmed-dangerous class — 4K+ resolution, 10-bit, HEVC/H.265 — matching
both this new memory-exhaustion finding and the earlier-documented DXGI/TDR crash on the
same content class. `ReviewTab._maybe_force_safe_decode()`, called once per freshly-loaded
master right after its spec is known, automatically swaps to `HybridPlaybackEngine`
(software decode) for matching content — **without persisting this to the user's own saved
preference** (`_apply_decode_mode` gained a `persist` flag specifically to keep this
per-file override from silently overwriting what the user actually asked for). The
"Software decode" checkbox reflects the override by getting checked *and disabled*, with a
tooltip explaining why, and both restore automatically — back to the user's own saved
preference, checkbox re-enabled — the moment a safer file is loaded.

**Verified**: pure-logic tests confirm the risk profile matches exactly the dangerous
combination (4K+/10-bit/HEVC, including the H265 alias and content above the 4K floor) and
excludes each near-miss (1080p 10-bit, 4K 8-bit, 4K H.264). End-to-end against the real
files: loading the confirmed-risky file forces `HybridPlaybackEngine` and disables the
checkbox without ever touching or altering the user's saved `review_software_decode`
setting; loading a genuinely safe file afterward (real 4K but 8-bit HEVC — an actual Pixel
phone clip, not synthetic) correctly lifts the override, restores the native engine, and
re-enables the checkbox; the plain manual toggle path (unaffected by the refactor) still
persists to settings exactly as before. Full regression suite + full-tree compile stay
green.

### Tasks 61-63 — camera-naming persistence, resizable columns, per-clip preview (done)

**Task 61 — remember camera naming across future folder loads.** `clip_model.assign_cameras`
now takes an optional `saved_labels` dict ({camera_id: label}) and prefers it over the
guessed default label whenever a clip doesn't already carry a session override. `Settings`
gained a new persisted `camera_labels` key. `merge_tab.py` passes
`self._settings.get("camera_labels", {})` into `assign_cameras` on every probe pass, and
`_maybe_prompt_camera_naming` now filters the naming dialog down to only cameras *not*
already in that saved map — if every detected camera is already known, the dialog is skipped
entirely. Every place a label gets set (the naming dialog, the inline group-header rename)
now also writes through to `Settings` via a new `_remember_camera_label` helper. Verified
end-to-end against the real 4-camera multicam folder: a fresh `MergeTab` (simulating a first
run) shows the naming dialog once and persists the confirmed labels to `settings.json`; a
second, completely fresh `MergeTab` instance (simulating an app restart) recognizes all four
cameras from the saved map and skips the dialog entirely, applying the remembered labels
automatically.

**Task 62 — resizable clip-table column headers.** Clip/Timestamp/Camera/Duration/WAV were
previously a mix of `Stretch` (blocks manual resize entirely) and `ResizeToContents` (also
blocks manual resize) — neither lets the user widen a column. Switched those five to
`Interactive` with sensible starting widths; Status stays `Stretch` to absorb leftover width;
tiny utility columns (`#`, ↑/↓, the hidden sync-detail columns) stay `ResizeToContents` since
dragging them wouldn't be useful. Verified programmatically that every intended column
reports `Interactive` and that a manual `resizeSection` call on Clip actually takes effect.

**Task 63 — per-clip low-res preview button.** A new column (`COL_PREVIEW`, inserted right
after Clip — every other column reference in the file uses the symbolic `COL_*` constants,
so renumbering them was a safe, mechanical change) holds a small "▶" button per clip row.
Clicking it extracts a short (≤5s) 160p-tall proxy starting at the clip's midpoint via a new
`build_clip_sample_cmd` (`core/ffmpeg_cmd.py`) on a background `_ClipSampleThread`, then plays
it in a small auto-looping `_ClipPreviewDialog` (`QMediaPlayer` + `QVideoWidget` +
`QAudioOutput`) — deliberately transcoding down to a tiny proxy file rather than asking the
player to decode+scale the real source, the same "use only the resources the task actually
needs" reasoning as the existing thumbnail/preview-frame extraction. Generated samples are
cached per clip path so repeat clicks don't re-extract.

Two real bugs turned up during verification, both instructive:
- A tuple-unpacking bug in the new code itself — `get_ffmpeg()` returns `(ffmpeg_path,
  ffprobe_path)`, and the new preview code had written `_, ff = get_ffmpeg()` (discarding
  ffmpeg, keeping ffprobe), so every real preview attempt silently ran ffprobe with
  ffmpeg-style arguments and failed instantly. Caught by isolating `_ClipSampleThread` in a
  standalone script and reading its actual stderr instead of trusting the UI-level symptom.
- A test-harness-only issue that looked identical to a hang at first: the failure above fed
  into the button's real error path, which pops a `QMessageBox.warning()` — a modal dialog
  that (like the already-documented camera-naming-dialog gotcha) blocks forever in a headless/
  offscreen test with no click ever arriving. Not a product bug; fixed by stubbing the warning
  dialog in the test harness, same as the established pattern for modal dialogs in automated
  tests.

Verified end-to-end against the real multicam folder after both fixes: clicking the button on
a real clip generates a real 284×160, 5.00s sample (confirmed via `ffprobe`), the button
re-enables correctly, the sample is cached, and a second click reuses the cache without
spawning a new extraction thread. The actual `QVideoWidget`/`QMediaPlayer` construction inside
the popup dialog was deliberately *not* exercised in this headless harness (this machine has a
confirmed history of Qt Multimedia issues under certain conditions — task 73) — the risk
profile here is very different (a tiny, few-second, always-software-decodable H.264 proxy vs.
the large 4K 10-bit HEVC content that actually triggered task 73's crash), so this is
considered low-risk but not headlessly provable; worth a real on-screen click-through if
anything looks off in practice.

### Tasks 66-71, 74 — Extract tab parity + output-format choice + dark-mode row fix (done)

**Tasks 66-68 — Spec/Camera/Duration columns.** The Spec column now shows codec, resolution,
fps (parsed from the manifest's `r_frame_rate`-style fraction string), bit depth, colour space,
rotation, and a VFR flag — previously just codec/resolution/bit-depth. The Camera column and
grouping now cross-reference the *current* Settings-persisted `camera_labels` map (task 61) via
a new `ClipEntry.camera_id` field (added to the manifest schema, backward-compatible — old
manifests without it just get `""` and fall back to the recorded `camera_label`, verified
against the real pre-existing `multicam test.mov` master), so a camera renamed once in the Merge
tab is recognised in Extract too, even retroactively for old masters merged with an unhelpful
generic label. A Duration column was added between Clip and Camera. Verified end-to-end against
the real master — correct durations, specs, and camera names for all four real cameras.

**Task 69 — per-clip preview button.** Same feature as the Merge tab's (task 63), adapted for a
clip that's still embedded in the master rather than a standalone file: a new
`build_preview_sample_cmd` (`core/extract.py`) seeks/maps straight into the master using the
same `RecoveryPlan` the real recovery path already computes, scales to 160p, and reuses the
Merge tab's `_ClipPreviewDialog` directly (safe to import cross-module — it's a fully generic
popup with no dependency on Merge-tab internals) rather than duplicating it. Verified end-to-end
against the real master: a real 284×160, 5.00s sample generated from inside the file, with the
same caching + headless-safe-testing approach as task 63.

**Task 70 — MOV vs. MP4 output format, with incompatible audio split out.** A new "Recover
video as" dropdown (Native / MOV / MP4), persisted to Settings. Native keeps each clip's own
original container (today's long-standing default); MOV/MP4 force every recovered clip into
that container. For MP4 specifically, a clip's camera audio is checked against a small
incompatible-codec list (`is_mp4_compatible_audio` — practically just uncompressed PCM variants,
since AAC/AC3/etc. are all fine in MP4) and, if incompatible, is decoded out to its own
`"<name> (camera audio).wav"` file instead of being force-muxed into a container that can't hold
it — the video's own `-c copy` stream copy is untouched either way. The WAV-backup separation
this asked for ("ALAC reverted back to WAV") already existed since Phase 4 and needed no change.
Verified: real MP4 extraction of an actual clip from the real master (all its clips happen to
use AAC camera audio, so the split path wasn't naturally exercised end-to-end) plus direct
unit verification of the codec-compatibility check and both command builders. Changing the
dropdown after a master's already loaded re-populates the tree so the "Recovers as" preview and
future extraction stay in sync.

**Task 71 — "Create folder" button.** A small `_CreateFolderDialog` suggests a name
(`"<master stem> - recovered clips"`) and location (the master's own directory), editable
before creating; Browse lets the user pick a different parent location entirely. Verified: real
folder actually created on disk with the correct suggested name/path derived from a real master
file.

**Task 74 — dark-mode alternating-row banding fix.** A real bug, not a new regression: found by
tracing the app's shared QSS (`theme.py`) and discovering its `alternate-background-color` rule
was scoped only to `QTableWidget` — but both the Merge tab's clip table and the Extract tab's
clip tree are `QTreeWidget`s, which never matched that rule and silently fell back to Qt's
built-in default alternate-row colour (a light grey), clashing badly with dark mode and matching
exactly what the user's screenshots showed. Fixed by extending every `QTableWidget` rule in that
block to also match `QTreeWidget`. Verified by rendering a real themed `QTreeWidget` offscreen
and sampling actual pixel colours row-by-row in both dark mode (correctly alternating `#0e0a06`/
`#130a04`) and light mode (correctly alternating `#efe7d9`/`#e9dfcc`) — confirmed the fix, not
just the CSS text.

### Task 75 — real rotation-loss bug found in production + "Optimize baseline for delivery" (done)

**The bug, found from real user output.** Given a real master built with "one track per clip
(bit-exact)" active, a side-by-side ffprobe/framemd5/raw-elementary-stream investigation of the
recovered clips against their originals found: clips that needed transcoding (odd-spec, sitting
on their own dedicated archival track) recovered genuinely bit-exact — video and audio hashes
identical. But clips that already matched the chosen baseline spec (`conform_status: "ok"`, no
archival track at all — recovered via a seek into the shared baseline video stream) were **not**
bit-exact when they carried a non-zero rotation tag (270°/180°, common on action cameras). Root
cause: `probe.apply_conformance` never checked rotation when deciding whether a clip could
stream-copy into the baseline; ffmpeg's concat demuxer, when gluing that clip's data in next to
others with a different (usually zero) rotation, only carries the *first* segment's Display
Matrix side-data for the whole resulting stream — so the rotated clip silently lost its
orientation on recovery. "One track per clip" was never scoped to cover this (it only governs
archival-track grouping for clips that already needed one) — this was a real, separate,
pre-existing gap, confirmed with real ffprobe/framemd5 evidence, not a regression from anything
built this session.

**The fix the user chose, after a design discussion**, was broader than patching
`apply_conformance`: a new **"Optimize baseline for delivery"** checkbox (Merge tab, ARCHIVAL &
DELIVERY section) that, when active, forces *every* clip to transcode regardless of whether it
matches the baseline — eliminating the shared-baseline-stream-copy path (and this whole class of
bug) entirely, not just the rotation case. Depends on **Archival master** + **One track per
clip**, since once nothing stream-copies into the baseline, every original still needs a safe
home on its own track. All three checkboxes (plus the new **Verify MD5 recovery**, see below)
default checked, so the dependency is discoverable by unchecking one and watching what fades —
disabling (not hiding) a dependent checkbox preserves its checked state underneath, so
re-enabling a prerequisite restores exactly what was chosen before.

**Quality presets.** Since the baseline no longer needs to match whatever spec the dominant
camera happened to shoot in, "Optimize baseline for delivery" also exposes a quality target —
four named presets (Archival/Mezzanine, Master Quality, YouTube/Streaming ⭐ recommended,
Social/Compact), each showing both the x264 and x265 CRF number (x265 needs a higher number than
x264 for equivalent visual quality — it's simply a more efficient codec) plus a plain-language
description of what it's for. `ConformSpec` gained a `quality` field (default 18, unchanged
behaviour when this mode is off); `_video_encoder_args`/`hw_video_encoder_args` both now take
that value instead of a hardcoded `18` for CRF/`-global_quality`/`-cq`/`-qp_i` across every
codec path (software and all three GPU vendors).

**Verify MD5 recovery** (`core/verify.py`, new module): after the merge finishes, extract every
clip straight back out of the just-built master and MD5-compare it against its original —
video, camera audio, and WAV backup — writing a human-readable `<master-stem>.verify.log` with
every hash. Two real, non-obvious things had to be right for this to mean anything:
- **Video** is compared as a raw elementary stream (`-bsf:v h264_mp4toannexb`/`hevc_mp4toannexb`,
  no container), never whole-file bytes — a genuinely bit-exact recovered clip still lands in a
  different container (different moov atom, duration rounding) than the original, which was
  exactly the false alarm that kicked off this whole investigation (task 74/75's early
  ffprobe-based frame-count read showing inflated `nb_frames`, resolved by force-decoding with
  `-count_frames`/`framemd5` to confirm the real content was untouched).
- **Audio** decode-compares to a fixed PCM spec — and this one was caught directly while
  building the feature: the first real test reported every clip's camera audio as a MISMATCH,
  which turned out to be `-f wav` writing a `LIST`/`INFO` metadata chunk (an `ISFT`/`INAM` tag)
  that differed between the two extractions despite the actual samples being identical. Fixed by
  switching to headerless raw PCM (`-f s16le`) — confirmed byte-identical immediately after.
- A clip that already matches its own MP4/MOV compares to the *whole* original file; a clip
  recovered from a shared master seeks/maps the same window `core.extract.build_recovery_plan`
  already computes for real recovery, so verification and recovery can never quietly diverge.

On a mismatch, a `QMessageBox.warning` fires immediately (impossible to miss, not buried in a
log), and the completion dialog's message includes the pass/fail summary either way.

**Closing the loop into Extract**: a master with a sibling `.verify.log` shows a banner right at
the top of the Extract tab — green "✓ Verified when created — all N clips confirmed
byte-identical" or an amber warning naming exactly which clips didn't pass — before the user even
starts picking what to recover. The point isn't a new feature so much as a promise the app keeps
visibly: what you archived is what you get back, and the app tells you so up front rather than
leaving you to wonder.

**Verified end-to-end against real footage, twice** (once finding the audio-metadata bug, once
confirming the fix): a real GPU-encoded merge of real 4K clips with Optimize baseline + Verify
MD5 both active reported `1/1` (and separately `2/2`) clips byte-identical for video and camera
audio, and loading that same master back into Extract showed the green trust banner immediately.
Full existing test suite (manifest, extract, ffmpeg_cmd, gpu_encode, camera_id, baseline, theme)
stays green throughout.

### Task 76 — real user-found verification false-positive (done)

The user ran their own real test (four clips, `01 input` → merge → `02 merged` → Extract →
`03 recovered`) and found `Verify MD5 recovery` reporting `PXL_20260703_120349515.LS`'s camera
audio as a mismatch. Investigated by hashing the raw PCM of the user's own already-recovered file
(`03 recovered`) against the original (`01 input`) with no seek/duration limit at all on either
side — byte-identical, proving the actual recovered audio was correct all along; the bug was in
the verification check itself, not the recovery.

Root cause: for a bit-exact archival track (a lone clip — the whole track is nothing but this
clip), `_verify_one_clip` cut the recovered-side audio extraction to `plan.video_duration`
(the video stream's own declared length) instead of reading it to its natural end. A track's
audio and video rarely declare exactly the same duration down to the millisecond (this clip's
video declared 18.108911s, its audio's own natural length was 18.092604s) — AAC's fixed-size
frame quantization means a `-t` cutoff pinned to the *video's* duration can land a few
milliseconds short of or past the audio track's real boundary, changing the hash despite the
underlying samples being identical. Fixed by only applying that duration cutoff when the
archival track is genuinely *shared* between multiple clips (where isolating this clip's own
window really is necessary) — a lone clip's track is read to EOF on both sides, matching how the
untruncated original file was always read. WAV backup extraction was already correct as-is: it
always reads from the baseline's own shared timeline, where a cutoff is never optional.

Verified by re-running verification directly against the user's real master with the fix
applied: the previously-failing clip's camera audio now matches exactly (all 4/4 clips pass,
including the other three that already passed before). Existing test suite stays green.

### Task 77 — adaptive verification + self-healing rotation/geo/metadata recovery (done)

Scope change mid-testing: rather than just running the planned archival/delivery test matrix,
made the MD5 checker itself adaptive — auto-retrying a mismatch with an alternate extraction
before concluding it's real (catching exactly the class of false positive task 76 found by
hand), and extended it to check rotation and key provenance metadata (GPS/location, creation
time, device make/model), not just audio/video payload.

**Real, non-obvious finding along the way**: GPS/creation-time/device tags live at the
whole-FILE level in MOV/MP4, not per-stream. Copying a clip's video/audio out of a shared
master does not bring them along — confirmed directly: a clip's video and audio hashed
perfectly while every metadata tag came back missing. `probe.py` now captures these tags
(`StreamInfo.format_tags`, keyed by `KEY_METADATA_TAGS` — deliberately the RAW keys a camera
actually wrote, not a renamed/derived set, since guessing a single naming convention silently
fails for any camera using a different one); `ClipEntry.metadata_tags` carries them into the
manifest; `core.extract.recover_metadata_args` replays them verbatim onto the recovered file
via `-metadata`, used by both `ExtractWorker`'s real recovery and the verification's own
Metadata check (which now performs an actual recovery-with-reinjection rather than probing the
master directly, testing what a user's Extract click really produces).

Two further real ffmpeg quirks found and worked around, both confirmed by isolating a minimal
reproduction before touching the fix:
- ffmpeg's MOV/MP4 muxer **silently drops any metadata key outside its own built-in
  whitelist** — a vendor tag like `com.android.model` vanished with no warning at all.
  `-movflags use_metadata_tags` tells the muxer to keep arbitrary tags instead of filtering
  them.
- Two further cosmetic-only reformattings that would otherwise look like false mismatches:
  the muxer can zero-pad a GPS string's longitude ("-3.3728/" → "-003.3728/", same coordinate)
  and can duplicate a tag into "X;X" when the same value reaches it through two of its own
  internal paths (confirmed identical both times). `core.verify.tags_equal` tolerates both
  before ever reporting a real mismatch.

Verified end-to-end against a real merge: all 4/4 clips now match on video, audio, rotation,
*and* every GPS/device/creation-time tag the originals carried. Existing test suite stays
green throughout (probe_tracks, manifest, extract, ffmpeg_cmd, gpu_encode, camera_id,
baseline, theme).

### Task 78 — full archival/delivery MD5 matrix + shared-track offset & WAV verification fixes (in progress)

Ran the planned matrix (4 configs — baseline-only / archival-shared / archival-per-clip /
optimize-delivery — × a small 4-clip folder, then a large 5-clip folder that mixes h264+hevc
4K plus one conforming 1080p). Small folder: the three archival configs recovered 4/4 clips
byte-identical; baseline-only's mismatches are its known limitation (transcoded clips have no
archival track to fall back on). The **large folder exposed two bugs the small folder couldn't
reach** — it was the first to have a spec group with 3+ clips *and* WAV-paired clips.

Test-harness robustness fixes needed to even complete the matrix unattended:
- Each config now runs as an ISOLATED subprocess (`md5_matrix_test.py --single`) with an outer
  timeout, because a persistent QApplication hangs after each merge's real work finishes (the
  work is always correct on disk — only the process's own Qt teardown wedges). A fresh
  subprocess per test means the hang just dies with it and the next test proceeds.
- A headless dialog stub tried to `print()` a "⚠" glyph that Windows' cp1252 console can't
  encode, raising `UnicodeEncodeError` out of a Qt slot and killing the process mid-run — fixed
  by forcing UTF-8 stdout/stderr and making the stub print exception-safe.
- A transcoded clip lands in the master under the BASELINE's target codec, not its own — the
  verification's elementary-stream extraction must probe the recovered stream's actual codec
  (`verify.probe_video_codec`) or the annexb bitstream filter crashes on a codec mismatch.

**Bug A — first hypothesis (offset drift) was WRONG; real cause is concat is not byte-exact.**
Initially theorised the shared-track mismatches were `in_track_start` drift from summed probed
durations. Investigated directly on the retained failing master and DISPROVED it: the shared
h264 track's frames add up exactly (2904 = 1959 + 1958... i.e. 1959 + 945), total duration is
the exact sum, and 131634's byte region equals its original size exactly — the offsets were
already correct. The real cause: **going through the concat demuxer + MOV remux does not
preserve the elementary stream byte-for-byte.** The first clip's video came back 29,701 bytes
smaller than its original ES — stripped SEI/AUD-type NAL units that carry no pixel data. Proof
it's metadata-only: the DECODED pixels are identical (rawvideo MD5 matches for the first 60
frames), and the first clip's DECODED camera-audio PCM is identical too. So a concatenated
archival track is **decode-lossless but not byte-identical**. (The offset work — 
`probe_keyframe_times`/`measure_in_track_offsets`/`probe_video_stream_duration` — is kept as a
harmless robustness improvement, but it is NOT what fixes these mismatches, because there was no
drift to fix.)

Audio has an additional wrinkle beyond metadata: a NON-FIRST clip on a concat track is decoded
after an `-ss` seek into the middle of the AAC stream, so its priming samples are wrong and the
decoded PCM genuinely differs (131634's recovered PCM ≠ original). This is exactly the "concat
demuxer perturbs AAC priming" caveat the build code already calls out for lone clips — it bites
every non-first clip in a shared track.

**What IS byte/sample-exact, confirmed:** a clip on its OWN un-concatenated track — i.e.
per-clip archival mode (every odd-spec clip gets its own track, muxed from the original
directly), or a spec group that happens to have exactly one clip (small folder: all 4 clips
were singleton groups, which is the only reason it passed 4/4). A CONFORMING ("ok") clip is
only ever in the baseline concat, so it is decode-lossless but never byte-exact — this is
inherent to the design, not a patchable offset bug.

**Bug B — WAV backup verification compared the wrong reference (fixed).** The master's WAV/ALAC
track is deliberately SYNC-ALIGNED to the video at build time (`clip.wav_flags` applies the
constant offset — `-ss` trims the WAV head, `-itsoffset` delays it). It is lossless but NOT a
verbatim byte-copy of the raw original .wav; for a trimmed clip the discarded head is gone by
design, so byte-exact recovery of the raw file is impossible. The check was comparing a
recovered window against the untouched original, so it "failed" even where video+audio were
fine. Reworked to compare against the original with the SAME sync transform applied (mirror the
trim with `-ss` on the source), and to report the delay case honestly rather than a false FAIL.

**Resolution (user chose all three directions; results):**
- (3) Byte-exact concat — CONFIRMED INFEASIBLE. Tested directly: the concat demuxer strips the
  SEI/AUD NALs on READ, identically across mov / mkv / `+bitexact` output (same stripped hash
  every time, differing from a plain-copy reference). Byte-exactness is only possible by NOT
  routing through the concat demuxer — i.e. per-clip / lone tracks. This is a build-time loss;
  no extractor can recover bytes discarded upstream.
- (2) Per-clip archival = the byte/sample-exact guarantee. Un-concatenated tracks (per-clip
  mode, or singleton spec groups — why the small folder passed 4/4) carry the original stream
  verbatim; the final mux stream-copies it without the concat demuxer, so it stays byte-exact.
- (1) Adaptive decode-lossless verification — IMPLEMENTED and validated against the retained
  large_archival_shared master (via tests/_verify_existing_master.py, no re-merge). On a raw-ES
  mismatch the check now falls back to hashing DECODED content: `build_decoded_video_md5_cmd` /
  `build_decoded_audio_md5_cmd` / `decoded_md5` use ffmpeg's own `md5` muxer (no giant temp
  files). Video: all concat-sourced clips now PASS as "not byte-identical, but DECODES
  identically (concat drops SEI/AUD metadata NALs)". Audio: compared over an INTERIOR window (a
  300 ms guard at each end) so AAC priming at the start and the audio/video boundary at the end
  don't trip a false mismatch — first-clip and conforming clips PASS "decodes identically across
  the interior". Result went from 0/5 (all "unexpected") to 4/5 with accurate, honest
  diagnoses.

Known remaining limitation (honestly reported, not masked): a NON-FIRST clip's camera audio on
a SHARED track can't be exactly aligned for verification, because its audio is sought by a
video-based offset while the true audio boundary sits at the cumulative AUDIO durations (the two
drift apart), on top of the AAC-priming seam. The samples are provably intact (they match when
read to their natural end), and the video decodes identically — so the clip is present and
playable; the diagnosis says exactly this and points to One-track-per-clip archival for
verifiable byte/sample-exact audio. Fully fixing it would mean seeking audio by an
audio-boundary offset (a recovery-architecture change), deferred as low-value since per-clip
mode already guarantees it. Regression suite (manifest, extract, probe_tracks, ffmpeg_cmd) green
throughout.

### Task 79 — WAV-backup ALAC corruption: mismatched bit depth across clips (fixed)

Follow-up investigation into task 78's one remaining WAV-backup mismatch (clip 130115 in the
large-folder per-clip archival matrix run). Initial theory was another window-alignment issue
like the shared-track audio case above — DISPROVED by direct testing. The real finding, root-caused
on the retained real master:

Decoding the merged WAV-backup ALAC track threw hundreds of `Error submitting packet to decoder:
Invalid data found` / `invalid element channel count` / `invalid zero block size` errors — a
genuine stream corruption, not a math/offset problem. Camera audio (AAC) on the same master
decoded perfectly clean, isolating the fault to the ALAC path specifically. Bisected with a
minimal controlled reproduction (encode two synthetic sources to ALAC, concat, decode):

- A real WAV backup (24-in-32-bit source) encodes via ffmpeg's ALAC encoder at **24-bit**.
- The SILENCE filler used for a clip with no WAV backup (`anullsrc`, no format specified) encodes
  at **16-bit** — ffmpeg's ALAC encoder auto-picks a bit depth from whatever it's fed, and an
  unspecified `anullsrc` defaults to 16-bit.
- `core/ffmpeg_cmd.py`'s `build_mux_cmd_plan` emits a UNIFORM audio-track layout across every
  clip in a merge (so the final concat's stream count stays consistent — a real, working design)
  — but its ALAC branch (`elif codec == "alac": cmd += [f"-c:a:{i}", "alac"]`) never forced a
  sample format, so a clip's WAV slot could land at either bit depth depending only on whether
  THAT clip happened to have a real backup or fell back to silence.
- Concatenating ALAC segments that declare DIFFERENT bit depths corrupts the stream at the seam —
  confirmed directly: forcing `-sample_fmt s32p` on both a synthetic silence-ALAC and a real-WAV-ALAC
  segment made an otherwise-corrupting concat decode perfectly clean.

**Fix**: `build_mux_cmd_plan`'s ALAC branch now always appends `-sample_fmt:a:{i} s32p` alongside
`-c:a:{i} alac`, for every ALAC-coded slot — real backup or silence filler alike. s32p is a safe
superset of any real source's precision, and forcing it uniformly guarantees every clip's ALAC
segment in a merge declares identical parameters, so the concatenated WAV-backup track always
decodes cleanly regardless of which clips have a real backup and which fall back to silence. New
regression test (`test_ffmpeg_cmd.py::test_plan_alac_sample_format_is_forced_and_matches_real_and_silent_fills`)
locks in that both paths emit the identical forced format.

**Confirmed on a real merge**: re-ran the exact large-folder per-clip archival config that
previously produced the corrupted clip. Decoding the WAV-backup ALAC track now throws zero
errors (was hundreds), and it reports a uniform 24-bit format throughout. The corruption bug is
resolved.

**A second, separate issue surfaced once decoding was clean**: clip 130115's WAV backup still
MD5-mismatched. Root-caused as a genuinely different problem — confirmed directly by
round-tripping the original through the identical ALAC encoder, which reproduced it exactly
(ruling out any encode lossiness), while the recovered window's content still didn't match at
all, even with an interior guard applied. This is a POSITION mismatch: the WAV backup always
lives on the shared baseline track (no per-clip escape the way camera audio has), and its
recovery window is seeked using the *video's* cumulative baseline offset — nothing enforces that
a clip's embedded WAV segment runs exactly as long as its video, so that offset can drift from
the WAV track's own true position. This is the same *class* of limitation already documented
above for shared-track camera audio, just now confirmed to also affect the WAV backup — and,
because WAV has no per-clip mode to fall back on, more consequential there. The WAV-backup
mismatch diagnosis is now honest about this (distinguishing "stream corruption" — fixed — from
"the window landed on the wrong samples" — a known, currently-unavoidable limitation for WAV
backup specifically) rather than a generic "worth a closer look". A real fix would mean seeking
the WAV by its own audio-boundary offset instead of the video's (a recovery-architecture change,
same deferred scope as the camera-audio case) — left as a documented, understood limitation
rather than pursued further this session.

## v1.4 progress notes

- **Stability pass (done)**: fixed the rare "app closed itself" bug — `_on_finished()` in
  `merge_tab.py`/`whatsapp_tab.py` dropped the last reference to a possibly-still-running
  QThread before showing the completion dialog; destroying a live QThread hard-aborts the
  process. All QThread sites now `settle()` (wait) before the ref is dropped, and every tab
  gets a `shutdown()` called from `MainWindow.closeEvent`. Added `src/crash_log.py`
  (faulthandler + excepthooks + Qt message handler → `crash.log` beside `settings.json`,
  tagging any "QThread" message `[THREAD-LIFETIME]`) so a recurrence would leave evidence.
- **Theme discipline pass (done)**: `warn` no longer equals `accent` (a caution used to read
  as the brand colour); muted-text contrast raised; every hardcoded hex literal outside
  `theme.py`/`about_tab.py` (brand colours) now routes through `theme.active_palette()`.
  `tests/test_theme.py` guards both regressions.
- **Playback spike (done)** — `tools/spike_playback.py <master.mov>` tests whether
  QMediaPlayer + QVideoSink (the render path the Review tab uses, not QVideoWidget) can
  open a real 4K 10-bit HEVC master, switch audio tracks, and play a slow-motion segment.
  Run against a real ~46-minute master (HEVC Main10 yuv420p10le, 3 audio tracks: AAC/ALAC/AAC):
  **PASS — clean playback on every track, including the slow-motion chapters, zero
  errors.** The user's report of a local media player stalling on a static frame during
  slow-mo did not reproduce in QtMultimedia — that appears specific to the external player,
  not the file. **Decision: `QtPlaybackEngine` only; the `HybridPlaybackEngine` fallback in
  the plan is not needed for this codec/track combination** (kept as a documented contingency
  if a different backend/OS proves less capable — re-run the spike before assuming it still
  holds on Linux/Steam Deck).
  Two things confirmed by the spike that shape the Review tab design:
  - `QVideoFrame.pixelFormat()` is genuinely 10-bit (`Format_P010`), but
    `frame.toImage()` silently converts to 8-bit `Format_RGB32`. Playback-time scopes are
    therefore approximate; exact 10-bit scopes and all snapshots must go through ffmpeg
    frame extraction (`rgb48le` / 16-bit PNG), never `QVideoFrame.toImage()`.
  - Qt exposes no useful per-track metadata (title/language all empty) — track labels
    ("Camera mic", "WAV backup", "Mix") must come from `probe.py`, not from Qt.
- **Review tab UI assembly (done)** — `review_tab.py` (`ReviewSession` position authority +
  `ReviewTab`), `review_workers.py` (5 background workers), and the widgets in `widgets/`
  (`video_view.py`, `jog_wheel.py`, `scopes_panel.py`, `audio_lanes.py`, `trackbar.py`).
  Verified end to end against the real pool-day master, including several rounds of
  real bugs the verification caught before they could ship:
  - `core.spectrogram.to_rgb()` returned `(time, frequency, 3)` — sideways for display;
    now `(frequency, time, 3)` with high frequency at the top, matching how a spectrogram
    is conventionally read.
  - `core.scopes.waveform_rgb()`/the parade tinting normalized linearly against the single
    largest bin — a real frame's one big uniform region (sky, wall, out-of-focus background)
    would swamp everything else, making the rest of the waveform invisible. Fixed with a
    sqrt-compressed normalization.
  - `review_workers._run_cancelable()` treated empty `stdout` as failure — correct for
    frame extraction (data comes back via stdout) but wrong for the snapshot command, which
    writes to a file and legitimately returns nothing on stdout. Every snapshot was failing.
  - **A genuine race condition**: `ReviewTab._apply_tick_set()` assigns the tracked worker
    reference before calling `.start()`, so `cancel()` could fire before the QThread's `run()`
    reached its `Popen()` call. `self._cancelled` was set but never re-checked before
    spawning the process, so a "cancelled" full-file mix render ran to completion anyway.
    Fixed by checking `_cancelled` at the top of every cancelable worker's `run()` (and
    inside the shared `_run_cancelable()` helper) before spawning anything.
  - `QtPlaybackEngine`'s post-load "prime" pulse (see above) used a delayed `singleShot`
    auto-pause; a real `seek()` arriving during that window could be immediately followed
    by the prime's own pause, cutting off the frame the seek was trying to show. Fixed by
    having `seek()`/`play()`/`pause()`/`load()` end an in-progress prime immediately instead
    of leaving the timer to fire later.
  - `ReviewTab.shutdown()` used to unconditionally clear its tracked-worker list even when
    a worker's `settle()` call timed out — silently reintroducing the exact "drop the
    reference to a still-running QThread" crash the Phase-1 stability pass fixed elsewhere.
    Fixed: `thread_utils.settle()` now returns whether the thread actually finished, and
    `shutdown()` only drops the workers that did.
  **Known real-world characteristic, not a bug**: rendering a tick-set mix for the *entire*
  length of a very long master (this test master is ~46 minutes) on slow/cloud-synced
  storage can take several minutes — well past "brief". The UI already shows "Rendering
  mix…" throughout and the render is fully cancelable, so this is a UX/architecture note
  for later (e.g. a windowed/incremental mix instead of always rendering the whole file)
  rather than something fixed in v1.4.
- **Integration + housekeeping (done)** — the Review tab is wired into `main.py` (inserted
  after "WhatsApp clip"); the merge-complete dialog's new "Review" button loads the fresh
  master and switches to it; a "Load master…" browse button and `.mov`/`.mp4` drag-and-drop
  cover the rest. Version bumped to 1.4.0 everywhere it's hardcoded (`main.py`,
  `merge_tab.py`, `about_tab.py`, `build.bat`, `build_linux.sh`, the `.spec` header).
  Built via `pyinstaller LunaVaultFuseBox.spec` + the same runtime-data copy `build.bat`
  does: succeeded, `Qt6Multimedia.dll` and — critically — the `ffmpegmediaplugin.dll`
  backend plugin are both present in `dist/LunaVaultFuseBox/_internal/PySide6/`, the frozen
  exe launches cleanly (version 1.4.0 in the title bar), `crash.log` shows a clean session
  start with no errors, and it shuts down without incident.
- **Field crash after shipping v1.4 (fixed)** — pressing play in the Review tab crashed with
  `numpy._core._exceptions._ArrayMemoryError: Unable to allocate 63.3 MiB for an array with
  shape (8294400,)`. Root cause: the playback-time "approximate scope" path
  (`ReviewTab._update_approx_scope` → `core.scopes.histogram_rgb`) processed the *entire*
  3840×2160 frame at full precision roughly five times a second — each update briefly
  allocating several ~66 MB `int64` arrays (r/g/b/luma) from data that only ever needed
  8-bit precision, on top of the QImage→numpy conversion's own ~50 MB of copying. Fixed at
  both ends:
  - `core/scopes.py` gained `_downsample_for_scope()` — stride-slicing (a free view, not a
    copy) caps every scope function (`histogram_rgb`, `waveform_parade`, and transitively
    `waveform_rgb`) at ~250k pixels regardless of source resolution, and dropped the
    needless `int64` upcasts (`histogram_rgb`'s r/g/b now stay at the array's own dtype;
    `rescale_to_bit_depth` now returns `uint16`, not `int32`, and downsamples before its
    float conversion rather than after).
  - `review_tab.py`'s `_update_approx_scope` now shrinks the frame via `QImage.scaled()`
    (cheap, native, GPU/SIMD-backed) to a max 640px dimension *before* touching numpy at
    all, so the playback-time path never even constructs a full-resolution array.
  - New tests in `tests/test_scopes.py` exercise a real 3840×2160-sized array through every
    affected function to guard against this regressing.
  **A second, separate, more fundamental issue surfaced while verifying the fix**: an
  isolated test with *zero* application code in the frame-delivery path (bare
  `QMediaPlayer` open + play, no scopes, no histogram) reproduced a ~45-second full-process
  stall during sustained playback of the same 4K 10-bit HEVC master, with the same
  `hardware accelerator failed to decode picture` / `Failed to add bitstream or slice
  control buffer` messages seen in the field crash log. This points to the GPU's D3D11VA
  hardware video decoder itself struggling to sustain 4K 10-bit HEVC decode on that
  machine — independent of anything in this codebase.

  **Confirmed persistent after a clean reboot** — same failure, same `crash.log` signature,
  now with the actual smoking gun: `Failed to create 2D texture: COM error 0x887a0005: The
  GPU device instance has been suspended.` `0x887A0005` is DXGI's device-removed error —
  Windows' TDR (Timeout Detection and Recovery) forcibly resetting the GPU driver because it
  stopped responding. A genuine hardware/driver ceiling on that machine for 4K 10-bit HEVC
  hardware decode, not session-accumulated state and not fixable in this codebase's existing
  playback path. Also explains the "white/blank window" the user saw: once the GPU device
  resets, every surface the app was rendering through goes invalid until the app recovers or
  Windows shows it as unresponsive.

  **Fixed** by building the `HybridPlaybackEngine` fallback designed in Phase 3 but not
  built at the time (the spike passed on the development machine). It never touches the GPU
  for video decode at all: video comes from periodic low-resolution ffmpeg frame extraction
  (reusing `ffmpeg_runner.FramePreviewWorker`, the same worker the WhatsApp tab's before/
  after preview already uses) polled roughly every 300ms — a slideshow rather than smooth
  30fps, the deliberate trade for a path that can't crash a GPU driver it never calls into.
  Audio is unaffected by the GPU issue (never hardware-accelerated) and stays real-time
  throughout, playing a rendered file through an audio-only `QMediaPlayer` — this is now the
  *only* way this engine plays audio, even for a single ticked track (`set_audio_single`
  always returns `False`, since there's no "master" player to natively switch a track on;
  `ReviewTab._apply_tick_set()` was fixed to actually check that return value instead of
  ignoring it, falling through to a render for either engine when a native switch isn't
  available). Exact per-frame precision (paused scopes, snapshots) doesn't go through either
  engine — `ReviewTab` already gets those from `FrameFetchWorker` directly.

  Selected via a new "Software decode" checkbox in the Review tab's header (persisted to
  settings as `review_software_decode`, read once at `ReviewTab.__init__` — takes effect
  after an app restart, not a live hot-swap, since a GPU device reset likely leaves that
  session's rendering pipeline unrecoverable regardless).

  Verified against the real master: correct duration/frame delivery/audio playback, and a
  real position-sync bug the verification caught — `QMediaPlayer.setPosition()` isn't
  guaranteed to have taken effect before the source finishes loading, so a stale near-zero
  position from the audio player's own `positionChanged` signal could silently overwrite a
  correct just-seeked position right as playback started. Fixed by making the engine's own
  wall-clock timing authoritative (matching how `QtPlaybackEngine`'s mix-player sync already
  worked) and treating the audio player's position reports as a drift-correction signal
  rather than ground truth, plus re-asserting the position immediately before `play()` as a
  second line of defence against the same race.

### Task 80 — Memories shelf: dark-mode readability + per-collection controls + storage decision aid (done)

  Three things, from a real screenshot of the friendly Memories tab in dark mode.

  **Dark-mode contrast bug.** Each memory card's text lines (title / date / "kept") were
  rendering as harsh dark boxes over the lighter card. Root cause: the global
  `QWidget { background:<bg> }` QSS rule cascades to every child `QLabel`, so the labels
  painted the *window* background (`#1B1610`, darker than the card's `surface2 #281F14`) on
  top of the card — reading as heavy black bars and hurting legibility. Fixed by giving the
  tile's labels `background:transparent` (they now sit on the card surface); no palette
  change, so light mode and Legacy mode are unaffected. `_Tile` in `library_view.py`.

  **Per-collection controls.** Home tiles gained a "⋯" menu (`_Tile.menu_requested` →
  `HomeView._tile_menu`) with Rename / Move left / Move right / Remove. Backed by new pure
  `core/catalog.py` ops: `Catalog.move` (clamped reorder) and `.rename`, plus disk-level
  `rename_collection` (updates the folder's `collection.json` source-of-truth when reachable
  *and* the cache), `reorder`, `remove_from_library` (drops the catalog entry only — every
  file untouched), and `delete_collection_folder` (`shutil.rmtree` + forget). Remove is
  safe-by-default: a first dialog offers "Remove from library" vs "Delete files…", and the
  destructive path requires a second confirmation naming the exact folder path.

  **Storage decision aid (Task 15).** The bare "Make fully portable" button became a
  "Storage options…" button opening `storage_compare.StorageCompareDialog` — a themed
  side-by-side comparison of the embedded archival master (default: one file that both
  uploads to YouTube and archives long-term) vs also writing separate clip files + album
  page (opt-in, additive, uses more space). Opting in routes through the existing
  `make_portable_requested` → `core/portable.make_portable`, so nothing about the default
  behaviour changed; the folder layer is purely additional. The comparison widget is themed
  entirely from `active_palette()` (no literal colours, passes `test_theme`).

  **Classic Merge tab parity.** Exposed the Task 13 clean-re-encode playback fix as a
  "Compatible playback master" checkbox (`merge_tab._compat_baseline_check`), off by default
  there since power users often want an exact stream copy. The Add flow still forces it on.

  Tests: `test_catalog.py` (+6 for move/rename/remove/delete/reorder), `test_storage_compare.py`
  (new), `test_output_suggest.py` (+1 for the compat checkbox). 29 test files green.

### Task 81 — theme-repaint fix for the shelf, clip-preview black-window fix, Developer panel (done)

  **Shelf didn't follow the theme.** `LibraryView` was the only view never wired to
  `ThemeController.changed`, so its tiles (which bake palette colours in at build time) kept
  whatever palette they were first drawn under — switching to dark left white light-mode
  cards on the dark background. Fixed by connecting `LibraryView` to `changed` and giving
  Home/Collection/Memory `_restyle()` methods that repaint their palette-coloured labels and
  rebuild the tiles. Also lifted `FRIENDLY_DARK`'s card surfaces (`surface`/`surface2`/
  `input_dk`/`border`) clear of the page background so photos sit on a comfortable frame, and
  made the per-card "⋮" menu always-visible instead of hover-only.

  **Clip preview black window.** `_ClipPreviewDialog` passed a raw path to
  `QMediaPlayer.setSource`, which parses `C:\…` as a URL with scheme `c` → media never loads.
  Fixed with `QUrl.fromLocalFile()`, plus an on-screen message on `errorOccurred`/
  `InvalidMedia` so a genuine failure isn't a silent black window.

  **Developer panel (hidden).** New `dev_panel.py`: the logo triple-click now reveals a
  "⚙ Developer" button beside the Legacy toggle (`main._reveal_hidden_controls`) that opens a
  modeless `DeveloperOptionsDialog` of experimental, independently-switchable, default-off
  toggles. First set tunes the per-clip preview via new kwargs on
  `build_clip_sample_cmd(hw_decode, gpu_vendor, fast)`: GPU encode (resolved through
  `gpu_encode.detect_best_hw`, auto-falls back to libx264 when no GPU encoder works), GPU
  `-hwaccel auto` decode, and an ultrafast 2s sample. `merge_tab._preview_accel()` reads the
  settings and `_accel_sig()` keys the preview cache per option-set so a toggle change
  regenerates rather than reuses.

  Tests: `test_library_view.py` (+1 restyle), `test_clip_preview.py` (new),
  `test_ffmpeg_cmd.py` (+4 sample-cmd options), `test_dev_panel.py` (new). 31 test files green.

### Task 82 — Developer panel expansion: sections + choices + Review-tab experiments (done)

  Restructured `dev_panel.py` from a flat checkbox list into grouped `SECTIONS` of `BoolOpt`/
  `ChoiceOpt` (choices render as themed `QComboBox` with int `userData`; values persist as-is).
  `DeveloperPanel` gained a `changed` signal forwarded from the dialog so a live view can react.

  New wired options:
  - **Preview resolution** (`dev_preview_height`, 160/240/360) — `build_clip_sample_cmd` gained a
    `height` kwarg (`scale=-2:{height}`); `merge_tab._preview_accel()` passes it and `_accel_sig()`
    includes it so the preview cache differentiates.
  - **Software playback smoothness** (`dev_review_frame_poll_ms`, 150/300/500) — `HybridPlaybackEngine`
    now takes `frame_poll_ms` (with a live `set_frame_poll_ms`), `make_engine` passes it through,
    `ReviewTab._new_engine()` is the single seam that injects it, and `reload_dev_settings()` (wired
    to `DeveloperPanel.changed` in `main`) live-updates the running engine.
  - **Allow GPU decode for 4K 10-bit HEVC** (`dev_review_allow_risky_hw_decode`) — gates the
    auto-force-software override: `risky = is_risky_hw_decode_profile(...) and not self._allow_risky_hw()`.
    Off by default; affects only the next master loaded (never persisted per-file).

  Tests: `test_dev_panel.py` (+choice/height), `test_review_playback.py` (+frame-poll engine),
  `test_ffmpeg_cmd.py` (height covered by scale assertions). 31 test files green.

### Task 83 — overview-filmstrip fix + preview/review Developer options batch (done)

  **Overview thumbnails never appeared (real bug).** `_start_thumbnail_strip` reserved slots via
  `OverviewTrackbar.set_thumbnail_count(N)` and the worker delivered all tiles (verified end-to-
  end against a real master), but `OverviewTrackbar.set_duration()` also did `self._thumbnails = []`
  — and the default `QtPlaybackEngine` reports duration **asynchronously**, so that clear ran
  *after* reservation and the worker had begun, resetting the list to empty. Every subsequent
  `set_thumbnail(i, img)` then failed its `index < len([])` guard and was dropped. Removed the
  clear from `set_duration` (a new master is cleared authoritatively by `load_master`'s
  `set_thumbnail_count(0)`). Regression test in `test_trackbar_thumbnails.py`.

  **Developer options batch.** Preview generation: `dev_preview_height` extended to 480/720.
  New preview-window group — `dev_preview_window_size` (small/medium/large), `dev_preview_aspect_mode`
  (fit/stretch/crop → `QVideoWidget.setAspectRatioMode`), `dev_preview_speed` (0.5/1/2 →
  `setPlaybackRate`), `dev_preview_loop` — all read in `merge_tab._show_preview_dialog` and applied
  by `_ClipPreviewDialog`. New Review overview group — `dev_review_thumb_count` (12/24/48) and
  `dev_review_thumb_width` (120/160/240), read in `_start_thumbnail_strip` (which now also cancels
  any in-flight worker) and regenerated live by `reload_dev_settings`.

  Tests: `test_trackbar_thumbnails.py` (new), `test_dev_panel.py` (+preview-dialog options, review
  thumbnail settings, DEFAULTS coverage). 32 test files green.

### Task 84 — same-camera clips split across baselines by avg-fps drift (fixed)

  A folder of clips from one camera at one nominal rate (29.97) was being split into multiple
  baseline groups and flagged to transcode. Root cause: `probe()` set `fps_float` from
  `avg_frame_rate` (total_frames/duration), which drifts a few hundredths per clip (measured
  directly: same 29.97 source reporting 29.92 / 29.95 / 29.97) even when `r_frame_rate` is a
  byte-identical clean nominal (`30000/1001`) across all of them. Because spec grouping keys on
  `fps_str` and `apply_conformance` compares `fps_float` at 0.01 tolerance, the drift produced
  distinct baselines and needless transcodes.

  Fixed by extracting `probe._nominal_fps(cap_fps, r_float, avg_float, is_vfr)`: capture-fps tag
  wins, else **r_frame_rate for CFR clips** (the stable nominal), else avg for genuinely-VFR clips
  (whose r is the misread one, e.g. Pixel VFR reporting r=120). VFR detection (`|r-avg|>0.5`) is
  unchanged, so VFR clips still conform. Verified against real camera files (clean clips report
  r==avg==30000/1001, so no behaviour change there). Test: `test_probe_fps.py` (unit + an
  integration asserting drifted same-camera clips all land `status=="ok"` on one `fps_str`).

### Task 85 — WAV-backup position drift: measured concat positions (fixed)

  The long-deferred Task 78 limitation, fixed properly. Root cause: the WAV-backup recovery
  window was seeked by the VIDEO's cumulative baseline offset (`compute_baseline_offsets` =
  Σ clip video durations), but the concat demuxer actually advances each segment's timestamps
  by the per-clip temp FILE's own container duration — nothing forces those to agree (a WAV/
  audio stream that out- or under-runs its video changes the file duration). Mid-track WAV
  windows could therefore land on the wrong samples: honest MD5 mismatches with no corruption.

  Fix — **measure, don't model**: after each per-clip temp file is muxed, one cheap local
  ffprobe (`probe.probe_concat_segment`) records (container duration, WAV-slot stream duration).
  The runner accumulates the true concat cursor and writes two new manifest fields per clip:
  `ClipEntry.concat_start` (measured segment start in the master) and `wav_track_duration`
  (measured WAV/ALAC segment length). A failed probe leaves them None and poisons every LATER
  position (a cursor is only trustworthy while every preceding duration was measured).
  `extract.build_recovery_plan` prefers the measured pair for the WAV window and falls back to
  the historical video-offset model for older manifests — fully backward compatible. The verify
  diagnosis now distinguishes drift-on-old-master (re-merge to fix) from a mismatch despite
  measured positions (genuinely worth a look). Tests: `test_extract.py` (+3: preference,
  fallback incl. half-set fields, manifest round-trip with tolerant load).

### Task 86 — seam diagnostic: pin mechanism behind mid-track video decode mismatches (done)

  New `core/seam_diag.py` (pure) + `tools/diagnose_midtrack_decode.py` (runner). Per clip:
  framemd5 the original's head (default 12s) and the master decoded from a widened lead-in
  (default 4s before the modelled start), then classify by per-frame hash alignment:
  full match at the expected offset → MATCH; full match at a shifted offset → **window
  rounding** (mechanism 2, reports the shift in frames/ms); damaged head + intact tail
  (≥50% aligned) → **seam damage** (mechanism 1, reports damaged-frame count); else divergent.
  Read-only tool, decodes to hashes on stdout. Tests: `test_seam_diag.py` (parse + all five
  verdict classes, incl. the over-explanation guard). The tool also checks each clip's TAIL
  (last 4s) — decisive when a head matches but full-window verification still fails.

  **Findings on the real 8-clip master (2026-07-07 Gran Canaria, plain stream-copy baseline,
  no archival tracks):** ZERO seam damage — every compared frame across all 8 clips (heads +
  tails, ~7,600 frames) decodes pixel-identically from the master. Every video verification
  failure in that master's verify log was **mechanism 2 only**: the modelled window boundary
  lands ±1 frame (≈33ms) off at clip boundaries (tails consistently +1; clip 026's head −1),
  so the hashed window contains one foreign frame / drops one own frame while the content
  itself is perfect. Notable: a same-spec, single-camera HEVC concat shows NO splice decode
  damage — unlike the earlier multicam mixed-encode master (Task 13's broken-splice playback
  bug). → Fixed for new masters in Task 87.

### Task 87 — video windows from measured boundaries + honest "unexpected" diagnoses (done)

  The follow-up Task 86's findings demanded. `extract.build_recovery_plan` now derives a
  baseline clip's VIDEO window from the measured positions Task 85 already records:
  `video_start = entry.concat_start`, window length = gap to the NEXT clip's measured start
  (falls back to the modelled duration for the last clip or past a poisoned probe), with a
  `video_measured` flag on the RecoveryPlan. Archival-track clips and old manifests are
  byte-for-byte unchanged.

  **Seek guards (`extract.SEEK_EPS` = 2ms), applied only to measured windows** — commands print
  timestamps at ms precision and frame pts are rational, so a hair of rounding must never cost
  a frame, and the two seek modes fail in OPPOSITE directions:
  - DECODE paths (verify's decoded-MD5 fallback) seek EPS **early** — accurate seek keeps
    frames with pts ≥ target, so rounding can't drop the clip's first frame, while the
    previous clip's last frame (a full frame-interval earlier) stays excluded; the window
    ends EPS before the next measured boundary, excluding the next clip's first frame.
  - COPY paths (bitstream ES extraction, `build_recover_clip_cmd`) seek EPS **late** —
    copy-mode input seek snaps to the nearest keyframe AT-OR-BEFORE the target, so a
    boundary rounding a hair below this clip's own IDR would otherwise snap a whole GOP
    back into the previous clip.

  Diagnosis honesty (the verify log's "unexpected — nothing to explain it"): the video branch
  now distinguishes (a) modelled mid-concat window → explains the ±1-frame boundary effect,
  points at tools/diagnose_midtrack_decode.py, notes new masters measure boundaries; (b)
  measured window still mismatching → genuinely suspicious, tool-pointer, closer look; (c)
  own-archival-track mismatch → the truly alarming case, called out as such. Audio keeps its
  own specialised text, which no longer asserts "the video decoded identically" without
  checking the actual Video result in the same report (it was provably false on 6 clips of
  the Gran Canaria log).

  Tests: `test_extract.py` (+3: measured video window incl. next-clip gap + last-clip
  fallback, archival/unmeasured invariance, copy-seek guard applied only when measured);
  the real-ffmpeg recovery integration test still passes bit-exact. 34 test files green.

### Task 88 — "Show me" merge animation (done)

  New `show_me.py` + a "✨ Show me…" button left of Pre-flight. An animated, plain-language
  explainer of what THIS merge will do, driven by the user's real selection and parameters —
  no ffmpeg involvement, purely a visualisation of the plan.

  Architecture (testable-first):
  - `build_story(clips, archival, per_clip_archival, optimize_baseline, compat_baseline,
    audio_tracks, output_name) -> Story` — PURE. Mirrors the merge's decision logic: conform
    status → copy vs convert (reason = first probe conflict; `_effective_optimize_baseline`
    forces all-convert with its own reason), archival mode → vault slots ("own" per clip for
    OTPC; shared spec-group labels for grouped mode, with conforming clips correctly getting
    NO slot — the baseline is their lossless copy), WAV chips, plan-ordered audio shelves.
    Folds clip 9+ into a "+N more" card (MAX_CARDS=8).
  - `build_phases(story)` — the narration/timing script (intro → one flight per card,
    staggered → optional compat sweep → outro).
  - `ShowMeCanvas` — QPainter scene: film-strip cards (sprockets, camera label, WAV reel
    icon) flying on an arc into a keepsake-box MOV container (movie-reel shelf + camera/
    wireless/mix tape shelves + dashed-green vault). Converting cards pass a "converter"
    ring and cross-fade to the warn tint; wav-less clips draw dotted silence fillers on the
    tape shelf. Time is INJECTED (`set_time`) so tests scrub without a timer; the dialog owns
    the 33ms wall-clock QTimer. All colours from `theme.active_palette()` (theme test green).
  - `ShowMeDialog` — canvas + live narration label + Replay.

  Merge-tab wiring: `_open_show_me` builds the story from `_selected_clips()` + the real
  parameter getters and opens the dialog; button enabled/disabled alongside Pre-flight.
  Tests: `test_show_me.py` (8: story decisions, optimize/archival/vault modes, +N fold,
  phase coverage, timerless scrubbing, offscreen paint at 4 scrub points, dialog replay).
  35 test files green.

### Task 89 — progress transparency + auto-selected archival defaults (done)

  Two independent user-facing fixes to the merge tab.

  **Progress transparency.** A slow transcode or MD5 pass looked identical to a hung
  process — the progress bar only showed a %, GB, and clip-name pills. `ffmpeg_runner.py`
  already emitted a `stage_label` string per progress tick but `merge_tab.py` never
  displayed it. Fixes:
  - New `_step_label` (plain-language current activity) + `_kind_badge` (coloured
    STREAM COPY / TRANSCODE / MERGE / ARCHIVE / MD5 VERIFY tag, via new
    `_progress_kind(stage, label, palette)`) above the stage-pill row, updated every
    `_on_progress` tick from `data["stage_label"]`.
  - `MergeWorker._clip_stage_label(clip, hw_encoder=None)` builds the actual text: a
    stream-copying clip says so plainly ("lossless, no re-encode"); a transcoding clip
    names the real encoder (GPU: NVENC/Quick Sync/AMD AMF, or CPU: libx264) and the real
    reason (`clip.stream.conflicts[0]`, e.g. "different fps") — recomputed on the
    GPU→software fallback retry so the label never lies about which path actually ran.
    Merge/archive/verify stage labels enriched to say what they are ("Merging clips into
    the baseline — stream copy, lossless", "Archiving original files, group 1/2 —
    lossless copy for recovery", "Verifying VID_002 (2/8) — MD5 pass against the
    original").
  - Fixed a real display bug found while wiring this up: `_verify_md5_recovery` emits
    `stage_idx`/`stage_total` numbered 0..len(clips) again (its own per-clip loop,
    unrelated to the merge's numbering), but `_on_progress`'s pill-coloring blindly
    re-applied that idx against the SAME pill list used during the merge — so watching
    verify run recolored already-green clip pills back to idle one at a time, looking
    like the merge was undoing itself. Fixed by branching on `data["stage"] == "verify"`:
    a dedicated "MD5 verify" pill (added only when Verify MD5 recovery is checked) is the
    only one touched during that stage; the merge's own pills are pinned green instead of
    re-evaluated against verify's unrelated numbering.

  **Auto-selected archival defaults.** Previously Archival master + One track per clip +
  Optimize baseline for delivery were all hardcoded checked, regardless of the footage —
  wasteful (extra tracks + a forced full re-encode) for a single camera shooting one
  consistent spec, where the baseline already stream-copies losslessly and there's no
  odd-spec original to protect.
  - New `_auto_select_archival_params()`, called from `_build_baseline_chooser()` (so it
    sees the just-computed `_spec_groups`) at both its exit points: `len(_spec_groups) <=
    1` (one shared spec — typically one camera, or several shooting identically) →
    Archival master ON, the other two OFF. `> 1` (varied specs) → all three ON, restoring
    the old safety-net behaviour (per-clip bit-exact tracks + a consistently re-encoded
    delivery baseline).
  - Never clobbers a deliberate choice: the three checkboxes' `toggled` signals now go
    through `_on_archival_checkbox_touched`, which sets `_archival_user_overridden = True`
    unless the change came from `_auto_select_archival_params` itself (guarded by
    `_applying_auto_archival`) — a real user click permanently opts that folder out of
    auto-selection. `_load_folder` resets the override flag, so a genuinely new folder
    gets a fresh automatic choice.
  - Construction-time defaults for One-track-per-clip/Optimize baseline flipped to
    unchecked (only matters before any folder is loaded — auto-selection immediately
    overwrites it once probing finishes).

  Manually verified offscreen: uniform-spec folder → (True, False, False); varied-spec
  folder → (True, True, True); a user click freezes the choice against further
  auto-application; resetting the override (simulating a new folder) re-applies it. All
  seven `_progress_kind` classifications (stream copy, transcode w/ GPU, merge,
  compat re-encode, archive ×2, verify) checked against the real label strings.
  35 test files green (no behavioural change to existing suites — this task's
  verification was interactive/scripted rather than a new test file, since it's UI
  wiring over already-tested worker logic).

### Task 90 — Review tab: thumbnail fix, Overview/Audio reorder+alignment, loading spinners, 480p fast preview (done)

  Four related Review-tab fixes/features from one user report ("thumbnails still not
  showing", "audio waveforms no longer showing", "Overview should sit above Audio and
  align with it", "preview is slow — add a low-res hardware-friendly proxy").

  **Root-caused the thumbnail bug** (real, not a race): generated a synthetic
  multi-keyframe H.264 test master (`ffmpeg -f lavfi testsrc/sine`, `-g 30
  -force_key_frames`) and ran `build_thumbnail_strip_cmd`'s exact command directly.
  Returncode -22 (`0xFFFFFFEA`), empty stderr (`-v quiet` swallows it) — running the
  same command at `-v error` revealed the real cause: ffmpeg's mjpeg encoder REJECTS
  standard "tv"/limited-range yuv420p ("Non full-range YUV is non-standard"), which is
  what virtually every real camera clip is (limited range is the norm; only synthetic
  full-range test sources happened to work, which is presumably how this shipped
  unnoticed). Waveform extraction (`build_pcm_extract_cmd`/`pyramid_from_stream`)
  was independently verified correct against both single- and multi-track (AAC+ALAC)
  synthetic masters — no bug found there; the "no longer showing" report is addressed
  by the new loading spinners below (a slow, silent extraction reading as broken) plus
  this thumbnail fix (both symptoms were reported together).
  - Fix: `core/review_media.py` `build_thumbnail_strip_cmd`'s `-vf` filter chain gets
    `,format=yuvj420p` appended — forces full-range colour before the MJPEG encode.
    Verified end-to-end (subprocess + QImage load) across 5 timestamps on the
    multi-keyframe synthetic master: 5/5 produced valid, non-null 160×90 JPEGs.
  - Test: `test_thumbnail_strip_cmd_forces_full_range_for_the_mjpeg_encoder` in
    `test_review_media.py`.

  **Overview/Audio reorder + alignment.**
  - `review_tab.py` `_setup_ui`: the Overview section block now comes before Audio
    tracks in both source order and `root.addWidget(...)` calls.
  - `widgets/audio_lanes.py`: exported `LANE_LABEL_MARGIN` (=180: checkbox 20px +
    spacing 10px + info column 140px + spacing 10px, now module constants
    `_CHECKBOX_W`/`_INFO_W`/`_LANE_SPACING` instead of ad-hoc literals) — the pixel
    width of the checkbox+name/codec column that precedes every lane's waveform
    canvas. Checkbox width was previously unset (platform sizeHint, non-deterministic);
    fixed to `_CHECKBOX_W` for a reliable constant.
  - `widgets/trackbar.py` `OverviewTrackbar`: overrides `TimelineBase._track_x()`/
    `_track_w()` to add `LANE_LABEL_MARGIN` to the left offset — every other paint
    method (`_paint_thumbnails`, `_paint_envelope`, `_paint_ruler`, `_paint_viewport`,
    plus the inherited `_paint_track`/`_paint_scrubber`) already computes off these two
    methods, so overriding them alone shifts the WHOLE track right to match. A new
    `_paint_label` draws "Video" in the freed-up margin, mirroring an audio lane's own
    name label. Verified by measuring `AudioLaneStack`'s actual laid-out canvas x
    (`lane.x() + lane._canvas.x()` after `show()` + `processEvents()`) against
    `OverviewTrackbar._track_x()` — both 180px, exact match.

  **Loading spinners.** New `widgets/spinner.py` `LoadingSpinner` — a small QTimer-driven
  rotating-arc `QPainter` widget (`start()`/`stop()`, hidden when stopped). New
  `review_tab.py` `_LoadingIndicator(QWidget)` pairs a spinner with a caption label,
  shown/hidden together (`start()`/`stop()`), built via `ReviewTab._loading_indicator(text)`
  and placed in each section's header `right=` slot (`_overview_loading` alongside the
  existing drag-hint label; `_audio_loading` alone, since Audio tracks previously had
  nothing there). Wired to the REAL worker lifetimes, not a fixed timer: overview
  spinner starts in `_start_thumbnail_strip` (right before `ThumbnailStripWorker.start()`)
  and stops in `_on_thumb_worker_finished`; audio spinner starts before
  `PeakScanWorker.start()` (only if there are audio tracks) and stops on the worker's own
  `finished` signal. Both explicitly stopped at the top of `load_master` so a fast
  re-load can't leave a stale spinner running. Verified offscreen: `isHidden()` is True
  initially, False after `start()`, True again after `stop()`.

  **480p fast-preview proxy.** New `core/review_media.py` functions:
  - `build_proxy_cmd(ff, path, out_path, height=480)`: `-map 0:v:0 -map 0:a` (preserves
    the master's audio-track ORDER — matches `probe_audio_tracks`' `audio_index`
    numbering, so `PlaybackEngine.set_audio_single(track_idx)` behaves identically on
    the proxy) into plain 8-bit H.264 (`profile high`, `yuv420p`, `veryfast`/CRF 23),
    `scale=-2:min(height\,ih)` (never upsamples a source already smaller than target),
    audio re-encoded to AAC (uniform codec regardless of source, since the source may
    be ALAC) at 128k, `+faststart`.
  - `proxy_cache_path(cache_dir, master_path, height)`: deterministic
    `sha1(resolved_path|size|mtime|height)[:16]`-keyed filename — a file replaced at the
    same path (a re-merge) gets a fresh proxy instead of reusing a stale one.

  New `review_workers.py` `ProxyRenderWorker(QThread)` — mirrors `MixRenderWorker`'s
  cache-hit-skip pattern (`proxy_ready` emitted immediately if `out_path` already
  exists), `cancel()` removes a partial file so a later existence check can't mistake
  it for done.

  `review_tab.py` wiring: new "Fast preview (480p)" checkbox next to Software decode
  (disabled by default, tooltip "Preparing a 480p proxy for this master…", persisted via
  `review_fast_preview_480p` setting). `load_master` always kicks off a background
  proxy render (`_start_proxy_render`, cache path under `get_app_dir()/_temp/
  review_proxy/`) — cheap to have ready even unchecked, so checking it later is instant
  rather than a fresh wait — while the INITIAL engine load always uses the real master
  (correct immediately, never blocked on a render). `_on_proxy_ready` (guarded by
  `master_path == self._path` against a stale race from a since-superseded load) enables
  the checkbox and auto-applies the proxy if already checked. New `_current_source_path()`
  (`self._proxy_path if self._using_proxy and self._proxy_path else self._path`) — also
  swapped into `_apply_decode_mode`'s GPU/software reload so toggling decode mode while
  Fast preview is active reloads the PROXY, not silently falling back to the master.
  `_swap_playback_source(use_proxy)` mirrors `_apply_decode_mode`'s
  save-position/reload/reseek/resume-play pattern for the live source swap. Scopes,
  snapshots, waveform peaks and the thumbnail filmstrip are entirely unaffected — they
  all read `self._path` directly, never `_current_source_path()`.

  Verified end-to-end against a synthetic multi-track (AAC + ALAC) master: proxy
  rendered to 854×480 h264/yuv420p with both audio tracks preserved in AAC, in order;
  loading with the checkbox pre-checked auto-applied the proxy the moment it was ready
  (confirmed via the engine's own ffmpeg debug log showing the 480p file loaded);
  toggling on/off after the proxy was already ready correctly swapped the live source
  both directions (confirmed via `_current_source_path()` and `_session.duration`
  staying correct post-swap); no `engine.error` signal fired.
  Tests: `test_review_media.py` (+4: audio-track-order mapping, scale never upsamples,
  cache-path determinism, cache-path changes when the file's size/mtime changes).
  35 test files green.

### Task 91 — Review tab: view/play individual archival clip originals (done)

  A master built with Archival master on carries every odd-spec clip's untouched
  original on its own hidden VIDEO STREAM (`core.manifest.ClipEntry.archival_track`) —
  previously only reachable via Extract and Recover, never actually watchable in the
  Review tab. User ask: "I want to be able to see the other video tracks and play the
  individual clip originals."

  - `core/ffmpeg_cmd.py` `build_preview_cmd` gains `video_track: int = 0`, adding an
    explicit `-map 0:v:{video_track}` — previously no `-map` at all, relying on
    ffmpeg's own "best stream" pick, which is unreliable once a master has more than
    one video stream. `ffmpeg_runner.py` `FramePreviewWorker` threads the same param
    through (default 0, so its two other unrelated callers in `whatsapp_tab.py` are
    unaffected).
  - `PlaybackEngine` (`review_playback.py`) gains `set_video_track(track_idx) -> bool`,
    alongside the existing `set_audio_single`. Unlike audio (where `HybridPlaybackEngine`
    always declines — no single "master" player to flip a track on), BOTH engines
    support this:
    - `QtPlaybackEngine.set_video_track`: `QMediaPlayer.setActiveVideoTrack` (confirmed
      present in this PySide6 version, symmetric with the existing audio-track API).
    - `HybridPlaybackEngine.set_video_track`: stores `self._video_track` (reset to 0 on
      every fresh `load()`), threaded into every `FramePreviewWorker` request from then
      on; immediately re-requests a frame so the switch is visible without waiting for
      the next poll tick.
  - `TrackScanWorker` (`review_workers.py`) now also calls `core.manifest.read_manifest`
    (embedded metadata first, sidecar fallback) and adds it as a 4th `tracks_ready` arg
    — `Optional[Manifest]`, `None` for a master with no manifest (Archival master was
    off, or it wasn't built by this app) or no archival tracks, in which case there's
    nothing new to offer.
  - `review_tab.py`: new "Video source:" combo in the Preview section (hidden unless
    the manifest actually has archival tracks) — `_populate_video_sources(manifest)`
    lists "Master (playable)" plus ONE ENTRY PER CLIP with `archival_track is not None`
    (not one per track: several clips sharing a track in grouped-archival mode each
    still get their own selectable entry, since each has its own `in_track_start`).
    `_on_video_source_changed(index)` calls `engine.set_video_track` + `engine.seek
    (in_track_start)`, sets a readout label ("Viewing original: VID_0042.MP4 (HEVC
    3840×2160…)"), and records `self._clip_window_end = in_track_start +
    in_track_duration`. `_on_session_position` auto-pauses once position reaches that
    point — an archival track can hold several concatenated clips back-to-back, and
    without this, playing "VID_0042's original" would silently run on into whatever
    clip the concat placed next on the same track. Picking "Master (playable)" clears
    the window and returns to the baseline (`set_video_track(0)`, seek to 0). Combo,
    readout and clip-window state all reset at the top of `load_master` so a fresh
    master never inherits the previous one's selection.

  Manually verified end-to-end against synthetic multi-video-stream masters (built with
  `ffmpeg -map 0:v -map 1:v -map 2:a`, hand-written matching manifest sidecars via
  `core.manifest.write_sidecar`) run through the REAL `ReviewTab.load_master()` →
  `TrackScanWorker` → `_populate_video_sources` → `_on_video_source_changed` pipeline:
  combo correctly lists Master + each clip; selecting a clip sets `engine._video_track`
  to its `archival_track` and seeks to its exact `in_track_start` (tested with two
  clips sharing ONE archival track at different offsets — 1.5s and 3.5s — both
  resolved correctly, not just the shared track's start); `_clip_window_end` computed
  correctly in both cases; auto-pause confirmed by driving the REAL engine's `play()`
  (not just the session state directly, which doesn't exercise `HybridPlaybackEngine`'s
  own `_playing` guard) then simulating a position report past the boundary — both
  `engine._playing` and `session.playing` correctly become `False`. 35 test files green
  (no new test file — this task's logic lives in ReviewTab methods requiring the full
  widget tree + a loaded manifest to exercise meaningfully; verified via the scripted
  end-to-end runs above instead, consistent with Task 89's approach for UI wiring over
  already-tested lower-level pieces).

### Task 92 — real WAV sync bug: coarse envelope rescue for large clip/WAV duration mismatches (fixed)

  User report: "audio from 26m50s is completely mismatched" on a real 8-clip Gran
  Canaria master with WAV set as primary audio.

  **Diagnosis.** The master's manifest placed 26m50s ~15s into clip
  `VID_20260707_183203_025` (a baseline chapter start at 1595.18s). The verify log
  showed WAV/video mismatches on 6 of 8 clips, so the first task was separating
  "genuine problem" from "known MD5-vs-window-rounding noise" (Task 87's honest
  diagnosis branching flags both as "worth a closer look," deliberately, since it can't
  tell them apart from a hash alone):
  - `tools/diagnose_midtrack_decode.py` on clip 025's VIDEO: head MATCH (600/600 frames
    pixel-identical), tail WINDOW-OFFSET (1-frame rounding) — i.e., mechanism 2, the
    already-understood, harmless kind. Confirmed the same for other failing clips too.
  - Custom GCC-PHAT cross-correlation scripts (ad hoc, not shipped) comparing the
    master's embedded WAV-track audio against each clip's own original WAV backup:
    clips 023/024/027/028/029 all showed strong, confident correlation (conf 0.57-0.79)
    once compared against their real short (~0.3-0.4s) pre/post-roll padding — genuinely
    fine audio, MD5 mismatch was the same rounding-window noise as the video. Clip 025
    showed ZERO correlation at every offset tried (conf ~0.00-0.04), including a coarse
    full-file search — a real problem, not noise.
  - Root cause found by comparing WAV/video DURATIONS: every other clip's WAV backup is
    within 0.3-0.4s of its video's length (ordinary mic-vs-camera start/stop lag).
    Clip 025's WAV is **385 seconds longer** than its video — the wireless mic was
    clearly left running well beyond that one clip. `core/sync_advanced.py`'s
    `analyze_sync` assumes END-ALIGNMENT (all the WAV/video duration difference sits as
    pre-roll before the clip) and only fine-tunes within `MAX_TAU=0.5s` of that guess —
    for a 385s real mismatch, every one of the 6 GCC-PHAT windows searched nowhere near
    the true alignment, so the merge locked onto a garbage constant offset and embedded
    audio from ~385s away from where it belonged, for that clip's ENTIRE ~30-minute
    span in the master. Confirmed conclusively with a robust whole-clip RMS-envelope
    (1-second bins) cross-correlation between the master's embedded audio and the
    original WAV file: z-score 10.51 (>10 std devs above the noise floor) at offset
    385s — an unambiguous real match, just at completely the wrong place.

  **Fix — `core/sync_advanced.py`:**
  - New pure functions `rms_envelope(pcm, bin_n)` (per-bin RMS — the coarse,
    drift-tolerant counterpart to `gcc_phat_lag`'s sample-accurate delay estimate) and
    `envelope_offset(env_a, env_b)` (cross-correlates two envelopes, returns
    `(offset_bins, z_score)` — the confidence gate). Both unit-tested with synthetic
    signals (known-shift recovery, unrelated-signal low-confidence, edge cases).
  - `LARGE_MISMATCH_S = 5.0`: when `abs(clip_dur - wav_dur)` exceeds this (real shoots
    show ~0.3-0.4s normally — wide margin before distrusting end-alignment), a coarse
    envelope pass (`_coarse_preroll`, whole-clip vs whole-WAV, `_extract_envelope`) runs
    BEFORE the fine per-window loop. Its result becomes the anchor for that fine loop
    only if `z_score >= ENVELOPE_Z_THRESHOLD = 4.0` (measured directly: real match ≈10,
    unrelated audio ≈0); otherwise falls back to end-alignment with a note warning the
    result should be checked by ear, rather than silently trusting a low-confidence
    coarse guess either.
  - Generalized the per-window position math from being end-alignment-specific
    (`ct = clip_dur - from_end`, `wt = wav_dur - from_end`) to anchor-relative
    (`ct = overlap_start + EDGE_SKIP + i*step`, `wt = ct + preroll`) — algebraically
    verified to reduce to the EXACT original formula when `preroll` is the default
    end-alignment value, so every clip that already worked (the 0.3-0.4s-mismatch
    common case) is completely unaffected.
  - Real bug found while validating the fix itself: `_extract`'s existing 20s
    subprocess timeout (sized for the ~4s per-window pulls) silently killed the new
    whole-clip envelope extraction on the 11.7GB/30-minute source (`_extract_envelope`
    got `None` back, envelope alignment always failed). Fixed by threading a `timeout`
    param through `_extract` and scaling it in `_extract_envelope`
    (`max(60, min(600, dur))`).
  - `analyze_sync` docstring rewritten to explain the end-alignment assumption
    explicitly and why it needed this rescue path.

  Verified end-to-end against the real clip that exposed the bug: **before the fix**
  (traced through the broken math) the old code would compute `constant_offset ≈
  -384.5s`. **After the fix**: coarse envelope pass finds `z=6.8` (confident), fine
  windows lock onto a tight, consistent `constant_offset = -0.6s` (residual 5.7ms
  across all 6 windows) — right in line with the ~0.3-0.5s startup lag every other clip
  in the same shoot shows. Practical guidance for the user: re-running the merge picks
  up the fix; the already-built master can't be retroactively repaired byte-for-byte,
  since the wrong audio is already baked into its baseline track.
  Tests: `test_sync_advanced.py` (+5: envelope reflects loud/quiet blocks, trailing
  partial bin dropped, known-shift recovery with z-score, low confidence for unrelated
  signals, a-longer-than-b safe no-match). 35 test files green.

### Task 93 — user control over WAV sync, smarter MD5 verify, visual Pre-flight (done)

  Three related asks in one request: manual control over WAV alignment/drift (on top of
  the already-existing WAV reassignment + manual-nudge UI), MD5 verify that skips
  predictably-doomed checks instead of running them anyway, and a visual (film-strip/
  tape-reel) illustration in Pre-flight.

  **Alignment mode + drift override (`core/sync_advanced.py`, `clip_model.py`,
  `audio_sync_dialog.py`, `ffmpeg_runner.py`).**
  - `analyze_sync` gains `anchor_mode: str = "auto"` ("auto" = existing behaviour —
    end-alignment, or the Task 92 coarse-envelope rescue for a large duration mismatch;
    "start" forces `preroll=0`, bypassing the rescue entirely; "end" forces literal
    end-alignment even for a large mismatch, also bypassing the rescue). Both overrides
    are simple branches ahead of the existing `if abs(res.end_offset) > LARGE_MISMATCH_S`
    check — the default path (`anchor_mode="auto"`) is untouched byte-for-byte.
  - `ClipInfo` gains `alignment_mode: str = "auto"` and `drift_override: Optional[float]
    = None` + `effective_drift_ratio()` (`drift_override if set else sync_drift_ratio`).
    `MergeWorker._mix_for` now calls `effective_drift_ratio()` instead of reading
    `sync_drift_ratio` directly — the ONLY place drift reaches ffmpeg (the mix track's
    `atempo` filter; the lossless WAV track is never resampled, unaffected either way).
  - `AdvancedSyncDialog` (already existed, with a manual-nudge spinbox and — discovered
    during research — an already-working "double-click a clip's WAV cell to reassign"
    dialog) gains: an Alignment combo (Auto/start/end) that RE-RUNS analysis on change
    (`_start_analysis()`, settling the previous `_AnalyzeThread` first); a Drift combo
    (Auto/Off/Custom ms/min) that doesn't need to re-run analysis (applied at Apply
    time only); a "Reassign WAV file…" button taking an `on_reassign_wav` callback
    (merge_tab.py wires it to the EXISTING `_open_wav_swap_dialog`, so this dialog and
    the table's double-click share one implementation) — after reassignment, closes
    with a message if the clip ends up with no WAV, or re-runs analysis for the new
    pairing.
  - Tests: `test_sync_advanced.py` (+3: anchor_mode="start"/"end" both skip the coarse
    rescue and lock in their forced preroll even for a large mismatch, monkeypatching
    `_extract`/`_coarse_preroll` via a small `_Patched` context-manager helper since
    this project's tests are plain scripts, not pytest — "auto" still runs the rescue,
    confirming the override is additive); new `test_clip_model.py` (+4: effective_drift_
    ratio prefers override/falls back to auto-detected/exact custom value, fresh-clip
    defaults). Smoke-tested `AdvancedSyncDialog` fully offscreen with a faked
    `_AnalyzeThread` (real thread would touch ffmpeg): alignment combo correctly
    updates `clip.alignment_mode` and (would) restart analysis; drift combo correctly
    reads back an existing `drift_override=1.0` as "Off"; reassign button correctly
    wires to and invokes the provided callback.

  **Smarter MD5 verify (`core/verify.py`, `ffmpeg_runner.py`, `merge_tab.py`).**
  Researched exactly which verify outcomes are ALREADY 100%-predictable from the
  manifest alone (not just "usually fails"), reusing the reactive diagnosis text
  `compare_adaptive` builds AFTER a mismatch as the source of truth for what to predict
  BEFORE attempting one:
  - Video: `entry.conform_status == "transcode" and plan.video_stream == 0` — re-encoded
    straight into the shared baseline with no archival track backing it up; nothing
    byte-exact survives to compare against, ever.
  - Camera audio: `entry.has_camera_audio and plan.audio_stream is not None and
    plan.video_start > 0 and not safe_to_read_unbounded` — a non-first clip sharing a
    track (baseline or grouped archival); AAC priming + audio/video boundary drift at
    the concat seam make exact alignment impossible, confirmed directly against a real
    multi-clip master where every clip meeting this condition failed, not just some.
  - New `core/verify.py` `predict_unverifiable(entry, plan, own_archival_track,
    safe_to_read_unbounded) -> dict` — pure, no ffmpeg — returns `{label: reason}` for
    the above; a label absent is still worth attempting (most real mismatches ARE
    genuine surprises). `_PREDICTED_PREFIX = "predicted unverifiable"` constant so a
    verify-log/summary scan can count real skips precisely regardless of the specific
    per-check reason text.
  - `MergeWorker.__init__` gains `skip_predictable_verify: bool = True`. `_verify_one_clip`
    computes `predicted_unverifiable` once (only when the flag is on) right after
    `own_archival_track`/`safe_to_read_unbounded`, then gates the Video and Camera-audio
    blocks: predicted → append a `StreamCheck(skipped_reason=f"{_PREDICTED_PREFIX} — "
    + reason)` directly, no extraction; not predicted → the existing `compare_adaptive`
    path runs exactly as before (unchanged), including its own reactive relabeling of a
    camera-audio mismatch — now only reachable when the flag is off, i.e. the user's
    explicit "verify everything anyway" override. `ClipVerifyResult.passed` already
    excludes any `skipped_reason` check from its pass/fail computation (pre-existing
    behaviour, confirmed by reading it, not changed) — so a clip whose only issue was a
    now-skipped, always-doomed camera-audio check correctly counts as PASS overall
    instead of falsely dragging the summary down for something that was never a real
    problem. `_verify_md5_recovery`'s summary line now appends a `(N checks predicted
    unverifiable, skipped)` note when any occurred.
  - New checkbox in merge_tab.py's ARCHIVAL & DELIVERY section: "Skip checks predicted to
    fail (recommended)", nested under Verify MD5 recovery (disabled when that's off,
    matching the existing cascading-fade pattern), default checked — unticking is the
    user's override for exhaustive verification regardless of what's predictable.
  - New `tests/test_verify.py` (+9, pure, `SimpleNamespace` fakes for `entry`/`plan`, no
    ffmpeg/Qt): conforming first clip predicts nothing; transcoded-with-no-archival-track
    predicts Video (and does NOT when it HAS one, via `video_stream != 0`); first clip's
    camera audio never predicted even on a shared track (nothing precedes it); non-first
    clip on a shared track predicts Camera audio (and does NOT when
    `safe_to_read_unbounded`); no-camera-audio/no-audio-stream never predicted (those
    have their own, different, pre-existing skip reasons); `_PREDICTED_PREFIX` stability.
    Smoke-tested the merge_tab.py checkbox offscreen: exists, defaults checked, enabled
    state cascades correctly from Verify MD5 recovery.

  **Visual Pre-flight (`preflight_dialog.py`, `merge_tab.py`).** Rather than build a new
  visualization system, reused `show_me.py`'s existing `ShowMeCanvas`/`Story`/
  `build_story` wholesale: `PreflightDialog` gains an optional `story: Optional[Story] =
  None` constructor param; when supplied, a `ShowMeCanvas(story)` is embedded above the
  existing per-clip cards inside its own titled frame ("HOW YOUR CLIPS BECOME THE
  MASTER"), with `set_time(total_duration)` called once right after construction — a
  purely STATIC final frame (everything already landed on the reel/shelves/vault), no
  QTimer, since `ShowMeCanvas` itself owns no timer (only `ShowMeDialog` does) and
  `set_time()` is exactly the injectable-time seam `test_show_me.py` already scrubs in
  its own tests. Dialog's minimum size grows from 560×480 to 700×480 only when a story
  is provided; `story=None` (no caller passes one outside merge_tab.py yet) is
  byte-for-byte the previous behaviour. `merge_tab.py`'s `_open_preflight` now builds the
  `Story` the identical way `_open_show_me` already does (same parameters, same
  `build_story` call) and passes it through — Pre-flight and Show Me are now guaranteed
  to depict the same plan, since both derive it from one function.
  Tests: new `tests/test_preflight_dialog.py` (+3, offscreen): no story → no diagram
  attribute + original 560×480 minimum (backward compat); story → diagram exists, frozen
  at its own `total_duration` (not mid-animation), minimum size grows to accommodate it;
  diagram actually renders to a `QImage` without crashing and paints real (non-flat,
  std-dev > 1.0 across channels) content, not a blank frame.

  38 test files green (35 → 38: new `test_verify.py`, `test_clip_model.py`,
  `test_preflight_dialog.py`; existing files gained tests in place).

### Task 94 — 5 real user reports: no-WAV clip fallback, MD5 window-rounding, Bluetooth wording, per-clip Primary override, DST display bug (done)

  One session, five related asks from an actual multi-day shoot's merge (`20260707 -
  Lola and Popops return from Gran Canaria`), diagnosed against the real manifest.json
  and verify.log before touching any code.

  **No-WAV clip audio fallback (`core/ffmpeg_cmd.py`).** Reported: a master with
  "primary" = WAV had a silent clip on the one clip (026) with no WAV backup. Root
  cause: `_slot_fill`'s `kind == "wav"` branch only had `wav_alac`/silence — no
  fallback to camera audio, unlike the `camera` branch's existing `wav_aac` fallback
  in the opposite direction. Since "primary" sets ONE track's `-disposition:default`
  for the WHOLE FILE, a clip missing that track's normal source played silent even
  with real audio available on the other track. Fix: new `cam_alac` fill (camera
  audio re-encoded ALAC) mirroring `wav_aac` symmetrically. Tests: `test_ffmpeg_cmd.py`
  (+2: no-WAV clip's backup slot uses camera audio not silence; `plan_report.py`
  agreement); `test_plan_report.py` (+2: report/builder agree, notes explain it).

  **MD5 verify: measured-window video/WAV mismatches (`core/verify.py`,
  `ffmpeg_runner.py`).** Reported: "skip checks predicted to fail" was on, but Video
  and WAV backup checks still ran to full, expensive, failing completion — only
  Camera audio had a prediction rule (Task 93). Confirmed via the real verify.log
  that these fail almost every time on measured-window mid-concat clips, and via
  `tools/diagnose_midtrack_decode.py` (run against the real master) that the video
  mismatches are benign 1-frame window-rounding (Mechanism 2, `core/seam_diag.py`),
  not damage — head matched perfectly, tail was off by exactly 1 frame, decodes
  identically. Rather than blanket-skip (which would hide genuine corruption), added
  a cheap PARTIAL pre-check that distinguishes benign rounding from a real mismatch
  before running the expensive full pass:
  - `quick_video_rounding_check` — decodes a SHORT head+tail window (framemd5,
    `core.seam_diag.classify_window`) instead of the whole clip; benign only if
    EVERY window checked is MATCH or WINDOW-OFFSET — a SEAM or DIVERGENT verdict, or
    a decode error, falls through to the full comparison unchanged.
  - `quick_wav_rounding_check` — decodes a short window from the source WAV, then
    scans a small range of seek shifts (±120ms, 15ms steps) around the master's
    modelled position for an exact decoded-PCM match — the audio analogue.
  - Wired into `_verify_one_clip`'s Video and WAV-backup sections, gated by
    `self._skip_predictable_verify` and the exact "measured window, shared/baseline
    track" conditions the existing reactive diagnoses already targeted
    (`not own_archival_track and plan.video_measured` / `used_measured`) — confirmed
    benign → honest PASS with a diagnosis citing the pre-check's finding, no full
    extraction; not confirmed → falls through to the pre-existing `compare_adaptive`/
    full-WAV-compare path unchanged, so real corruption is never silently hidden.
  - New `probe_audio_stream_count` (core/verify.py) — reused later by Task 94's
    "preserve WAV in full" feature.
  - Tests: `test_verify.py` (+8, monkeypatching `_run_framemd5`/`decoded_md5` module
    globals rather than real ffmpeg, mirroring `test_seam_diag.py`'s hash-list
    fixtures): confirms benign window-rounding (head+tail), rejects genuine
    divergence, decode errors are never treated as benign, finds/fails-to-find a
    shifted WAV match, source-decode-failure isn't benign; +2 for
    `probe_audio_stream_count`.

  **Remove "Bluetooth" wording (`merge_tab.py`, `core/ffmpeg_cmd.py`,
  `core/track_info.py`).** Renamed `"Camera audio (Bluetooth mic)"` →
  `"Camera audio (AAC)"` in the 4 places it appeared (TRACK_OPTIONS,
  `_update_primary_labels`, the per-clip audio-slot title, `track_info.audio_tracks`'s
  label). Left `"(on-board mic)"` and About tab's narrative prose (which explains the
  Bluetooth-mic-into-camera + on-board-mic-backup workflow) untouched — neither was
  flagged as wrong. Updated the one test asserting the old label text.

  **Per-clip Primary override + WAV reassign/disconnect mismatch dialog + preserve-
  in-full (`clip_model.py`, `core/ffmpeg_cmd.py`, `merge_tab.py`, `core/manifest.py`,
  `ffmpeg_runner.py`, `core/extract.py`).** Originally "let the user pick the primary
  audio track per clip," expanded during confirmation to also cover WAV
  disconnect/reassignment mismatches and full-WAV preservation. Every option maps
  onto already-tested machinery rather than inventing new merge behaviour:
  - `ClipInfo.primary_override: Optional[str] = None` ("camera"/"wav"/"mix") and
    `preserve_wav_full: bool = False` (opt-in, defaults OFF per explicit correction —
    NOT matching the video archival track's checked-by-default).
  - New "Primary" column (COL_PRIMARY) in the clips table: a per-row `QComboBox`
    (`_build_primary_combo`) offering only options this clip can actually satisfy
    (`_valid_primary_options` — pure, tested) plus "Auto"; manually-overridden rows
    get an accent border + bold text (`_style_primary_combo`), matching the
    `manually_moved` visual-cue pattern already used for COL_ORDER.
  - `core/ffmpeg_cmd.py`'s `_override_fill(target, slot_codec, clip)`: resolves the
    override for ONLY the disposition-default slot (index 0 of the enabled tracks)
    by reusing `_slot_fill`'s existing fill vocabulary (camera-into-ALAC is the same
    `cam_alac` fallback Task 94 added above; wav-into-AAC is the existing `wav_aac`
    fallback; mix gets a new `mix_alac` fill — same mix filtergraph, ALAC-encoded —
    for when Mix is forced into a WAV-primary file). Returns `None` when the
    requested source isn't available on this clip, or for slow-motion clips (forcing
    un-stretched camera "copy" onto a stretched-video slot would desync it) — the
    caller then falls back to Auto rather than forcing something silently wrong.
    Every OTHER slot (e.g. the WAV-backup track when Primary=camera) keeps its own
    normal automatic fill — Primary and the WAV cell stay separate concerns.
  - WAV mismatch dialog (`_WavMismatchDialog`, styled like the Quality Target radio
    cards): shown from `_open_wav_swap_dialog` before committing a reassignment whose
    duration differs from the clip's own by more than `core.sync_advanced
    .LARGE_MISMATCH_S` (5s). Four mutually-exclusive options — Trim automatically
    (recommended, default) / Align to clip start / Align to clip end / Don't use this
    WAV — map straight onto `ClipInfo.alignment_mode` ("auto"/"start"/"end") or a
    disconnect (falls back to camera audio automatically via the fix above). A
    separate, non-exclusive checkbox ("Also preserve this WAV in full, on its own
    archival track") sets `preserve_wav_full`, unchecked by default. Cancelling
    leaves everything uncommitted.
  - "Preserve WAV in full": `core/ffmpeg_cmd.py`'s `build_wav_archival_mux_cmd` appends
    each requested clip's untouched original WAV as its own standalone, stream-copied
    (byte-exact — MOV carries linear PCM natively, no re-encode needed), explicitly
    non-default audio track onto an already-finished master — independent of whether
    video Archival master is even on. `MergeWorker._append_preserved_wavs` runs this
    as an extra final stage (after the existing archival-or-not master is ready, before
    the atomic move into place) only when at least one clip requested it; probes the
    master's existing audio-stream count first (`probe_audio_stream_count`) so the new
    streams' indices/dispositions are computed from what's actually there. New
    `ClipEntry.wav_archival_stream` records each preserved clip's dedicated stream
    index in the manifest; `RecoveryPlan.wav_archival_stream` + new
    `build_recover_wav_archival_cmd` (plain stream copy) thread it through
    `core/extract.py` for a future one-click recovery action — not wired into an
    Extract-tab UI action yet, scoped out of this pass.
  - Tests: `test_ffmpeg_cmd.py` (+12: `_override_fill`'s 4 source/availability/mix-
    requires-conform cases; per-clip override forcing WAV into a camera-primary
    slot and vice versa; Auto is a no-op; an unavailable override falls back to Auto;
    slow-motion clips ignore the override; `build_wav_archival_mux_cmd`'s stream
    mapping/disposition/no-op-when-empty); new `test_merge_tab_primary.py` (+8:
    `_valid_primary_options`' 5 availability combinations; retroactive coverage for
    the DST fix below, which had none). `test_extract.py` (+3: RecoveryPlan carries/
    omits `wav_archival_stream`, the recovery command is a bare stream copy).

  **DST/timezone display bug (`merge_tab.py`).** Reported: Timestamp column values
  differ from filenames "for daylight saving reasons" and a "differs from filename"
  warning fires. Confirmed via the real manifest.json that the camera's metadata is
  CORRECT (every clip's UTC `creation_time` is exactly +1h from its BST filename
  time) — not a metadata bug. `_fmt_timestamp_cell` parsed the UTC-aware datetime
  correctly but never called `.astimezone()` before formatting, so it displayed raw
  UTC — and the "differs" warning false-positived on every DST-affected clip
  universally, not just this shoot. Fix: `.astimezone()` before `.strftime(...)`.
  Confirmed via `clip_model._iso_epoch` (already timezone-safe via `.timestamp()`)
  that clip ORDERING is unaffected — display-only bug. Tests in the new
  `test_merge_tab_primary.py` above: converts UTC to local before display; no false
  "differs" warning when the filename matches the correctly-converted local time;
  still flags a genuine clock mismatch.

  39 test files green.

### Task 95 — Extract tab: recover the preserved WAV + manual controls for foreign masters (done)

  Two related asks: finish wiring up Task 94's "preserve WAV in full" opt-in so it's
  actually recoverable from the Extract tab (scoped out of that pass), and give the
  Extract tab manual override controls for a master produced by a DIFFERENT tool
  (no manifest, and possibly no/wrong chapter markers either).

  **Recovering the preserved WAV (`extract_workers.py`, `whatsapp_tab.py`).**
  `ExtractWorker.run()` gains a 4th per-clip recovery step: when
  `plan.wav_archival_stream is not None`, runs `core.extract
  .build_recover_wav_archival_cmd` (plain stream copy — already implemented, just
  unwired) to `"{stem} (WAV - preserved original).wav"`, alongside the existing
  video/camera-audio/WAV-backup steps — a failure here isn't fatal to the clip,
  matching the existing WAV-backup step's own leniency. `_populate_extract_tree`'s
  "Recovers as" column now mentions this file when applicable, so the user sees
  it'll come out before clicking Extract, not just discovers it afterward.

  **Manual controls for a foreign master (`probe.py`, `core/extract.py`,
  `extract_workers.py`, `whatsapp_tab.py`).** The existing no-manifest fallback
  (chapter markers → `GenericRecoveryPlan`) hard-coded audio track 0 as camera
  audio, video stream 0, no WAV-role concept, no rotation override, and gave up
  entirely on a master with zero chapters. Every `GenericRecoveryPlan` field is
  now user-editable rather than assumed, and a chapterless master is no longer a
  dead end:
  - New `probe.py` `VideoTrackInfo`/`parse_video_tracks`/`probe_video_tracks`,
    mirroring `AudioTrackInfo`/`probe_audio_tracks` — needed so the Extract tab can
    offer a video-stream picker for a master carrying more than one video stream
    (e.g. its own archival-track-style embedding from a different tool).
  - `ManifestLoadWorker.manifest_ready` now emits the full `AudioTrackInfo`/
    `VideoTrackInfo` lists (was just a bare list of audio indices) — one signal
    signature change, one call site (`whatsapp_tab.py`), no other listeners.
  - `GenericRecoveryPlan` gains `wav_stream: Optional[int] = None` (a second audio
    track manually assigned the WAV-backup role) and `rotation: Optional[int] =
    None` (explicit override in degrees — `None` means "leave untouched", so a
    deliberate "force to 0°" is distinguishable from "don't touch it").
    `build_generic_recovery_plans` gains optional `camera_audio_index`/
    `wav_audio_index`/`video_stream_index` overrides (all default to today's
    exact behaviour when omitted — fully backward compatible). New
    `build_generic_recover_wav_cmd` (the WAV-role analogue of
    `build_recover_wav_cmd`) and `_rotation_metadata_args` (`-metadata:s:v:N
    rotate=X` + `-movflags use_metadata_tags` — ffmpeg's MOV/MP4 muxer translates
    this into the correct display-matrix side data automatically, so it works
    alongside a plain stream copy, no re-encode needed), wired into
    `build_generic_recover_clip_cmd`.
  - `GenericExtractWorker.run()` recovers the WAV-role track too when
    `plan.wav_stream is not None`, non-fatal on failure, same pattern as
    `ExtractWorker`'s own WAV-backup step.
  - `whatsapp_tab.py`: a new "Manual controls" panel (`_ex_manual_frame`, built
    fresh per master load by `_rebuild_manual_controls`) appears whenever the
    generic (no-manifest) path is active, chapters or not:
    - **Audio tracks** — one role combo per detected track (Ignore/Camera
      audio/WAV backup, from `probe_audio_tracks`); changing any of them
      re-derives every plan's `audio_stream`/`wav_stream` in place
      (`_on_audio_roles_changed`) and refreshes the tree without losing check
      states.
    - **Video track** — shown only when `probe_video_tracks` finds more than one
      stream; re-derives every plan's `video_stream` on change.
    - **Rotation override** — Auto/0°/90°/180°/270°, applied to every plan.
    - **Per-row ✎ Edit / 🗑 Remove** (new `EX_COL_EDIT`/`EX_COL_REMOVE` tree
      columns, manual-mode only) and a **"+ Add clip…"** button — all three open
      `_ManualClipDialog` (name + start/duration as `H:MM:SS` text, reusing the
      Share panel's own `_tc_to_secs`/`_secs_to_ffmpeg`), and land in
      `_commit_generic_plan`, which stamps a new or edited `GenericRecoveryPlan`
      with whatever the audio-role/video-track/rotation controls currently say —
      this is what makes a completely chapterless foreign master usable at all,
      replacing the old "no manifest and no chapter markers — nothing to
      recover" dead end with an empty, buildable list.
    - `_populate_extract_tree_generic`'s Spec/Recovers-as columns now surface the
      manual WAV-role assignment and rotation override per row.
  - Tests: `test_probe_tracks.py` (+4: video-only stream indexing, no-video-
    streams, missing-field fallback, plain-dataclass check); `test_extract.py`
    (+7: camera/WAV/video-stream overrides win over the chapter-derived default,
    default has no WAV stream, the WAV-role recovery command maps the assigned
    track, rotation metadata args absent/present/explicitly-zero); new
    `test_extract_manual_mode.py` (+10, offscreen, constructs a real `WhatsAppTab`
    and drives `_on_extract_manifest_ready` directly with fake `AudioTrackInfo`/
    `VideoTrackInfo`/`ChapterInfo` — no real master file needed): manifest path
    still hides the manual frame; a chapterless master shows it and starts with
    an empty, disabled-Extract-button list; the video-track combo only appears
    with >1 stream; audio-role/rotation changes propagate to every plan; the
    full add→edit→remove round trip; a newly-added plan carries whatever the
    manual controls currently say; the Spec column reflects it; the timecode
    helpers and `_ManualClipDialog.values()` parse correctly.

  40 test files green.

### Task 96 — Extract tab: "ignore manifest" override for manual extraction (done)

  Follow-up to Task 95: even when a manifest IS found, the user may want the manual
  controls anyway — a hand-edited master, one from a buggy older version, or a
  manifest that's simply wrong for this particular file. Previously a present
  manifest was used unconditionally with no opt-out.

  **`whatsapp_tab.py`.** New `self._ex_ignore_manifest_check` (`QCheckBox`, hidden
  by default, shown only once `_on_extract_manifest_ready` confirms a manifest was
  actually found) sits just under the verify banner. `ManifestLoadWorker` already
  probes chapters/audio/video tracks unconditionally regardless of manifest
  success (Task 95), so toggling this needs no second probe — `_on_extract_manifest_
  ready` now just stashes the raw `(manifest, chapters, audio_tracks, video_tracks)`
  onto the instance and defers the actual mode decision to a new
  `_apply_extract_mode()`, called both there and from the checkbox's `toggled`
  handler (`_on_ignore_manifest_toggled`). `_apply_extract_mode` is the single
  source of truth: manifest-driven recovery when a manifest exists AND the
  checkbox is unchecked, the SAME manual/generic path Task 95 built (chapter-based
  if chapters exist, else the same buildable-from-scratch empty list) otherwise —
  reusing `_rebuild_manual_controls`/`_populate_extract_tree_generic` byte-for-byte,
  so "ignore manifest" is genuinely indistinguishable from a real no-manifest
  master once toggled on. `_load_extract_master` resets the checkbox (unchecked,
  hidden, signals blocked while doing so) on every new master load — a fresh master
  always starts trusting its own manifest.

  Two call sites that re-derived "which mode is active" from `self._extract_
  manifest` directly needed the same fix (confirmed as a real bug this way — both
  would have silently ignored the checkbox otherwise):
  - `_on_extract_format_changed` (repopulates the tree when the output-format combo
    changes) — now checks `self._extract_generic_plans is not None` FIRST, since
    that field is `None` exactly when manifest mode is active (per
    `_apply_extract_mode`) and stays a real list either way once ignored.
  - `_start_extract` — same reordering, with a comment pointing back at
    `_apply_extract_mode` as the source of truth so a future edit doesn't
    reintroduce the drift.

  Tests: `test_extract_manual_mode.py` (+5): the checkbox is offered when a
  manifest is found and hidden when one isn't; toggling switches to manual mode
  using the SAME probed chapters and back again, rebuilding the tree correctly
  both ways; manually-added/edited clips work normally once ignored (including the
  chapterless-master case, starting from an empty list even though a manifest
  technically exists); `_start_extract`'s mode-dispatch logic matches the active
  mode rather than raw manifest presence.

  40 test files green (existing file gained tests in place, no new file this time).

### Task 97 — clip-split detection, WAV duration column, per-clip transcode/LRV override, LRV preservation (done)

  Five items from a live investigation into a real shoot: comparing clips 025/026/
  026's WAV found the camera splitting one continuous take into two video files
  while a separate audio recorder kept rolling across the split (see the
  clip-model finding below); the user then asked for the app to detect/resolve
  that pattern automatically, show WAV duration in the clip list, let a clip's
  video be manually forced to transcode (or conform an LRV proxy instead) while
  archiving the byte-exact original, and optionally preserve an LRV proxy too.
  Clip 026 itself was separately diagnosed and found clean (see below) — its
  earlier missing-WAV finding (Task 94) is the actual explanation for the
  "odd playback" report, not a new anomaly.

  **Clip 026 diagnosis (no code change — investigation only).** Compared clip
  026 against siblings 022–029 on codec/profile/color space (identical),
  frame-rate drift (measured avg fps vs. nb_frames × 1001/30000 — every other
  clip drifts +2 to +22 frames from accumulated encoder timing; 026 is the
  ONLY one at exactly 0), per-frame PTS gaps (022/023/027/028 each show one or
  more 0.1001s = 3-frame gaps, the classic dropped-frame signature; 026 has
  none), and keyframe/GOP interval (steady 2.002s throughout, matching 027).
  Conclusion: clip 026 is one of the CLEANEST clips in the shoot on every axis
  checked — the "odd playback" is almost certainly the already-fixed missing-
  WAV silence, not a new defect.

  **Clip-split detection (`clip_model.py`, `merge_tab.py`).** New
  `detect_clip_splits(clips) -> [(clip_a, clip_b), …]`: clip_a has a WAV,
  clip_b doesn't, the two are time-adjacent (creation_time gap ≤ 5s, falling
  back to filename_ts when creation_time is missing on either), and clip_a's
  WAV duration is within 3s of clip_a's own video PLUS clip_b's video combined
  — requires all three signals together, deliberately, so a long WAV alone
  never triggers a false positive. `MergeTab._populate_table` recomputes this
  every rebuild (cheap) and inserts an inline banner row (`_add_split_banner_
  row`, `setFirstColumnSpanned`) right under clip_a with a "Review & resolve…"
  button opening `_ClipSplitDialog` — explanation + a stretch-factor-based
  two-segment timeline bar + three options (Split and pair each half
  [recommended] / Leave as-is / Don't ask about this pair again). "Split"
  (`MergeTab._resolve_clip_split`) does a PLAIN FFMPEG TRIM (`-ss
  clip_a.duration -c copy`) of clip_a's WAV into a new physical file
  (`<clip_b stem>_backup.wav`) and assigns it as clip_b's own WAV — a real,
  independent file on disk, not a shared-file-offset model, so every existing
  WAV mechanism (sync analysis, verify, extract) keeps working on clip_b
  completely unchanged, as if it always had its own recording. Dismissed
  pairs are tracked in-memory only (`{(path, path)}` set, ClipInfo isn't
  hashable) — no persistence across a fresh folder load, deliberately simple.
  Tests: `test_clip_model.py` (+6: the real-shoot pattern, adjacency
  required, duration-match required, skips when either has-WAV condition is
  wrong, filename_ts fallback); new `test_clip_split_ui.py` (+5: banner
  surfaces/hides a detected pair, dismissal persists across a repopulate, the
  dialog's default/radio-selection behaviour, a defensive no-crash check when
  clip_a unexpectedly has no WAV). (One unrelated finding along the way: bare
  `MergeTab(Settings())` construction in a short-lived offscreen script exits
  with a nonzero code from Qt's own background-thread teardown, reproducing
  with zero clips/zero custom code — pre-existing, not a regression; the new
  test file calls `os._exit(0)` after its assertions finish so a sweep script
  reads it by test results, not that artifact.)

  **WAV Duration column (`merge_tab.py`).** New `COL_WAV_DUR` next to the
  existing WAV column, `_fmt_dur(clip.wav_duration)` (already probed/tracked
  on `ClipInfo`, just never surfaced) — the numbers that make the clip-split
  pattern visible to a user scanning the table, not just the dialog.

  **Per-clip video-source override + LRV proxy pairing (`clip_model.py`,
  `core/ffmpeg_cmd.py`, `ffmpeg_runner.py`, `merge_tab.py`).** New
  `ClipInfo.video_source_override` ("auto"/"transcode"/"lrv") and
  `ClipInfo.effective_status()` — `clip.status` (the raw probed value) forced
  to "transcode" when the override requests it (for "lrv", only when
  `has_lrv()` — otherwise falls back to Auto rather than forcing a pointless
  same-spec re-encode). `core/ffmpeg_cmd.py`'s `build_mux_cmd_plan` and
  `ffmpeg_runner.py`'s manifest-building/progress-label/GPU-request call sites
  now read `effective_status()` instead of `.status` — so a manual override
  reuses the EXACT SAME conform+archival machinery a genuine spec mismatch
  already goes through (byte-exact original preserved on its own track via
  the existing odd-spec archival system), no new merge path invented.
  `video_source_override == "lrv"` additionally swaps the actual video INPUT:
  a second `-i {lrv_path}` is added, video maps from that input (always via
  `-filter_complex` with an explicit `[{n}:v:0]` reference, never the `-vf`
  shorthand, which silently ignores `-map` and would pick the wrong input),
  camera AUDIO still comes from the clip's own file (the proxy carries its
  own unwanted audio track too) — and the output is cut to the clip's own
  true duration (`-t`) since a real proxy's own duration rarely matches its
  paired 4K file's to the millisecond (confirmed directly: ~0.3s apart).
  `transcode_vf_parts` gains optional `src_width`/`src_height` overrides —
  `clip.conflicts` describes the CLIP's own spec and says nothing about a
  proxy's, so scale/pad is applied unconditionally when an override is given
  (a harmless no-op if the dimensions already happen to match).
  New `clip_model._pair_lrv` (mirrors `_pair_wav`'s exact/prefix + cross-brand
  `_clip_key` cascade) wired into `scan_folder`, populating `ClipInfo.lrv_path`
  from any `.lrv` sidecar in the source folder.
  New "Video source for {clip}" dialog (`_ClipVideoOptionsDialog`, opened by
  clicking a clip's Status badge — `_make_status_badge` replaced by a real
  `QPushButton` styled identically, `_make_status_button`, reflecting
  `effective_status()` so an overridden badge matches what the merge will
  actually do): Auto / Force transcode / Use the LRV proxy instead (only
  offered when `has_lrv()`), styled as the same accent-bordered radio cards
  used throughout this session's other resolution dialogs.
  Tests: `test_clip_model.py` (+7: effective_status's 4 combinations
  including the lrv-without-a-proxy fallback, has_lrv, 3 scan_folder pairing
  cases); `test_ffmpeg_cmd.py` (+9: transcode override forces a re-encode on
  an otherwise-matching clip, Auto is unaffected, lrv override maps video
  from the second input via filter_complex + applies the duration cutoff +
  scales using the PROXY's own dimensions, lrv override without a paired
  proxy falls back to Auto, 3 `transcode_vf_parts` override-parameter cases);
  new `test_clip_video_options_ui.py` (+6: the dialog offers only Auto/
  Force-transcode without an LRV vs. all three with one, defaults to the
  clip's current override, reports the selected radio + preserve-checkbox
  state, the checkbox reflects the clip's existing value, applying choices
  through the Status-badge click path).

  **Opt-in LRV preservation (`core/verify.py`, `core/ffmpeg_cmd.py`,
  `core/manifest.py`, `ffmpeg_runner.py`, `core/extract.py`,
  `extract_workers.py`, `whatsapp_tab.py`).** `ClipInfo.preserve_lrv`
  (default OFF, same reasoning as Task 94's `preserve_wav_full`) — an
  independent opt-in surfaced in the SAME `_ClipVideoOptionsDialog` (only
  shown when `has_lrv()`), combinable with any of the three video-source
  choices. New `build_lrv_archival_mux_cmd` — the video analogue of Task 95's
  `build_wav_archival_mux_cmd`: appends each requested clip's low-res proxy
  (video + its own audio, `-c copy`, both explicitly non-default) as
  standalone tracks onto an already-finished master, existing video/audio
  stream counts supplied by the caller (new `probe_video_stream_count`,
  mirroring `probe_audio_stream_count`) so it layers correctly regardless of
  what else (video archival, preserved WAVs) already ran. New
  `MergeWorker._append_preserved_lrvs`, called in `run()` right after
  `_append_preserved_wavs` (so it's always counting streams AFTER any WAVs
  already landed) — same "runs regardless of whether Archival master is on"
  independence. `ClipEntry.lrv_video_archival_track`/`lrv_audio_archival_track`
  record the preserved proxy's own stream indices in the manifest;
  `RecoveryPlan` + new `build_recover_lrv_archival_cmd` (plain stream copy,
  audio map optional for a video-only proxy) thread it through
  `core/extract.py`; `ExtractWorker.run()` recovers it automatically
  alongside video/camera-audio/WAV-backup/preserved-WAV (to
  `"{stem} (LRV proxy).mov"`, non-fatal on failure like the other optional
  steps); the Extract tab's "Recovers as" column mentions it upfront.
  Tests: `test_ffmpeg_cmd.py` (+1: stream mapping/disposition for 2 proxies);
  `test_verify.py` (+2: `probe_video_stream_count` counts/error-handles);
  `test_extract.py` (+3: RecoveryPlan carries/omits the archival-track
  fields, the recovery command maps video+audio or video-only correctly).

  42 test files green.

### Task 98 — Battle-test campaign, Exhibit A: audio-only export crash + recovery/verify gaps (fixed)

  Real user report: Advanced output → video unchecked ("audio-only export") failed
  during merge, and no crash log appeared in `failure_logs\`. Reproduced headlessly
  against 2 real clips (a fresh `MergeTab` driven exactly like `tests/md5_matrix_
  test.py`, `_include_video = False`, real ffmpeg, no mocking) rather than guessed
  at from reading code.

  **The crash (`core/ffmpeg_cmd.py`, `ffmpeg_runner.py`).**
  `build_final_archival_mux_cmd` mapped the baseline's video with a bare `"-map",
  "0:v"` — fine when the baseline always has a video stream, but an audio-only
  baseline has NONE, and ffmpeg hard-errors ("Stream map '' matches no streams")
  the moment Archival master is also on (which auto-selects on for a mixed-spec
  folder, as happened here). Mirrors the exact reasoning the function already
  applied to `"0:a?"` two lines above (a baseline with every audio track disabled
  has zero audio streams) — just never extended to video. Fixed with a new
  `base_has_video` parameter: `"0:v?"` when False, and the `-disposition:v:N`
  assignment recomputed from actual output stream positions instead of assuming
  the baseline always occupies v:0 (every archival video's output index shifts
  down by one when the baseline contributes none). `_build_and_mux_archival`
  passes `base_has_video=bool(base_video_count)` (already computed from
  `self._plan.include_video` for the manifest's own bookkeeping — this was the
  ONE caller not yet using it).

  **Recovery/verify never anticipated a video-less baseline either
  (`core/manifest.py`, `core/extract.py`, `ffmpeg_runner.py`).** Even after the
  crash fix, MD5 verify's Video/Rotation/Metadata checks and the real Extract-
  and-Recover feature would still try to map a video stream that doesn't exist —
  `build_recovery_plan` unconditionally set `video_stream = 0` for any clip
  without its own archival track. New `Manifest.baseline_has_video` (defaults
  True — every manifest written before this field existed always had video, no
  migration needed), set from `self._plan.include_video` alongside the sibling
  `baseline_audio_tracks` assignment. `RecoveryPlan.video_stream` is now
  `Optional[int]` — `None` only when BOTH the baseline has no video AND this
  clip has no archival track of its own (a clip with its own archival track
  still recovers real, preserved video even when the delivered baseline has
  none — archival preservation doesn't depend on the baseline). `build_recover_
  clip_cmd`/`build_preview_sample_cmd` skip the video map entirely when `None`
  instead of emitting a doomed `0:v:N`; this is the SAME command real Extract-
  and-Recover uses, so a user extracting from an audio-only master hit the
  identical crash, not just this app's own verify pass. Verify's Video/Rotation
  checks now report a clean `skipped_reason` ("not applicable — this master was
  exported without video") instead of leaking the raw ffmpeg parse error caught
  by `full_video_check`'s own try/except.

  **The missing failure log.** Root cause not the same failure this session's
  repro reproduced (that one DID leave a log — verified directly), but a real,
  separate gap found by reading `log_manager.log_merge`/`merge_tab._on_finished`
  together: `_on_finished` wraps the ENTIRE call to `log_manager.log_merge` —
  which builds a rich per-clip breakdown (`analyze_clip`, sync/offset fields,
  `_effective_plan()`) — in a bare `except Exception: pass`. Any exception
  anywhere in that enrichment used to raise straight out of `log_merge` BEFORE
  `_append()` (and therefore `_write_failure_txt()`) ever ran, so a failure log
  is entirely at the mercy of code that has nothing to do with logging. Hardened
  both layers: `log_merge` now wraps its own enrichment in a try/except and
  falls back to a thin-but-real entry (still calls `_append()`) on any failure;
  `_on_finished` now builds `plan`/`mix` in their own inner try/except so a
  failure there doesn't skip the `log_merge` call altogether. Belt-and-braces —
  either layer alone would have caught the class of bug that was found.

  **Test-harness bug found along the way (`tests/md5_matrix_test.py`).** While
  confirming the fix end-to-end, every SUCCESSFUL headless merge with MD5 verify
  on hung indefinitely after verification finished — `_on_finished`'s "Merge
  complete!" dialog constructs a raw `QMessageBox(self)` instance and calls
  `.exec()` directly, which the harness's existing dialog-safety patching
  (`QMessageBox.warning`/`.information`/`.question` classmethods,
  `_CameraNamingDialog.exec`) never covered — a modal `.exec()` blocks forever
  offscreen with nothing to click it. This means every historical matrix run's
  PASSING configs were likely silently eating the full per-test timeout instead
  of finishing in seconds, then getting recorded as `"timeout"` — probably the
  real explanation for slow/inconclusive past matrix runs, not the merges
  themselves being slow. Fixed with `mt_mod.QMessageBox.exec = lambda self: 0`
  alongside the existing `_CameraNamingDialog.exec` patch.

  **New finding, not fixed (needs its own investigation): WAV-backup MD5
  mismatch.** Both test clips' WAV-backup track failed MD5 verify with
  "Both sides decoded cleanly — worth a closer look at this clip's seam" —
  confirmed via a control run (same 2 clips, video included, otherwise
  identical settings) that this is unrelated to the audio-only work above; it
  reproduces identically either way. Flagged for a dedicated follow-up rather
  than folded into this fix, since Exhibit A's own scope is the crash/logging,
  and this is a different code path (WAV backup recovery windowing) with its
  own honest "worth a closer look" diagnosis already built in.

  Tests: `test_ffmpeg_cmd.py` (+2: `"0:v?"` map + disposition renumbering when
  `base_has_video=False`, with/without archival video streams present);
  `test_extract.py` (+5: `video_stream` is `None` only for the no-archival-
  track case, still set for a clip with its own archival track, `build_recover_
  clip_cmd`/`build_preview_sample_cmd` omit the video map, `baseline_has_video`
  round-trips through JSON and defaults `True` for old manifests missing the
  key); `test_log_manager.py` (+1: a clip whose `.duration` raises still
  produces a real, appended failure entry, not a swallowed exception).

  42 test files green.
