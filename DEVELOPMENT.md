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
