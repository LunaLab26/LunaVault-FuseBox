# Investigation report: the `hw_encoder=auto` + H.264 compatible-master "timeouts"

Follow-up to `INVESTIGATE_HW_ENCODER_AUTO_TIMEOUT.md`; context in
`BATTLE_TEST_REPORT_ROUND2.md` §3. **Outcome: a real, reachable app bug was
found, root-caused to a specific ffmpeg mechanism, fixed with a minimal
two-flag change, and verified.** The "slow encode" framing from Round 2 was
wrong — these cells were never slow.

## The headline

`compat_baseline=h264 + hw_encoder=auto` merges of real mixed footage were
not timing out at 600s. They were **failing outright in ~57 seconds**, at
1.71× realtime (nearly 3× FASTER than the software path's 0.60×), and the
test harness recorded the fast failure as a timeout because of the exact
`finished`-signal blind spot documented in `BATTLE_TEST_REPORT_ROUND2.md`
§8: the merge failed, the app's own internal handling worked fine (a real
GUI user gets a proper "Merge failed" dialog), but the harness's external
`finished` connection never fires, so it sat out the full 600s budget and
then reported "timeout... expected output files never appeared."

## How the hypotheses fell, in order

1. **Probe overhead — dead.** `detect_best_hw(ff, "h264")` resolves to
   `vaapi` in 0.13s cold, 0.000s cached. `hw_encode_plan` adds 0.11s.
2. **VAAPI encoder slow — dead.** A synthetic 2×8s 4K HEVC 10-bit concat
   fixture, driven through the exact `build_concat_reencode_cmd` output
   standalone: `hw_encoder=off` 26.6s, `auto` 25.7s. Identical (both
   decode-bound at ~0.6×).
3. **Stage-timed real run — smoking gun.** Running one real failed cell
   config (`doff_eauto_archoff_compath264`, real 9-clip footage) with a
   per-stage timing logger: per-clip conform completed normally in ~55s
   (genuinely using `h264_vaapi` — the UI stage label "CPU: libx264" is a
   separate cosmetic bug, see below), then the compat re-encode **died at
   t=7.97s of playback** — the exact end of the first concat segment:

   ```
   [vf#0:0] Terminating thread with return code -38 (Function not implemented)
   frame=240 fps=51 ... speed=1.71x
   Conversion failed!
   ```

## Root cause

The concat demuxer's segments are **not parameter-uniform**: a
stream-copied ("ok"-conform) clip keeps its camera's exact decoded
parameters — for this footage, `yuvj420p` **full range (pc)** — while every
transcoded segment carries the encoder's `yuv420p` **limited range (tv)**.
At the first seam where parameters change, ffmpeg reinitialises the filter
graph. A graph containing `hwupload` (which the VAAPI encode path requires)
**cannot be reinitialised** — ffmpeg aborts the filter thread with
`AVERROR(ENOSYS)` (-38, "Function not implemented").

