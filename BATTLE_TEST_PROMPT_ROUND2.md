# LunaVault FuseBox — Battle Test Round 2: Follow-up Investigations & Coverage Gaps

You are battle-testing LunaVault FuseBox at `/home/deck/Downloads/Luna Ultra Video Merge v1-4`.
This is a FOLLOW-UP round. Round 1's findings are in `BATTLE_TEST_REPORT.md` (project root) —
read it first. Do not re-run what Round 1 already covered; every section below is either an
unanswered question Round 1 raised or a hole it couldn't close.

Existing assets you should reuse:
- `tests/md5_matrix_test_ext.py` — the Round-1 matrix harness (subprocess-per-cell isolation,
  signal-timeout fallback, per-cell process cleanup). Extend it; don't rewrite it.
- Real footage: `~/Videos/multicam video archive test.zip` (9 clips; Round 1 trimmed each to an
  8s stream-copy prefix — reuse that trick where encode time matters, but see §1 and §3 where
  full-length clips are specifically required).
- The venv at `venv/` and bundled ffmpeg/ffprobe in `bin/`.

## §1 — Root-cause the §6.1a verification anomaly (HIGHEST PRIORITY)
Round 1 found that MD5 verification fails with "unexpected... nothing to explain it" for
non-final clips of multi-clip camera groups on SYNTHETIC footage (5 independent repros), yet
the same adaptive interior-window fallback correctly resolved the identical symptom shape on
REAL footage (two 9/9-clean runs). Find out why.
1. Reproduce one failing synthetic case (S1-style: two conforming clips, same camera group,
   Archival + one-track-per-clip, verify on) and instrument `_verify_one_clip()` in
   `src/ffmpeg_runner.py` (temporary logging only — revert after) to capture: the computed
   recovery window, the guard sizes, the alignment-search behavior, and where exactly the
   interior-window comparison diverges.
2. Test these specific hypotheses, one variable at a time:
   a. Clip duration relative to the ~300ms guard windows (try 3s, 10s, 30s, 120s synthetic clips).
   b. Audio content uniformity — synthetic tone/silence vs. noise vs. real-world audio pasted
      into an otherwise-synthetic clip (does self-similar audio defeat the alignment search?).
   c. AAC frame-boundary alignment at the concat seam (vary clip durations so the seam lands
      on vs. off an AAC frame boundary).
3. Deliverable: a definitive statement of the failure condition — and, critically, whether any
   REAL camera footage can satisfy that condition. That determines severity.
4. Do NOT fix it. Report the mechanism.

## §2 — MOV-box-level analysis of the bin_data leak (§6.1b)
Round 1 proved the leak survives every `-map`/`-dn`/negative-map combination, implicating
muxer-level track linkage. Go one level deeper:
1. Dump the full MOV box structure of the Gran Canaria source clip (ffprobe
   `-show_streams -show_format` is not enough — use a box-level dump; if no MP4 box tool is
   available, ffprobe's `-print_format json -show_entries` on stream dispositions/side data
   plus a hex scan for `tref` boxes is acceptable; document what tooling you used).
2. Identify the linkage: is the data track `tref`-referenced from the video track? What
   handler/type is it exactly?
3. Test whether a two-pass strip works: remux the conformed output once more with the data
   stream explicitly excluded — does a second remux drop it where the first mux couldn't
   avoid it? (This tests the maintainer-suggested post-mux strip without patching the app.)
4. Check generality: probe every clip in the multicam test set and any other real footage
   available for similarly-linked tracks. One camera's quirk, or a class?

## §3 — Close the hi-risk matrix hole: software-encode + ProRes-compat cells
Round 1 left 54/60 hi-risk cells unresolved on time budget (confirmed genuine slow encoding,
not hangs). Close it:
1. Re-run ONLY the timed-out subset (software-encode and ProRes/compat-baseline combinations)
   via `md5_matrix_test_ext.py` with `MD5_PER_TEST_TIMEOUT_S=600` minimum.
2. Run it in the background and expect multiple hours; check verify.log per completed cell —
   never trust the wrapper's pass/timeout status alone.
3. If cells STILL time out at 600s, verify active encode progress before extending further,
   and report per-cell encode throughput so a future budget can be set from data.

## §4 — Cancellation, interruption, and crash-recovery (entirely untested in Round 1)
1. Cancel mid-merge via the app's own cancel path at each phase: during per-clip conform,
   during concat, during archival mux, during MD5 verification. After each: confirm no
   orphaned ffmpeg processes, confirm `_temp/` is cleaned, confirm the UI returns to a
   usable state, confirm a subsequent merge into the same output folder succeeds cleanly.
