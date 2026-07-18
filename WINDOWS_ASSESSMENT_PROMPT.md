# LunaVault FuseBox — Windows Laptop Assessment (post-v1.4.003)

You are assessing LunaVault FuseBox v1.4.003 on a Windows laptop. The app was
battle-tested across two rounds on Linux (Steam Deck) and ten real bugs were
fixed — see `BATTLE_TEST_REPORT.md`, `BATTLE_TEST_REPORT_ROUND2.md`, and
`HW_ENCODER_AUTO_INVESTIGATION_REPORT.md` in the project root, plus the
v1.4.003 entry in `src/dev_history.py`. Read those first. Every fix was
verified on Linux only. Your job: verify the app — and specifically those
fixes — behave correctly on Windows, where several of them rest on
platform-sensitive foundations.

## §0 — Setup and sanity
1. The repo's `bin/` may still contain LINUX ffmpeg/ffprobe binaries from the
   Linux sessions. Windows needs `bin/ffmpeg.exe` + `bin/ffprobe.exe` (a full
   GPL build, e.g. gyan.dev, with libx264/libx265) — `build.bat` documents
   the expectation. Verify `python src/main.py` launches and the About tab
   shows v1.4.003 with the 2026-07-17 history entry on top.
2. Run the test suite (`python -m pytest tests/ -q`) — expect 510 passing.
   Note: the harness had real Windows cp1252 console-encoding crashes before
   (see `md5_matrix_test.py`'s UTF-8 reconfigure comment); any encoding
   failure is a finding, not an environment quirk to ignore.
3. Check `crash.log` in the project root for stale Windows crash evidence
   from earlier sessions before you start — note anything relevant, then
   record its size so you can tell later whether YOUR session appended to it.

## §1 — Windows-risk review of the v1.4.003 fixes (highest priority)
Each of these was verified on Linux; the listed concern is why Windows needs
its own check.

1. **Pre-flight output-folder writability check** (`merge_tab.py,
   _check_output_writable`) — uses `os.access(dir, os.W_OK)`, which is
   NOTORIOUSLY unreliable on Windows: it reflects the read-only attribute,
   not real ACLs, so it can return True for folders you cannot actually
   write to (and the read-only attribute on folders doesn't block writes
   anyway). Test: (a) an ACL-denied folder (create one, then
   `icacls <dir> /deny %USERNAME%:(W)`), (b) a read-only attribute folder,
   (c) a protected location like `C:\Program Files\test`. For each: does the
   pre-flight catch it, and if it doesn't, does the merge still fail at the
   END with the clear "Finalising archive failed / Permission denied" dialog
   (the pre-fix behavior — acceptable fallback, worth documenting) or
   something worse? A pre-flight that passes bad folders on Windows is a
   finding; silent failure would be a severe one.
2. **Font-metrics UI fixes** — three fixes depend on font metrics/layout
   timing that differ on Windows (Segoe UI vs Linux fonts, and DPI scaling):
   the Extract tab's "Create folder…" button width (now computed from
   `fontMetrics().horizontalAdvance`), the Merge tab empty-state paragraph
   height (a `QTimer.singleShot(0, ...)` deferred wrap-height computation),
   and the clips-table Status column minimum width (130px). Verify all three
   visually at **100%, 125%, and 150% display scaling** (the 2026-07-12
   history entry shows scaling has caused real overlap bugs on Windows
   before). Take actual screenshots.
3. **Per-instance scratch isolation** (`_instance_scratch_name` /
   `_make_scratch`) — run two merges concurrently (two app instances) and
   confirm each gets its own `_temp\run_<pid>_<hex>` folder, both complete,
   and both clean up. Also confirm the deeper scratch path doesn't break
   Windows' 260-char MAX_PATH on a realistically deep project location.
4. **Square-crop fix** — quick functional check: synthesize a square clip
   (`ffmpeg -f lavfi -i testsrc2=size=2160x2160:rate=30:duration=3 ...`) plus
   one 16:9 companion, merge with "Square clips: Crop to fill 16:9"
   (default). Must complete; pre-fix this crashed 100% of the time.
5. **Chapter-track / `-map_chapters -1` fix and AAC-priming prediction** —
   platform-independent logic, so a light touch is fine: one archival merge
   of any real clip with MD5 verify on; confirm the verify.log explains any
   non-pass in plain language ("predicted unverifiable" / rotation-tag
   explanations) with no "unexpected... nothing to explain it" alarms.

