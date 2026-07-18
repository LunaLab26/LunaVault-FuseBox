# Investigation brief: `hw_encoder=auto` + H.264 compatible-playback-master reproducibly exceeds 600s

## App and context

LunaVault FuseBox is a PySide6 desktop app at
`/home/deck/Downloads/Luna Ultra Video Merge v1-4` that merges camera video
clips (+ WAV backups) into one master file, with an optional "Compatible
playback master" pass that re-encodes the whole baseline into one clean,
continuous H.264 or ProRes stream (`core/ffmpeg_cmd.py:build_concat_reencode_cmd`,
invoked from `ffmpeg_runner.py`'s `MergeWorker.run()`). Hardware
decode/encode offload (VAAPI on this Linux/Steam Deck machine) is controlled
by two independent settings, `hw_decode` and `hw_encoder`, each one of
`"off"`/`"auto"`/a specific vendor name. Resolution logic lives in
`core/gpu_encode.py` (`detect_best_hw`, `hw_encode_plan`, `vaapi_decode_global_args`,
`system_vaapi_ffmpeg`) and is threaded through by `core/ffmpeg_cmd.py`'s
`_resolve_hw_extras` (per-clip conform step) and `build_concat_reencode_cmd`
(the compatible-playback-master re-encode step).

A prior two-round battle test of this app is documented in
`BATTLE_TEST_REPORT.md` and `BATTLE_TEST_REPORT_ROUND2.md` in the project
root — read `BATTLE_TEST_REPORT_ROUND2.md`'s §3 section first; this brief
picks up exactly where that section's "not root-caused further this round"
note leaves off.

## What was found

Round 2 re-ran a 54-cell hi-risk matrix (real 9-clip camera footage,
`hw_decode` × `hw_encoder` × archival mode × compatible-playback-master
codec, all at `hw_decode`/`hw_encoder` ∈ {`off`,`auto`}) with a 600s/cell
minimum timeout. Final tally: 32 pass, 16 an already-understood/expected
mismatch pattern (unrelated to this), and **6 timeouts — every single one
`compat_baseline=True, compat_codec="h264", hw_encoder="auto"`**, spanning
both `hw_decode` values and all 3 archival modes:

```
dXXX_eauto_archoff_compath264
dXXX_eauto_archshared_compath264
dXXX_eauto_archpercpip_compath264
```
(`XXX` = `off` or `auto`, i.e. 2 hw_decode values × 3 archival modes = 6 cells)

Every *other* cell that reached the `compath264` / ProRes / `hw_encoder=off`
combinations on the same real footage completed well within 600s. In
particular:
- The exact same H.264 compatible-master re-encode at `hw_encoder="off"`
  finishes fine (confirmed: cells named `..._compath264` with `eoff` in
  their name are NOT in the timeout list).
- Every ProRes profile (`proxy`/`standard`/`hq`) at `hw_encoder="auto"`
  finishes fine too (ProRes is software-only by design — see
  `build_concat_reencode_cmd`'s own docstring: "Only ever applies to
  codec='h264'... there's no such thing as a hardware ProRes encoder").

So the failure is precisely at the intersection of **H.264 output** +
**`hw_encoder="auto"`** — not `hw_encoder` in general, not H.264 in general,
and independent of `hw_decode` or archival mode.

## Working hypothesis (not yet verified)

`hw_encoder="auto"` triggers `detect_best_hw(ff, "h264")` in
`build_concat_reencode_cmd`, which (per `core/gpu_encode.py`) presumably
probes for a working VAAPI encoder before deciding whether to use it. This
compatible-master re-encode is a **single continuous pass over the WHOLE
merged baseline** (the most expensive single step in the pipeline — per
`build_concat_reencode_cmd`'s own docstring), unlike the per-clip conform
step where the same probe cost would be amortized/cached differently or
matter less relative to each clip's own shorter runtime. A plausible
mechanism: the VAAPI probe-and/or-fallback path adds meaningful fixed
overhead, or the VAAPI encoder itself is markedly slower or hangs/stalls
partway through on this specific codec target on this hardware, and that
cost is only visible here because it's a single long pass rather than many
short ones.

This has NOT been confirmed — it's a plausible starting hypothesis, not a
conclusion. Round 2 did not have time to instrument this further.

## What to actually check

1. **Reproduce cheaply first.** Round 2's repro used real 8-second-trimmed
   4K camera clips from `~/Videos/multicam video archive test.zip`
   (trimmed copies may still exist under
   `/tmp/claude-*/scratchpad/battle_test/source/multicam_trimmed` from a
   prior session — regenerate via `ffmpeg -ss 0 -t 8 -c copy` per-clip if
   gone). Confirm the timeout reproduces on a MUCH shorter/smaller
   synthetic clip set too (faster iteration) before doing deep timing work
   on the real footage.
2. **Isolate the exact command.** Call `core.ffmpeg_cmd.build_concat_reencode_cmd`
   directly (import it, don't drive the whole app) with
   `codec="h264", hw_encoder="auto"` against a real concat file + chapters
   file from a completed merge's temp dir, and time it directly via
   `subprocess.run` outside of `MergeWorker`/Qt entirely. This removes the
   Round-2-documented `MergeWorker.finished`-signal-delivery test-harness
   artifact (see `BATTLE_TEST_REPORT_ROUND2.md` §8) from the picture
   entirely — you want to know if the *ffmpeg command itself* is slow, not
   whether a test driver's signal handling is confusing the measurement.
3. **Compare wall-clock time directly**, same input, same machine, back to
   back: `hw_encoder="off"` vs `hw_encoder="auto"` for the H.264 compat
   re-encode. Quantify the actual slowdown factor, not just "over 600s."
4. **Instrument `detect_best_hw`/`hw_encode_plan`/`system_vaapi_ffmpeg`
   directly** (temporary prints/timing, revert after — same discipline
   Round 2 used) to see exactly how long the *probe* itself takes, and
   whether the resolved plan is genuinely using VAAPI or silently falling
   back to something else that's unexpectedly slow.
5. **Watch the process while it runs**: `ps aux` / `nvtop`-equivalent for
   this iGPU (`radeontop` or similar on Steam Deck) to see whether the
   VAAPI encoder is actually active and making progress, or stalled/spinning.
   Round 1 and Round 2 both independently confirmed that a slow-but-genuinely-
   working ffmpeg process is easy to mistake for a hang — rule that out
   explicitly here too before concluding anything is actually broken.
6. **Check ffmpeg's own stderr** for this specific command combination —
   VAAPI driver warnings, unsupported profile/level fallback messages, or
   repeated retry/renegotiation chatter would be a strong, concrete signal.

## Deliverable

A precise root cause (or a confident "this is genuinely just slower, here's
the measured factor and why") for why `hw_encoder="auto"` specifically
slows down the H.264 compatible-playback-master re-encode step past 600s on
real footage, when every other codec/hw_encoder combination in the same
matrix does not. If you find and fix a real bug, apply a minimal, targeted
fix and re-run the same 6 previously-timed-out cell configurations to
confirm they now complete — but if the honest conclusion is "this is
correctly slow for a legitimate reason (e.g. VAAPI genuinely underperforms
software x264 on this specific hardware for full-length re-encodes)," say
so plainly rather than forcing a fix; that's a valid, useful answer too.
Do not touch any other part of the app while investigating this — this
brief is scoped to this one performance question only.
