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

- **"WhatsApp clip" → "Extract and Share"**: rename + extend the tab to also extract
  individual chapters back out of a master into standalone MP4 clips + WAV files — the
  merge process in reverse. Keeps the existing 720p colour-graded share-clip export.
- **Metadata preservation**: a dedicated conversation is wanted on what metadata the app
  should read/write/preserve and why it matters (e.g. audio-track title tags — see the
  "SoundHandler" finding in the Review-tab playback work below: masters currently carry no
  descriptive per-track titles, so nothing — Qt, another player, or a future "Extract and
  Share" — can label a track from the file itself). Ties into "labels" more generally;
  revisit once that conversation has happened.
- **Build history in the About tab**: a section at the bottom of the About tab listing
  what was built, in what order, and bug fixes along the way — presumably sourced from
  git log / commit messages or a maintained changelog. Needs design discussion (what
  granularity, per-version vs per-change, how it's generated/kept in sync) before scoping.

## App-wide + merge-tab refinement backlog (for a later action-plan discussion)

Observations from the user while using the app on their desktop — captured for later, not
acted on yet.

1. **Window doesn't rescale smartly — parts become inaccessible.** At smaller window sizes,
   content is cut off left/right with no way to reach it (vertical scroll exists, horizontal
   doesn't / layout doesn't reflow). Wanted: smart rescaling so the UI adapts to window size
   and every area stays reachable regardless of size — likely a mix of responsive layout
   (wrapping/collapsing panels) and ensuring scroll areas cover both axes where content can't
   shrink further, rather than adding horizontal scrollbars everywhere as a band-aid. Needs a
   proper pass across the app's tabs (Merge, Review, Extract/WhatsApp), not just one screen.
2. **Merge table: add a selection checkbox column.** Only ticked clips are included in the
   merge; unticked clips fade (reduced opacity/muted) as a visual cue they're excluded rather
   than being removed from the list. Useful for excluding a bad take without deleting/moving
   the file out of the source folder.
3. **Merge table: add a Timestamp column** between Clip name and Camera, showing the clip's
   position in the linear (chronological) order. **Highlight when it differs from the
   filename's own timestamp**, with a reason shown (e.g. "creation_time used — filename
   suggests DST offset" / "reordered manually"). This makes the Phase-1 creation-time
   ordering fix (which can silently reorder clips relative to filename expectations)
   visible and explainable in the UI, not just correct under the hood.

## Review-tab design refinement backlog (for a later action-plan discussion)

The Review tab's UX pass + polish are shipped; these are *next-level* design-language
refinements captured from a critique, to be turned into an action plan later — none urgent.

1. **Accent is doing too many jobs.** The amber accent currently marks section titles,
   selected toggles, the primary action (camera), and the viewport box (playhead is gold).
   A non-interactive label (section title) wearing the "active/clickable" colour dilutes the
   accent's signal. Proposal: mute section titles to `text_mute`, reserve accent strictly
   for interactive/active states (extends the theme-pass accent-scope discipline).
2. **Transport row conflates three control types.** Navigation (skip/step/play/step/skip),
   the jog *shuttle*, and the snapshot *action* sit evenly in one line. Add a divider/spacing
   to group by function — the camera especially is an action, not transport; also reinforces
   that chapter-skip vs frame-step are different (they're only subtly different icons).
3. **Scopes column is the tightest spot.** Badges + two toggle groups + the approx flag +
   a short (~96px) canvas packed into the narrow 2/5 column. Give the Parade/RGB sub-toggle
   a clearer hierarchy (it's a *style* of Waveform, not a peer of Histogram — segmented
   control or a "Waveform style:" label), and let the scope canvas breathe taller.
4. **Overview is near its density ceiling.** Envelope + viewport + playhead + ruler already
   share a short strip. When the Phase-2 thumbnail filmstrip lands, restructure this into a
   proper multi-row timeline (ruler / thumbnails / waveform) rather than cramming a fourth
   layer in. Design the restructure deliberately with the proxy/thumbnail work.
5. **Status line is a shared transient slot.** Snapshot confirmations, load progress, and
   errors all flash through one muted line. A successful snapshot save is worth a beat more
   prominence — e.g. briefly tint it `ok` green — so the user gets clear action feedback.

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
  - **Remaining: 3b** grouped view under editable camera headers + drag clips between groups
    to reassign; **3c** click-to-assign for unmatched WAVs. (Both are heavy merge_tab UI work.)

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

### Review-tab integration (Phase 2)

The multi-video-track master serves **both** recovery and review. Alongside the archival
original tracks, Phase 2 can add a small **review proxy track** (e.g. 960×540 H.264,
**opt-in per merge** via a merge-tab checkbox — decided with the user). Because it's a tiny
track the GPU can decode trivially, the Review tab can play it for **smooth, crash-free
playback** (upgrading today's `HybridPlaybackEngine` slideshow), and the **thumbnail
filmstrip** can be built cheaply from it instead of decoding the 4K master. The proxy is a
*distinct* track from the archival originals (proxy = tiny playback stand-in; archival =
full-quality originals for recovery), but both ride the same multi-track plumbing. The
timeline **timestamp ruler + clip markers** come from chapters/manifest the master already
carries. The four *standalone* Review polish items (accent-drawn snapshot camera, timestamp
ruler, scroll/pinch preview zoom, audio-lanes crop-to-viewport) are independent of all this
and are being done first.

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