This precisely explains every observation from both battle-test rounds:
- **Why only `compat_codec=h264` + `hw_encoder=auto`**: it's the only
  combination that puts `hwupload` into the *concat* re-encode's filter
  chain. ProRes is software-only (no hw filter chain); `hw_encoder=off` has
  no `-vf` at all; the per-clip conform also uses `hwupload` but each
  per-clip command reads ONE file — no mid-stream parameter change, no
  reinit, no crash (which is why Round 1's per-clip VAAPI cells all passed).
- **Why independent of `hw_decode` and archival mode**: the seam is in the
  concat input regardless.
- **Why Round 1's single-clip VAAPI compat test passed**: one segment, no
  seam.
- **Why my synthetic reproduction attempt failed to reproduce**: its two
  segments were identical libx265 encodes — parameter-uniform, no reinit.
  Heterogeneity (specifically the mixed full/limited range that falls
  straight out of mixing stream-copied camera originals with transcoded
  segments — i.e. the app's core use case) is the trigger. Rebuilt the
  fixture as stream-copy + vaapi-transcode segments: reproduced the -38
  failure identically, standalone, first try.

## The fix (applied to `core/ffmpeg_cmd.py:build_concat_reencode_cmd`, hw branch only)

1. **`-reinit_filter 0`** as an input option on the concat input: keeps the
   original filter graph alive and converts mismatched segments' frames to
   the graph's negotiated input format instead of rebuilding the graph.
2. **`scale=out_range=tv,`** prefixed to the `format=nv12,hwupload` filter
   chain, plus **`-color_range tv`** on the output: with no reinit, the
   whole output inherits whatever the FIRST segment negotiated — measured
   directly, a full-range first segment skewed the entire output's luma by
   ~5 points (a genuine range mismatch inside a tv-tagged stream). Forcing
   limited range pins the negotiation to exactly what the software path
   produces, regardless of segment order.

**Fix verification (standalone, real mixed-footage fixture):**
- Old command: dies at frame 240 (-38). Fixed command: all 480 frames, exit
  0, **1.73× realtime** (vs software's 0.60×).
- Color correctness: luma stats (`signalstats` YAVG) of the fixed VAAPI
  output vs. the libx264 reference agree to **within 0.05** at sample
  points in both the stream-copied and transcoded halves (85.22 vs 85.27;
  90.05 vs 90.04), output correctly tagged `tv`. The unpinned variant
  measured 80.60/86.24 at the same points — the exact arithmetic of a
  limited→full expansion, confirming the range-pinning is load-bearing.
- The `hw_decode=auto` arm (adds `-hwaccel vaapi` on the same input) also
  completes with the fix (1.29×).
- Full test suite: 510/510 pass (one existing command-shape test updated to
  assert the fixed shape, with the reasoning documented in the test).
- The 6 originally-failed cell configurations re-run through the real
  matrix harness: results appended below.

## Also found along the way (minor, not fixed)

- **Stage-label cosmetic bug**: during VAAPI-accelerated per-clip conform,
  the progress UI shows "CPU: libx264" — `MergeTab._ENCODER_LABELS` maps
  only nvenc/qsv/amf, so both `"auto"` and `"vaapi"` fall through to the
  default label. Purely cosmetic (the encode genuinely runs on VAAPI —
  confirmed via `ps`), but it would mislead a user checking whether their
  GPU is being used. One-line fix in `merge_tab.py` if wanted: add
  `"vaapi": "GPU: VAAPI"` (and ideally resolve `"auto"` to the detected
  vendor before lookup).
- **Round 2 §3's narrative needs a correction**: the "6 timeouts show the
  h264+auto combination consistently exceeds 600s... plausibly probe
  overhead" paragraph is wrong on both counts — the cells fail fast, and
  the probe is instant. (The other 48 cells' results and the "genuinely
  slow, not stuck" conclusion for the software/ProRes cells remain
  correct — cell 1's 602.9s of real work was verified directly.)

## Verification matrix results

All 6 originally-failed cell configurations re-run through the real matrix
harness (real 9-clip footage, MD5 verify on) with the fix applied — **all 6
now complete their merges; zero crashes, zero genuine failures**:

| Cell | Result | Verify |
|---|---|---|
| doff_eauto_archoff_compath264 | md5_mismatch | 6/9 |
| doff_eauto_archshared_compath264 | md5_mismatch | 8/9 |
| doff_eauto_archpercpip_compath264 | **pass** | 9/9 |
| dauto_eauto_archoff_compath264 | md5_mismatch | 6/9 |
| dauto_eauto_archshared_compath264 | md5_mismatch | 8/9 |
| dauto_eauto_archpercpip_compath264 | **pass** | 9/9 |

The four "mismatch" cells were checked against their verify logs directly:
- Both `archoff` 6/9 results are exactly the **same 3 rotation-tag clips**
  as every other `archoff` cell in the whole matrix — the long-documented
  "no archival track means no per-clip rotation tag" limitation, correctly
  diagnosed as expected in the log.
- Both `archshared` 8/9 results are a **single clip's camera audio**
  mid-way in a shared archival track — the documented shared-track
  alignment limitation, with the honest "use One-track-per-clip archival"
  diagnosis (its video verified decode-identical).

In other words, the `eauto+compath264` family now produces **exactly the
same verification outcomes as its `eoff` equivalents** — at hardware-encode
speed. With this, every one of the original 60 hi-risk matrix cells has a
final, explained disposition: no timeouts, no unexplained failures anywhere
in the grid.

(One harness note for future runs: the ~603s wall times recorded per cell
are the harness waiting out its `finished`-signal budget after the real
work completed much earlier — the §8 artifact again, benign here since the
file-existence fallback then collected the real results.)
