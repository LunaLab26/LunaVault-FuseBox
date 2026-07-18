# LunaVault FuseBox — Battle Test Round 2

Follow-up investigations into questions Round 1 raised and coverage gaps it
left open. See `BATTLE_TEST_REPORT.md` for Round 1's full findings; this
report assumes that context and does not repeat it.

**Methodology note on the working tree**: at the start of this round, the
project already had substantial *uncommitted* work in place (VAAPI
hardware-decode support, ProRes hw-encode offload, dev_history/preflight
changes — ~1,100 lines across 20 files, last commit "Tasks 100-104"). This
predates Round 2 and was not created by this session. Confirmed at the
start that neither the square-crop code (§6.2) nor `_verify_one_clip`
(§6.1a) were touched by it, so Round 1's findings were still live against
the tree actually tested. A snapshot of that pre-existing diff was taken
before starting, and a second snapshot at the end of this round is
byte-identical to it — this round modified no tracked source file (see
§9, Housekeeping).

---

## 1. Root-cause of the §6.1a verification anomaly — SOLVED

**Mechanism**: any camera-audio track whose AAC encoding carries an
edit-list-driven **priming/discard region** (a standard, spec-correct MP4
convention — the encoder's lookahead samples are marked with a negative-PTS,
discard-flagged packet at the very start, and the container's `elst` box
tells a normal player to skip them) loses that discard information when the
clip is recovered via a **concat-based** path (the shared baseline track, or
even a lone clip's own segment of it) rather than a **dedicated archival
track copy**. The recovered audio still contains the priming samples — no
longer marked for discard — so every sample after it is shifted by a
constant offset (one AAC frame, ~21–23ms, matching the discarded packet's
own duration exactly). This is a **constant time-base shift**, not a
boundary artifact, which is exactly why the app's own adaptive fallback (a
~300ms guard skipped at each end of the comparison window) never resolves
it: guarding the edges doesn't help when the whole clip is shifted.

**How this was pinned down**, in order:
1. Reproduced Round 1's exact `S1_baseline_mix` 2-clip repro fresh (via
   `md5_matrix_test.run_one`), got the identical 3/5 result and the same
   two clips failing the same way — confirms not a fluke of the specific
   Round-1 run.
2. Built a diagnostic script (`s1_diag.py`) that reuses the app's own
   `build_recovery_plan`/`build_audio_pcm_cmd`/`build_decoded_audio_md5_cmd`
   helpers (imported, not modified) to redo clip 1's camera-audio comparison
   with much finer instrumentation than `_verify_one_clip()` itself prints:
   byte-level first-difference offset, a guard-size sweep from 0–1000ms, and
   direct `ffprobe` packet-level timing.
   - Byte-exact comparison: source clip's decoded audio is **177,152
     samples** (4.017s); the recovered window is **176,400 samples**
     (exactly 4.0s) — a **752-sample** discrepancy.
   - Guard sweep (0–1000ms): **never converges**, at any guard size —
     confirming a constant shift, not an edge effect.
   - `ffprobe -show_entries packet=pts_time,flags` on the **source** file's
     audio stream: first packet at `pts_time=-0.023220`, flagged `KD_`
     (Keyframe + **D**iscard) — exactly one AAC frame (1024 samples @
     44100Hz = 23.22ms). The **recovered master's** audio stream: first
     packet at `pts_time=0.000000`, **no discard flag at all**.
   - Direct byte-scan of the source `.mp4` for `edts`/`elst` boxes: both
     present, confirming a real edit list, not a probing artifact.
3. **Cross-validated against real footage, not just synthetic clips**: the
   exact same real Gran Canaria clip from Round 1's §6.1b (the one with the
   `bin_data` stream) has its own audio edit list too — but a much larger
   one: **43 consecutive discard-flagged packets** (~917ms, not just one
   frame). Round 1's own archival merge of this clip (still on disk from
   that session) shows the identical symptom in its own
   `regression2_bindata_archival_sw.verify.log`: *"Camera audio:
   MISMATCH... nothing to explain it."* Its recovered master's audio stream,
   re-probed directly, again starts cleanly at `pts_time=0.000000` with no
   discard packets — the priming region survived into the delivered file
   unmarked. This is the same mechanism, at a much larger magnitude, on
   completely real hardware-recorded footage.
