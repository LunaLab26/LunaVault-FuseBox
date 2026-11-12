# Windows session report (7 Oct 2026): v1.4.019 → v1.4.024

Written by Windows-Claude, picking up Steam-Claude's session-3 handover (`HANDOVER_SESSION3.md`).
Branch `overnight-2026-10-06`, local commits only. **Nothing has been pushed.** Windows now owns the
v1.4.NNN sequence; Steam-Claude sends further changes as patches.

## Commits

| Ver | What | Kind |
|---|---|---|
| 020 | Test suite portable to Windows (`ffmpeg.exe` name; VAAPI tests no longer probe the real machine) | tests only |
| 021 | Header menu button: "⋯" was tiny in Segoe UI → "•••"; no lighter box behind the logo | UI |
| 022 | Merge clip table keeps file names readable in a ~1090 px window (180 px floor, then sideways scroll) | UI |
| 023 | "Converted for delivery" instead of "Will be converted" when Optimize is the only reason | UI |
| 024 | MD5 verify compares the right windows (Steam-Claude's patch): WAV backups now verify | verify |
| – | Harness: UTF-8 child I/O (Steam), `R2_app_default` case (app's real defaults) | harness |

Separate local branch `win/hevc-ps-fingerprint`: logs "HEVC join at risk" when two joined segments
carry different HEVC parameter sets. Detection only, not version-bumped.

Tests: 584 pass. The one failure is the canary below, expected while `bin\` holds a post-7.1 ffmpeg.

## Findings that need a decision

### 1. Newer ffmpeg corrupts mixed-HEVC lossless joins
- Since 7.1, ffmpeg's mov muxer strips in-band VPS/SPS/PPS from `hvc1` samples, so a joined master
  keeps only the first segment's parameter sets. Affected: the 2026 git build shipped in the Windows
  v1.4.004 release, and the 9.0.2 release. 7.0.2 (Deck) and 7.1.1 are fine. H.264 `avc1` is not affected.
- Real footage: R2 (v1.4.009, Optimize off) has 204 decode errors starting at the conform→Luna
  camera join (~31 s), against 2 on the Deck.
- The app's real default for a mixed folder (Optimize on) makes every segment a conform, which
  likely avoids it. The risk is a mixed HEVC folder with Optimize unticked, or two camera models.
- **Options:** (a) ship ffmpeg 7.1.1 on Windows now as a stopgap; (b) tag `hev1` when parameter sets
  differ (some Apple players refuse `hev1`); (c) re-encode the odd segments.

### 2. Luna 32-bit WAVs are stored at 24 bits
- Luna Ultra WAV backups are `pcm_s32le` with real data in the low byte (86–96% of samples).
  ffmpeg's ALAC keeps at most 24 bits, so the bottom 8 bits are dropped (about −144 dBFS, inaudible).
  The verify check compares at 16 bits, so it can't see this.
- **Options:** 32-bit PCM in the MOV slot, 32-bit FLAC, or honest "24-bit" wording in the restore log.

### 3. Test matrix incomplete
Windows has only 7.4 GB of RAM; Claude Code stopped the matrix twice for low memory. Six of the
v1.4.009 cases ran: R1 ×2 and R2 baseline/blurfill match the Deck; R2 default/perclip show
finding 1. Still to run: the rest of v1.4.009, all of the branch, and `R2_app_default`.

## Smaller notes
- At the laptop's native 150% scaling the header, folded Merge section and chips look clean.
- This PC has only an Intel HD 620, so QSV is the only GPU path tested. NVENC/AMF are untested.
- Never enable hardware decode for 4K 10-bit HEVC (unchanged rule; default stays off).