2. Hard-kill the app (SIGKILL) mid-merge. Relaunch. Confirm: no stale lock/state corruption,
   `_temp/` leftovers are either cleaned or don't contaminate the next run (Round 1 observed
   orphaned ffmpeg processes writing to shared `_temp/clip_NN.mov` paths when a DRIVING
   process died — establish whether the app's own abnormal-exit path has the same gap).
3. Disk-full mid-encode: use a small loopback/tmpfs filesystem as the output target and fill
   it mid-merge. The app must surface a distinct, truthful error — not report success, not
   leave a silently-truncated master.
4. Launch two app instances and run merges into the SAME output folder simultaneously.
   Document what happens (corruption, clean failure, or last-writer-wins).

## §5 — GUI error surfacing for silent ffmpeg failures
Round 1's §6.2 (square-crop crash) produced no output file and no distinct error in a
headless harness. Verify the real GUI path on the CRD display (:20, xdotool + x11grab
screenshots, per Round 1's method):
1. Trigger the §6.2 square-crop failure through the actual GUI. Screenshot what the user
   sees. A generic "merge failed" dialog is a PASS for surfacing (the bug itself is already
   filed); no dialog / apparent success with an empty output folder is a severe NEW finding.
2. Repeat for one mid-concat ffmpeg failure (e.g. delete a source clip after the merge
   starts) — does the failure message identify WHICH step/clip failed?

## §6 — Review tab: real playback verification (Round 1 was internal-state-only)
On the CRD display, load a Round-1 master with archival tracks and verify with actual
screenshots (not widget state):
1. Frames render (capture at t=0, mid, near-end; confirm non-black, distinct images).
2. Seeking: seek to 3+ positions, screenshot each, confirm the frame changes and matches the
   expected scene (use masters made from clips with visually distinct content).
3. Source switching: swap Master → an archival original mid-playback; confirm the picture
   actually changes to the original (different aspect/rotation makes this verifiable).
4. Track selection: switch audio tracks and confirm via the engine that playback continues
   (pixel-level audio verification is out of scope; state + no-freeze is the bar).
5. State explicitly which checks CANNOT be verified without a human (e.g. A/V sync,
   smoothness) rather than approximating them.

## §7 — Hostile inputs and environments
All Round-1 inputs were clean-ASCII paths on fast local disk. Test each of the following
end-to-end (merge + verify) with a small synthetic set, checking both success AND that any
failure is surfaced distinctly:
1. Source folder and filenames containing: spaces, unicode (CJK + emoji), single quotes,
   `%` and `#` characters (ffmpeg CLI/concat-demuxer escaping hazards).
2. Output folder: read-only (permission denied must surface distinctly), path >200 chars.
3. A source folder containing a zero-byte .mp4, a truncated .mp4 (head -c on a real clip),
   and a .txt renamed to .mp4 — the app must skip/flag these distinctly, not crash or hang.
4. A WAV backup whose duration wildly mismatches its paired video (e.g. 2x longer).
5. Source on a slow/removable-style mount if feasible (a loopback mount is acceptable).

## §8 — MergeWorker signal-delivery investigation
Round 1's harness repeatedly saw `MergeWorker.finished` fail to arrive despite completed
work — documented as a pre-existing quirk in `md5_matrix_test.py` itself. Establish whether
this is purely a headless-harness artifact or can affect the real GUI:
1. Read the signal/thread lifecycle in `ffmpeg_runner.py` + the merge tab's connection code.
   Identify the exact mechanism that stalls under a processEvents-polling loop.
2. In a real GUI session (CRD display), run 5 consecutive merges without restarting the app.
   Confirm the UI reaches its completed state every time (screenshot each completion).
3. Verdict: harness-only artifact (explain why the GUI is immune) or a real risk (explain
   the conditions).

## Ground rules (same as Round 1)
- Do NOT fix anything you find. Report it. Temporary instrumentation for §1 must be reverted
  (verify with `git diff` at the end — the tree must be clean apart from new test files).
- Never trust a wrapper's success/timeout status without reading the underlying verify.log
  or ffmpeg stderr.
- Real screenshots only; if the visual session is unreachable, say so plainly.
- Back up `settings.json` before starting; restore and diff it at the end.
- Clean up all orphaned processes and temp folders when done; confirm with `ps`.
- Final deliverable: `BATTLE_TEST_REPORT_ROUND2.md` in the project root — same structure as
  Round 1 (findings ranked by severity, explicit full-vs-representative coverage statement
  per section, and a "what still isn't covered" appendix).

Execute this prompt to completion. Do not ask questions or wait for user input.