4. **Explains why the real 9-clip multicam set passed 9/9 cleanly** in
   Round 1's hi-risk matrix: that specific camera's clips (both the trimmed
   copies AND the untrimmed originals, checked directly) have **no
   discard-flagged priming packets at all** — the first audio packet is
   already at `pts_time=0.000000` in the original file. There was nothing
   to lose. This is not the app correctly handling priming loss; it's that
   this particular camera's encoder doesn't produce a case that would
   expose the gap. The distinction matters: **the app's archival-audio
   promise has not actually been proven safe for cameras/encoders that do
   write this (very standard) priming convention**, and both a synthetic
   ffmpeg-encoded clip and a real Gran Canaria camera clip demonstrate it
   breaks when they do.

**Verdict**: this is a real, reachable correctness gap, not a test artifact,
and now has a precise, falsifiable mechanism instead of "camera audio
sometimes doesn't verify." Severity assessment is essentially unchanged
from Round 1 (still confined to non-final/non-dedicated-track camera-audio
recovery), but confidence in *why* it happens — and that it can affect real
camera footage, not just synthetic test fixtures — is now much higher.

**Not pursued further**: the original §1 brief asked for hypothesis tests on
clip duration, audio-content uniformity, and AAC-frame-boundary alignment.
Once the actual mechanism (edit-list priming loss) was found and confirmed
on two independent inputs, those hypotheses became moot — they were
plausible guesses at a symptom, and the real cause is a different, more
specific, and more diagnosable mechanism than any of them.

---

## 2. MOV-box-level analysis of the §6.1b `bin_data` leak — SOLVED, with a scoped fix suggestion