## §2 — Hardware pipeline: a DIFFERENT encoder family on Windows
The Linux seam-crash fix (`-reinit_filter 0` + `scale=out_range=tv` in
`build_concat_reencode_cmd`) was verified against VAAPI, which uses an
`hwupload` filter chain. On Windows, `hw_encoder=auto` resolves to
**NVENC/QSV/AMF** instead (see `core/gpu_encode.py` VENDOR_ORDER), which take
software frames directly — no `hwupload`, no `-vf` chain — so the crash
mechanism differs, but the fix's `-reinit_filter 0` still applies to their
commands and has never been exercised with them.
1. Identify which hw encoder this laptop actually has
   (`detect_best_hw`/`available_hw_vendors`, or the dev panel's display).
2. **The critical case**: "Compatible playback master" (H.264) + hardware
   encode on MIXED footage — at least one stream-copied clip plus at least
   one transcoded clip from a real camera (this mix is what crashed Linux at
   the seam). Confirm: completes, plays smoothly across the seam, and the
   two halves' brightness/colour match each other and a software-encoded
   reference of the same list (compare `signalstats` YAVG at a timestamp in
   each half — the Linux fix needed explicit range pinning; NVENC/QSV may
   handle mid-stream range changes differently, better or worse).
3. Per-clip conform with hw encode on, same footage: completes, and the
   progress label — check whether it names the real encoder ("GPU: NVENC"
   etc.) or falls back to "CPU: libx264" (a known cosmetic gap for
   vaapi/auto; verify whether Windows vendors are labeled correctly).
4. hw_decode option: exercise if offered on this machine; note that
   `vaapi_decode_global_args` is Linux-only, so confirm the decode option's
   Windows behavior (greyed out? silently software?) matches what the UI
   claims.

## §3 — The 4K 10-bit HEVC hardware-decode guard (DANGER — read first)
This exact laptop class is documented to HARD-CRASH THE ENTIRE OS when 4K
10-bit HEVC is GPU-decoded in Review (see round-1 §1.4 and
`dev_history.py` 2026-07-03). Verify the guard only — do NOT test the crash:
1. Load a 4K 10-bit HEVC master in Review: "Software decode" must be forced
   on and the checkbox disabled, with the explanatory tooltip.
2. Confirm the dev-panel override (`dev_review_allow_risky_hw_decode`) is
   OFF. **Do not enable it.** If it is somehow already on, turn it off and
   report that as a finding.
3. Save all work before this section regardless.

## §4 — Windows-hostile inputs and environments
Round 2 covered unicode/corrupt files on Linux; these are the Windows-shaped
equivalents, untested anywhere:
1. Source files named with unicode + spaces + `%`/`#` (as in round 2), on an
   NTFS path with mixed-case and a space-containing parent.
2. Output to a **OneDrive-synced folder** (placeholder/cloud-only files and
   sync locking are classic Windows failure modes for ffmpeg-heavy apps).
3. Output to a path exceeding 260 characters (unless long-path support is
   enabled system-wide — check and note which).
4. A source folder on a UNC network path (`\\server\share\...`) if
   available; otherwise a mapped drive letter.
5. Clips whose names collide case-insensitively (`clip.mp4` / `CLIP.mp4`
   from a Linux-made archive) if you can construct them.
For each: success is fine, and a clearly-surfaced failure dialog is fine;
hangs, silent no-output, or misattributed errors are findings.

## §5 — Ground rules (unchanged from prior rounds)
- Report; don't fix. Read the underlying verify.log/ffmpeg stderr — never
  trust a wrapper's status alone.
- If you drive merges programmatically: NEVER wait on an external
  connection to `MergeWorker.finished` — it's shadowed and unreliable
  (`BATTLE_TEST_REPORT_ROUND2.md` §8). Poll `worker is None` +
  Start-button-visible, or connect to `verification_done`/`progress`.
- Back up `settings.json` first; restore and diff at the end. `git status`
  before/after — the tree must gain nothing but your report.
- Real screenshots only; state plainly anything you could not verify.
- Deliverable: `WINDOWS_ASSESSMENT_REPORT.md` in the project root — findings
  ranked by severity, explicit per-section coverage statement, and a
  "Windows-specific gaps that remain" appendix.

Execute to completion. Do not ask questions or wait for user input.