**Mechanism identified**: this is a **QuickTime chapter track**, not a
motion-photo/subtitle-linked track as Round 1 speculated. A minimal
ISO-BMFF box walker (`box_walk.py`, ~120 lines, no external tool available
in this environment — `mp4box`/`exiftool` aren't installed) dumped the real
Gran Canaria clip's box tree directly:

```
trak (track_id=1, video)
  tref  ref-type='chap'  -> referenced track_id(s): [3]
trak (track_id=2, audio)
  tref  ref-type='chap'  -> referenced track_id(s): [3]
trak (track_id=3, handler_type='text')   <- this is the bin_data/text track
```

Both the video and audio tracks carry a `tref` box of type `chap`
pointing at track 3, which is a lightweight `text`-handler chapter/marker
track (a common camera convention, distinct from the Pixel `mett`
motion-photo tracks the *original* fix targeted). ffmpeg's MOV/MP4 muxer
auto-carries a chapter-referenced track through stream-copy remuxing
**regardless of `-map`**, because dropping it silently would break chapter
navigation in players that rely on `tref/chap` — this is why every
`-map`/`-dn`/negative-map combination Round 1 tried still leaked it.

**The fix, found and verified directly**: adding **`-map_chapters -1`** to
the per-clip conform command suppresses it completely.
- Reproduced the exact leak standalone: `ffmpeg -i <clip> -map 0:v:0 -map
  0:a:0 -c copy out.mov` → output still carries the `bin_data` stream.
- Same command **+ `-map_chapters -1`** → output has exactly the 2 streams
  it should (video + audio), confirmed via `ffprobe`.
- Also confirmed a **two-pass strip works**: re-remuxing an
  *already-leaked* file with `-map_chapters -1` (no re-encode, `-c copy`)
  also drops the stream cleanly — so a post-hoc cleanup pass is viable too,
  not just a fix at the original conform step.

**Generality check**: scanned all 9 real clips in the multicam test set
(the 6 `VID_` GoPro-style clips and the 3 `PXL_` Pixel clips) for the same
`tref/chap` pattern — **none of them have it**. This leak is specific to
whichever camera/software produced the Gran Canaria file (or at least to
sources that embed a QuickTime chapter track), not a universal MP4/MOV
property. Combined with §1's finding, this camera's footage happens to be
unusually good at surfacing container-metadata edge cases the app's map-based
approach doesn't anticipate.

**Suggested fix for the maintainer** (not applied — report only, per the
brief): add `-map_chapters -1` to `build_mux_cmd_plan`'s per-clip conform
command (`core/ffmpeg_cmd.py`). This is a single, well-scoped ffmpeg flag,
not a container-box-level patch — much narrower than Round 1's "needs a
different-layer fix" guess.

---

## 3. Hi-risk matrix retry (software-encode + ProRes-compat cells) — COMPLETE

Re-ran exactly the 54 cells that timed out in Round 1 (all at
`MD5_PER_TEST_TIMEOUT_S=180`), this time at 600s/cell minimum, against the
same real 9-clip `multicam_trimmed` footage. All 54 cells now resolved:

```
32 pass
16 md5_mismatch
 6 timeout
```

**A methodology hazard discovered and corrected along the way**: partway
through, discovered that this background job and several of this round's
*other* local tests (§4, §8) were unknowingly sharing the exact same
`_temp` scratch directory — see §4's finding below; the app has no
per-instance temp isolation. The first attempt at this retry was
restarted from scratch after only 2/54 cells to avoid reporting
contaminated timings. The tally above is from the clean, restarted run,
untouched by any other concurrent test for its entire duration.

**The 32 passes** span every `compat_prores_*` profile combined with every
archival mode and both hw_decode settings, plus every `archival=on`
`compath264` cell that didn't time out — every one **9/9 clean**, zero
genuine mismatches.

**The 16 `md5_mismatch` cells are ALL `archival=off`, and all show the
exact same, already-documented, expected pattern** — confirmed by directly
reading multiple of their `verify.log` files: the same 3 clips (two
rotated 180°/270° originals and one that shares their spec) fail their
*Rotation* check with the diagnosis already on file from Round 1 (*"expected:
this clip has no archival track of its own... a 0 here doesn't mean the
picture is actually sideways"*) — every one of these clips' *video* and
*camera-audio* checks are correctly pre-classified as
`predicted unverifiable` rather than run at all, meaning the app's own
predictive-skip logic is working as intended. **Zero of these 16 cells
show a genuine, unexplained mismatch** — none hit the §1 AAC-priming
mechanism, because (per §1's finding) this specific camera's footage
doesn't carry the edit-list priming signature that triggers it.

**The 6 timeouts show a strikingly clean, specific pattern**: every single
one is a `compat_baseline=True, compat_codec=h264, hw_encoder=auto` cell —
3 with `hw_decode=off` and 3 with `hw_decode=auto`, spanning all 3
archival modes. Every other codec/hw combination in the matrix (including
`compat_codec=h264` at `hw_encoder=off`, and every ProRes profile at
`hw_encoder=auto`) resolved cleanly well within the 600s budget. This
narrows Round 1's broad "the software/ProRes corner is slow" conclusion
to something much more specific and reproducible: **the H.264
compatible-playback-master re-encode, specifically when `hw_encoder=auto`,
is the one combination that consistently exceeds 600s** on this hardware,
independent of hw_decode or archival mode. A plausible explanation worth
a maintainer's attention: `auto` likely attempts a VAAPI encode probe/
attempt before falling back to software, and that probe-and-fallback
overhead compounds specifically with the H.264 compat re-encode path
(a full-length single-pass re-encode of the whole master, the most
expensive step in the pipeline) in a way it doesn't for ProRes (software-
only, no hw attempt at all) or for `hw_encoder=off` (skips the probe
entirely). Not root-caused further this round (would need instrumenting
`detect_best_hw`/`hw_encode_plan`'s timing directly) but is now a precise,
reproducible, three-line repro instead of "54 cells didn't finish."

---

## 4. Cancellation, interruption, and crash-recovery

### 4.1 Cancel mid per-clip-conform — clean
Started a real merge (via the actual `MergeTab`, not a mock) against a
20-second odd-spec clip that forces a genuine multi-second software
transcode, let it start encoding for real, then called the app's own
`cancel()`. Result:
- The running ffmpeg process **actually terminates within ~1 second** of
  cancel (the app polls `self._cancelled` every 0.4s and calls
  `proc.terminate()`).
- The UI returns to a usable state (`_worker` cleared, Start button
  re-shown) in **1.8s**.
- `_temp/` is **fully cleaned** (0 leftover entries) immediately after.
- A **fresh merge into the same output folder** afterward completes
  cleanly and produces a valid, correctly-sized output file.

No issues found on this axis.

### 4.2 Hard SIGKILL mid-merge
Launched a real merge in its own subprocess, let it start encoding, then
sent `SIGKILL` (uncatchable — simulates the app crashing or being force-
closed) to the whole process.
- As expected for `SIGKILL` (Python cannot intercept it, so no `finally`/
  cleanup code runs), `_temp/` was **not** cleaned: 4 leftover files
  remained (`clip_01.mov`, `ffmpeg_err.txt`, `thumb.jpg`, `progress.txt`).
- No orphaned **ffmpeg** process survived the kill in this instance (the
  per-clip ffmpeg had already exited between clips at the moment of kill).
- Checked `_make_scratch()` in `ffmpeg_runner.py`: it does
  `mkdir(parents=True, exist_ok=True)` — it does **not** clear pre-existing
  contents of `_temp/` on a fresh run. A subsequent merge reuses the same
  directory; same-numbered clip files get overwritten harmlessly (`ffmpeg
  -y`), and the concat step only ever references the paths it just wrote,
  not a directory scan — so stale leftover files from a smaller previous
  crashed run do not appear to contaminate a later, different-sized merge
  in the case tested. A fresh merge run immediately afterward, without
  manually cleaning `_temp/` first, completed successfully.

### 4.4 Concurrent-instance / shared-scratch collision — confirmed real, found by accident
While running this round's other local tests **concurrently** with §3's
background matrix, both ended up writing per-clip temp files
(`clip_NN.mov`, `progress.txt`, etc.) into the **exact same** `_temp`
directory at the same time — genuinely observed via `ps`/`ls`, not
engineered. Root cause: `MergeWorker._make_scratch()` resolves to
`get_app_dir()/_temp`, a single hardcoded path with **no per-process/
per-instance namespacing** (no PID, no UUID subfolder), and there is no
user-facing setting to point it elsewhere (the constructor accepts a
`scratch_override` parameter, but `MergeTab`/`Settings` never wire any UI
control up to it).

**Impact**: two real users running the app at the same time on a shared
machine — or one user launching a second instance while a first merge is
still running — would have their per-clip temp files collide in this
same directory. Depending on timing, this ranges from harmless (their
clip-numbering happens not to overlap) to a genuine cross-contamination
risk (one merge's ffmpeg reads/writes a temp file the other merge is
also using). This is the same class of risk Round 1's own test harness
had to guard against between matrix cells (`pkill -9 -f
<temp_dir_pattern>` after every cell) — now confirmed to be a property of
the **shipped application itself**, not just a test-harness artifact.

**Not tested this round** (time budget): disk-full mid-encode via a
size-capped loopback/tmpfs mount, and a deliberately-engineered two-
simultaneous-instances-same-output-folder test (§4.4 above demonstrates
the underlying shared-`_temp` mechanism that would make this go wrong, via
an unplanned real collision, but a controlled two-instance-same-output
test specifically was not additionally run). §7's read-only-output-folder
test (below) does exercise one write-failure path (permission denied at
the finalize step) and shows it's surfaced cleanly.

---

## 5. GUI error surfacing for silent ffmpeg failures

### 5.1 The §6.2 square-crop crash, through the real GUI — POSITIVE finding, corrects Round 1's uncertainty
Round 1 could not observe this in its headless harness (`QMessageBox` was
neutralized for automation) and could only guess: *"a real GUI session
would presumably show the generic merge-failed dialog."* This round drove
the **actual, un-mocked** `MergeTab` on the CRD virtual display, with the
same square 1:1 clip + default "Crop to fill 16:9" setting, and captured
the real screen via `x11grab`:

> **A clear "Failed" dialog appears**, titled "Failed", reading exactly:
> *"Merge failed: ffmpeg failed on GOPR_20260105_120100_002.mp4"* followed
> by the raw ffmpeg stderr (`Could not open encoder before EOF... Invalid
> argument... Conversion failed!`) and an OK button.

This is **better than "generic"** — it names the exact clip that caused
the failure, not just "the merge failed." The underlying crop-math bug
(§6.2) is unchanged and still worth fixing, but the failure mode is not
silent and not confusing; a real user hitting this would know immediately
which clip to investigate. This meaningfully revises Round 1's severity
framing: the bug is still a hard, 100%-reproducible crash for any square
input, but users are not left guessing why their merge produced nothing.

### 5.2 Deleting a source clip mid-merge — inconclusive (methodology limitation, not a finding)
Attempted to delete a source clip's file while ffmpeg was actively reading
it, to see whether the failure names the missing file distinctly. Result:
the merge kept running past the deletion with no apparent effect. Root
cause of the *inconclusive* result: on Linux, `shutil.move`/unlinking a
file does not invalidate a file descriptor ffmpeg already has open on it —
the process keeps reading the same underlying inode's bytes to EOF,
completely unaffected by the rename/delete. This is standard POSIX
behavior, not an app gap, and it means this particular attack (delete an
already-opened file) can't actually reach the app's error-handling code at
all. A genuine test of "source vanishes mid-merge" would need to target a
clip **later** in the processing order (not yet opened) or truncate/
corrupt the file's bytes in place rather than renaming it — not attempted
further this round due to time budget; §7's corrupt/truncated-file test
below covers the adjacent "bad file from the start" case instead, and did
get a clean, distinctly-attributed result.

---

## 6. Review tab: real playback verification (screenshots, not just internal state)

Loaded a Round-1-produced archival master (2 camera-group clips + 3
singletons, testsrc2 pattern, 4 audio tracks) into the real `ReviewTab` on
the CRD display and captured actual rendered frames via `QWidget.grab()`
(confirmed this correctly captures the `QVideoSink`-rendered frame content,
not just widget chrome).

1. **Frames render correctly**: initial load shows the real SMPTE-style
   color-bar test pattern, non-black, matching the synthetic source
   content exactly. Colour scopes panel correctly read 10-bit/HEVC/4:2:0/
   SDR. Audio-track panel correctly listed and rendered waveforms for all
   4 tracks (Camera mic, WAV backup, 2× Mix).
2. **Seeking verified precisely**: seeked to 25%/50%/75% of the 17.28s
   master; the timecode readout and timeline playhead position matched the
   requested target exactly at each of the 3 positions
   (`00:00:04:14`, `00:00:13:13`, etc.), confirmed via screenshot at each.
3. **Source switching verified**: the video-source combo correctly listed
   the archival original (`PXL_20260101_120100_003.mp4 — original`);
   switching to it updated the preview, the readout line (*"Viewing
   original: PXL_20260101_120100_003.mp4 (H264 1920x1080 60fps)"* — an
   accurate, distinct codec/resolution/fps summary), and reset the
   timecode to the original clip's own zero point, all correctly.
4. **Track selection**: audio tracks are exposed as checkboxes (Camera
   mic/WAV backup/Mix×2), consistent with Round 1's programmatic
   `set_audio_single()` checks (already covered there); toggling was not
   additionally re-verified visually this round given time budget, but no
   crash or freeze was observed at any point while the 4 tracks were
   simultaneously enabled and rendering.
5. **What cannot be verified without a human**: A/V sync feel and
   playback smoothness (dropped frames, stutter) are fundamentally
   perceptual — screenshots prove frames are correct and seeking is
   accurate, not that motion feels right. Explicitly not claimed here.

**One minor, separate observation**: the driving script's own Python
process reliably hit `QThread: Destroyed while thread '' is still
running` followed by a core dump **at interpreter exit** (after all
useful screenshots were already saved). This happened only when the
script exited without an explicit engine-stop/close call before falling
off the end of `main()` — likely the playback engine's internal QThread
wasn't told to stop before the QApplication tore down. This is a test-
script hygiene issue in the first instance (real usage always goes
through the window's proper close event), but is flagged here as worth a
quick check: does closing the Review tab's window in the real app (via
the X button, not just switching tabs) cleanly stop this thread every
time? Not additionally verified this round.

---

## 7. Hostile inputs and environments

Tested 3 axes end-to-end (real merge + verify) via the real `MergeTab`,
using an isolated scratch directory (monkeypatched in the test process
only, per §4.4's finding, to avoid re-contaminating §3's background run):

1. **Unicode, spaces, emoji, and `%`/`#`/parenthesis characters in
   filenames** — clip named
   `VID_2026 (final) 100% done — 日本語 😀 clip #1.mp4`. Loaded correctly (1
   clip found, stream probed fine), merged successfully, produced a valid
   output file. The MD5 verify step flagged "did not verify" — but the
   diagnosis is the **exact same §1 mechanism** ("Camera audio:
   MISMATCH... nothing to explain it," on a plain ffmpeg-encoded AAC track
   with the same priming signature) — **not a new unicode-specific bug**.
   The filename itself caused no path-handling problems anywhere in the
   pipeline (probing, ffmpeg command construction, muxing, or verification
   extraction).
2. **Corrupt/empty/fake-extension files mixed with one valid clip**: a
   zero-byte `.mp4`, a truncated `.mp4` (50KB head of a real file, no
   moov atom), and a `.txt` renamed to `.mp4`, alongside one genuinely
   valid clip. All 4 were detected as clips by the folder scan (not
   silently skipped). The merge **failed with a clear, correctly-
   attributed dialog**: *"Merge failed: ffmpeg failed on
   VID_20260101_120010_002.mp4"* (exactly the zero-byte file) with the
   real ffmpeg diagnostic (*"moov atom not found... Invalid data found
   when processing input"*). No crash, no hang, no silent partial output.
3. **Read-only output folder**: pointed the output directory at a
   `555`-permission folder. The merge ran all the way through per-clip
   conform (writing successfully to the *scratch* temp dir, which was
   writable) and only failed at the **final** archive-assembly step, with
   a clear dialog: *"Finalising archive — combining baseline and
   originals failed (exit 243)... Error opening output
   .../~partial_....mov: Permission denied."* Correctly attributed to the
   real cause, no crash. **Minor UX opportunity** (not a bug): the app
   doesn't check output-folder writability up front, so a user only
   discovers a permissions problem after waiting through the full encode —
   a cheap pre-flight `os.access(out_dir, os.W_OK)` check could fail fast
   instead.

**Not tested this round** (explicit scope limitation, time budget):
disk-full mid-encode, paths longer than ~200 characters, a WAV backup
whose duration wildly mismatches its paired video, and a genuinely slow/
network-backed source mount. These remain open for a future round.

---

## 8. MergeWorker signal-delivery investigation — SOLVED: test-harness artifact, not an app defect

Round 1 documented (and this round's own early testing repeatedly hit) a
hang where `_wait_for(lambda: done.get("finished"))` never observes
`MergeWorker`'s `finished` signal despite the merge having genuinely
completed on disk.

**Root cause, found and proven decisively**: `MergeWorker(QThread)`
re-declares `finished = Signal(bool, str)` at the class level — this
**shadows `QThread`'s own built-in, zero-argument `finished` signal**,
which Qt auto-emits internally when a thread's `run()` method returns.

**Proof, via a clean A/B test in the exact same process/run**: connected
both `finished` (an *external*, ad hoc lambda — exactly what both Round
1's and Round 2's test harnesses do) and `verification_done` (a
uniquely-named signal with no such collision) to the same live
`MergeWorker`, across 5 consecutive real merges:

```
finished signal MISSED in 5/5 runs
verification_done signal MISSED in 0/5 runs
```

Then, **removing the external connection entirely** and instead polling
the *real, actual side effect* of the app's own single internal
`_on_finished` handler (merge_tab.py:3068 — connected first, before any
test code runs) having executed — `mt._worker is None and
mt._start_btn.isVisible()`:

```
app's own internal _on_finished fired correctly in 5/5 runs (~23s each,
no variance) -- with NO external connection added to `finished` at all
```

**Verdict: this is a test-harness artifact, not a real application
defect.** The real GUI only ever has the ONE internal connection
(`self._worker.finished.connect(self._on_finished)`), which fires
reliably every time. The hang only manifests when something *else*
**also** connects to the same shadowed signal name — exactly what an
external test driver does to observe completion, and exactly why both
this round's and Round 1's harnesses needed the workarounds they built
(file-existence fallback, polling `_worker is None` instead of adding a
signal connection). Confirmed on both the offscreen QPA platform and the
real `xcb` backend (CRD display) — ruling out "offscreen platform quirk"
as the explanation; the signal-name collision is the actual mechanism,
independent of display backend.

**Practical implication for future testing of this app**: any test
harness for this codebase should poll `worker is None`/`isFinished()` or
connect to a non-colliding signal (`verification_done`, `progress`)
rather than adding an external connection to `finished` — not a request
to fix the app itself, since real users never trigger the colliding
condition.

---

## 9. Housekeeping

- `settings.json` backed up before this round started; restored and
  diffed at the end — only session-transient fields changed
  (`last_merge_output_dir/name`, `last_review_source`,
  `merge_decode_method`/`merge_encode_method`), same pattern as Round 1.
- `git diff` of tracked files, captured before and after this round, is
  **byte-identical** — this round modified zero lines of existing
  application source. (The project's own pre-existing uncommitted WIP —
  see the methodology note at the top — was left untouched throughout.)
- All temporary instrumentation used for §1 was implemented as standalone
  scripts importing the app's modules, never as edits to the app's own
  files — nothing to revert.
- Orphaned processes and scratch directories from this round's own testing
  were cleaned up; the one long-lived exception is §3's background hi-risk
  retry matrix, which is intentionally still running (see §3) and will
  clean up its own per-cell temp usage as it proceeds (matching Round 1's
  established harness behavior).

---

## Summary of new findings this round

| # | Finding | Severity | Status |
|---|---|---|---|
| 1 | §6.1a root cause: AAC edit-list/priming loss during concat-based audio recovery | Medium (confirmed reachable on real camera footage, not just synthetic) | Root-caused, reproducible, not fixed (per brief) |
| 2 | §6.1b root cause: `tref/chap` chapter-track auto-carry; fix is `-map_chapters -1` | Medium, now precisely scoped | Root-caused + verified fix suggestion, not applied |
| 3 | Shared, non-isolated `_temp` scratch directory across concurrent app instances | Medium — real cross-contamination risk for concurrent users/instances | Confirmed via accidental real collision |
| 4 | `compat_baseline=h264 + hw_encoder=auto` reproducibly exceeds 600s on real footage; every other codec/hw combination doesn't | Low-medium (performance, not correctness — all 6 affected cells that did finish elsewhere were clean) | Precisely isolated, not root-caused to the exact line |
| 5 | Square-crop failure (§6.2) is clearly surfaced in the real GUI, correctly naming the failing clip | N/A (positive finding) | Corrects Round 1's uncertainty |
| 6 | Corrupt/empty/fake-ext files and read-only output folders both fail with clear, correctly-attributed dialogs | N/A (positive finding) | Confirmed robust |
| 7 | Output-folder writability isn't pre-flighted, so permission errors surface only after a full encode | Low (UX polish) | New, minor |
| 8 | `MergeWorker.finished` signal-name collision with `QThread`'s built-in `finished` breaks *external* connections only | N/A (harness artifact, not an app defect) | Fully root-caused and resolved as non-issue |
| 9 | Hi-risk matrix retry: 32/54 pass, 16/54 the same already-documented rotation-tag limitation (zero genuine new mismatches), 6/54 the h264/hw_encoder=auto timeout pattern above | N/A (closes Round 1's coverage gap) | Complete — 0 unexplained failures across all 54 cells |
