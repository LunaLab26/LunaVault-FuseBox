"""dev_history.py — Development history shown at the bottom of the About tab.

Hand-curated, human-readable milestones (not a 1:1 mirror of every internal
task number — see DEVELOPMENT.md for the full technical log this is drawn
from). Newest first. Each entry has a one-line summary always shown, plus
a longer, plain-language detail list revealed on demand.

CONVENTION: whenever the app is amended, bump LAST_UPDATED below to the current
date + time (the About tab shows it as the "latest iteration" stamp), and add or
extend the newest HISTORY entry for anything user-visible.
"""

from dataclasses import dataclass, field

# Timestamp of the most recent change to the app. Update on every amendment.
LAST_UPDATED = "2026-07-18 22:59"


@dataclass
class HistoryEntry:
    date: str
    title: str
    summary: str
    details: list = field(default_factory=list)


HISTORY: list = [
    HistoryEntry(
        date="2026-07-18",
        title="v1.4.004 — Windows assessment: fixed a real crash on GPU-accelerated Compatible "
              "playback master (Intel/NVIDIA/AMD), plus two smaller display fixes",
        summary="A dedicated assessment on a real Windows laptop, using the round of fixes "
                "from v1.4.003, turned up one genuine crash and confirmed two smaller gaps. "
                "The crash: merging with \"Compatible playback master\" and GPU encoding "
                "turned on failed outright on Windows whenever the graphics card was Intel "
                "(Quick Sync), NVIDIA, or AMD — which, unlike the Linux/AMD hardware this app "
                "was originally hardened against, covers virtually every Windows PC with a "
                "dedicated or built-in GPU. Also fixed: the merge progress display could show "
                "\"CPU: libx264\" even while genuinely running on the graphics card, and the "
                "Extract tab's \"Create folder…\" button — already fixed once for a similar "
                "Linux issue — was still clipping its own leading letter on Windows.",
        details=[
            "Fixed: \"Compatible playback master\" combined with GPU encoding crashed outright "
            "on any Windows machine using Intel Quick Sync, NVIDIA NVENC, or AMD's encoder — "
            "confirmed directly on real Quick Sync hardware. Root cause: this merge step was "
            "given the wrong instruction for which copy of ffmpeg to actually run whenever the "
            "graphics card didn't need a different one from usual (true for all three of those "
            "vendors) — it ended up with no instruction at all instead of \"just use the normal "
            "one,\" and the merge died before doing any work. The AMD graphics used during this "
            "app's original hardening happened to always need that different copy, which is "
            "exactly why this never showed up until a real Windows machine was tested.",
            "Fixed: the merge progress display could say \"CPU: libx264\" for a clip that was "
            "genuinely being transcoded on the graphics card the whole time — confirmed the "
            "encode itself was always correct; only the on-screen label was wrong, for both the "
            "Windows and the (already-fixed) Linux hardware paths. It now names the real "
            "encoder in use.",
            "Fixed: the Extract tab's \"Create folder…\" button clipped its own leading \"C\" on "
            "Windows, at every display-scaling level tried — the same symptom as a Linux bug "
            "fixed last version, but the earlier fix's width calculation still came up a few "
            "pixels short against Windows' own font. The button now sizes itself the same way "
            "every other button in the app already does, which can't come up short on any "
            "platform or display scaling.",
            "Added a permanent regression test for the GPU-encode crash, covering the exact "
            "vendor family (Intel/NVIDIA/AMD) that was broken, alongside the existing AMD/Linux "
            "test — so this specific mistake can't quietly return for either hardware family.",
        ],
    ),
    HistoryEntry(
        date="2026-07-17",
        title="v1.4.003 — two-round battle test: fixed a square-clip crash, a hardware-encode "
              "crash on mixed footage, a false verification alarm, and six other real bugs",
        summary="A deliberate, no-holds-barred battle test of the whole app turned up ten real "
                "issues, all fixed and re-verified against real footage. The two headline fixes: "
                "merging any genuinely square (1:1) source clip with the default \"Crop to fill "
                "16:9\" option crashed outright — the crop math was impossible by construction "
                "for a square frame, no matter which baseline you'd picked. And \"Compatible "
                "playback master\" with GPU encoding could crash partway through on real mixed "
                "footage (camera originals mixed with re-encoded clips) — traced to an ffmpeg "
                "limitation at the exact point two differently-encoded segments meet. Both are "
                "genuine crashes on ordinary, common inputs, not edge cases. Also: a camera-audio "
                "verification check that could wrongly cry \"unexpected mismatch\" now correctly "
                "recognises the case ahead of time and explains it honestly; a leftover hidden "
                "track from some cameras' chapter markers no longer rides along into your master; "
                "two merges running at once (two copies of the app, or a merge alongside a "
                "WhatsApp export) no longer share one scratch folder and risk stepping on each "
                "other's temporary files; a permissions problem on your output folder is now "
                "caught before a merge runs, not after; and four smaller display fixes (an "
                "unreadable Status column at smaller window sizes, overlapping text on the empty "
                "Merge tab, a clipped button label, and stale About-tab copy).",
        details=[
            "Fixed: any genuinely square (1:1) source clip — common on GoPro-style \"square "
            "mode\" recordings — crashed the whole merge outright when \"Square clips\" was left "
            "on its default \"Crop to fill 16:9\", for every baseline you could pick. The crop "
            "math assumed the frame always needed to lose width to reach 16:9, which is "
            "impossible for a source that's exactly as tall as it is wide (or for a square/"
            "portrait baseline, where the assumption was wrong on a different axis) — ffmpeg "
            "rejected the request and the merge silently produced nothing. The crop direction is "
            "now worked out from the actual target shape, landscape, square, or portrait alike.",
            "Fixed: \"Compatible playback master\" with GPU (hardware) encoding could crash "
            "partway through — \"Function not implemented\" — on a real mix of camera-original "
            "and re-encoded clips, which is the ordinary case for most real shoots. Root cause: "
            "ffmpeg has to restart its internal video pipeline at the exact point where two "
            "differently-encoded segments meet, and the hardware-encode pipeline can't be "
            "restarted mid-stream. The merge now keeps one continuous pipeline across every seam "
            "and corrects a resulting colour-brightness mismatch that would otherwise sneak in — "
            "verified directly against a software re-encode of the same footage, matching to "
            "within a fraction of a percent, at genuine hardware-encode speed (about 1.7x "
            "realtime here, versus roughly 0.6x for the equivalent software encode).",
            "Fixed: a camera-audio verification check could report an alarming \"unexpected "
            "mismatch — nothing to explain it\" for a clip that was always going to fail that "
            "particular check, for an ordinary and fairly common reason (the clip's own audio "
            "carries a few encoder priming samples that a plain baseline copy can't preserve "
            "the same way a dedicated archival track does — nothing to do with data loss). The "
            "app now recognises this ahead of time and reports it as a clear, expected "
            "explanation instead of a scary unexplained failure — confirmed against both "
            "synthetic test footage and a real camera file that showed the same pattern.",
            "Fixed: some cameras embed their own hidden chapter/marker track in their video "
            "files. That track could ride along into your delivered master as a stray, unlabeled "
            "extra stream. It's now correctly dropped at every step that touches the original "
            "footage, so a delivered master only ever contains this app's own (correct, "
            "clip-named) chapters, never a leftover from the source camera.",
            "Fixed: two merges running at the same time — two copies of the app, or a merge "
            "running alongside a WhatsApp/share export — used to share one single scratch "
            "folder for their temporary per-clip files, with no separation between them. "
            "Confirmed directly as a real collision, not just a theoretical one: each merge's "
            "own cleanup step could delete the OTHER merge's in-progress files outright. Every "
            "merge and export now gets its own private scratch folder.",
            "New: the output folder is now checked for write access before a merge starts. "
            "Previously a permissions problem there was only discovered after the entire merge "
            "had already run, right at the final save — now it's caught instantly, before any "
            "work begins.",
            "Fixed: the Merge tab's clip-list Status column (the one place that shows whether a "
            "clip will stream-copy or transcode) could shrink to one or two unreadable "
            "characters at smaller window sizes, hiding the single most useful thing that "
            "column tells you. It now keeps a sensible minimum width; the table scrolls "
            "sideways instead of squeezing it away.",
            "Fixed: the Merge tab's empty-state message (\"Select a folder of clips to begin…\") "
            "could show its heading and its explanatory paragraph overlapping each other, in "
            "both themes. The paragraph's height wasn't being recalculated once its real font "
            "size took effect, so the layout reserved too little room for it.",
            "Fixed: the Extract tab's \"Create folder…\" button clipped its own leading \"C\" at "
            "default window size. Its width is now sized from the actual button text instead of "
            "a fixed guess.",
            "Updated the About tab's description of the Extract and Recover tab, which still "
            "referred to it by its old name (\"The WhatsApp clip tab\") and didn't mention its "
            "recovery half at all.",
        ],
    ),
    HistoryEntry(
        date="2026-07-16",
        title="v1.4.002 — fixed a merge failure on camera clips with a hidden metadata track",
        summary="Fixed a real crash: merging clips from cameras that embed a hidden metadata "
                "track (e.g. Google Pixel motion-photo / telemetry data) could fail outright "
                "when building the playable master — the merge died with an \"incorrect codec "
                "parameters\" error while writing the file. The app now correctly ignores that "
                "non-audio/video data track instead of trying to copy it into the master. "
                "Affected both CPU and GPU encoding; found and fixed against the real footage "
                "that triggered it.",
        details=[
            "Fixed: a merge could fail with \"Re-encoding into one smooth, compatible take "
            "failed\" / \"Could not write header (incorrect codec parameters?)\" when any source "
            "clip carried a data track that isn't video or audio — common on phones (Pixel "
            "motion-photo/timed-metadata) and action cams (telemetry). The concatenation step "
            "was copying that track blindly into the .mov master, which the format can't store, "
            "so the whole write failed at the last stage.",
            "The master-building steps now map only real video and audio (matching what the "
            "archival-track step already did), so those hidden data tracks are dropped cleanly. "
            "This affected both the \"Compatible playback master\" re-encode and an ordinary "
            "stream-copy master, on software AND GPU encoding — it was never GPU-specific, it "
            "was just first hit while testing the new GPU pipeline options.",
        ],
    ),
    HistoryEntry(
        date="2026-07-15",
        title="v1.4.001 — choose your decode + encode pipeline in Pre-flight",
        summary="Pre-flight now lets you pick how the merge does its heavy lifting: which "
                "combination of video DECODE (CPU or GPU) and ENCODE (CPU or GPU) to use. "
                "There's a recommended automatic default that picks the fastest combination "
                "measured for your machine; untick it to choose your own. Also starts a new "
                "version-numbering scheme — every change from here bumps the third number "
                "(v1.4.001, v1.4.002, …).",
        details=[
            "New: a \"Processing pipeline\" section in Pre-flight. Leave \"Use recommended "
            "settings\" ticked and the app picks the fastest decode+encode combination it "
            "measured for this hardware (on a GPU-equipped machine: hardware encode with "
            "software decode — the quickest overall; full hardware decode frees the CPU but "
            "runs a little slower). Untick it to set video decode and video encode each to "
            "Software (CPU) or Hardware (GPU) yourself.",
            "Hardware options are offered only when a working GPU encoder is actually present; "
            "on a machine without one they're shown greyed-out so you can see the option exists "
            "and why it's unavailable, and any hardware choice safely falls back to the CPU.",
            "Both merge stages honour the choice — the per-clip conversion AND the single "
            "\"Compatible playback master\" re-encode — so the whole merge follows one pipeline.",
            "The Merge tab's \"GPU encode\" tickbox still works as a quick shortcut for the "
            "encode half; the fuller decode+encode control lives in Pre-flight.",
            "Version numbers now increment by one on every change (this build is v1.4.001).",
        ],
    ),
    HistoryEntry(
        date="2026-07-15",
        title="Real GPU-accelerated transcoding on Linux (AMD/VAAPI), and a clearer error when ffprobe itself is broken",
        summary="GPU encoding on Linux was silently falling back to the CPU on AMD hardware — "
                "the detection only knew about NVENC/QSV/AMF (AMF is Windows-only; AMD's real "
                "Linux path is VAAPI), so \"Use GPU\" never actually engaged on this machine's "
                "Radeon iGPU, in either the per-clip transcode step or the \"Compatible playback "
                "master\" re-encode pass. Both are now properly GPU-accelerated end to end, "
                "verified with real hardware encodes against real footage. Also: if the bundled "
                "ffmpeg/ffprobe binaries are ever corrupted or missing hardware support, the "
                "Extract tab now says so directly instead of the misleading \"No chapter markers "
                "found in this file.\"",
        details=[
            "Fixed: GPU-accelerated transcoding did nothing on AMD graphics on Linux — "
            "detection only tried NVENC (NVIDIA), QSV (Intel), and AMF (AMD's Windows-only "
            "driver API, with no Linux equivalent), so \"Use GPU\" silently fell back to a "
            "slow CPU encode on any AMD Linux machine. VAAPI — the real way AMD (and Intel) "
            "GPUs accelerate video on Linux — is now detected and used, confirmed with a real "
            "hardware encode running noticeably faster than software on real footage.",
            "Fixed: the same CPU-only fallback was also silently happening in the \"Compatible "
            "playback master\" option's final re-encode pass, independent of the per-clip fix "
            "above and easy to miss since it's a single long re-encode rather than several "
            "short ones — that pass is now GPU-accelerated too when a working GPU encoder is "
            "available.",
            "Fixed: if the bundled ffmpeg/ffprobe binaries are ever corrupted, incompatible, or "
            "otherwise fail to run, the Extract tab used to report the same message as a file "
            "that genuinely has no chapters (\"No chapter markers found in this file\") — an "
            "actual tool failure and an empty-but-valid result looked identical. It now tells "
            "you plainly that the probe itself failed, rather than leaving you to guess the "
            "file was the problem.",
            "Safety: the hidden Developer option that lets you force GPU decode for 4K 10-bit "
            "HEVC in Review now spells out the real risk and asks you to confirm before it "
            "turns on — that content can hard-crash the whole computer (confirmed on very "
            "different machines, a Windows laptop and a Steam Deck alike), not merely freeze "
            "playback. If you do enable it, the Review tab's \"Software decode\" box now warns "
            "you right there before you uncheck it. The automatic protection for everyone who "
            "leaves that option alone is unchanged.",
        ],
    ),
    HistoryEntry(
        date="2026-07-13",
        title="Fixed a real audio-mix crash, HDR footage failing to merge, a freeze-frame cause, and more",
        summary="Found and fixed three real bugs: merging a clip with BOTH a Primary-audio "
                "override AND a separate Mixed Audio track could crash outright with an "
                "ffmpeg filter-graph error (caught live from a real \"Failed\" dialog); "
                "merging HDR/BT.2020 footage (common on modern phones) could also fail "
                "outright with an ffmpeg error; and a transcoded HDR clip's verification "
                "could report a false \"unexpected mismatch\" alarm even when everything "
                "actually recovered correctly. Also: if a clip's WAV backup ran even "
                "slightly longer than its own video — very common, and dramatic for a "
                "camera's file-split clips — the merge let that extra audio silently "
                "stretch the clip's segment in the master, holding the video on a frozen "
                "last frame for the difference. Plus: Compatible playback master now offers "
                "ProRes as an alternative to H.264, Pre-flight can run diagnostic checks on "
                "your clips before merging, the pre-flight diagram that wasn't landing well "
                "is reverted, and Select all/Select none was added to the Merge tab's clip "
                "list.",
        details=[
            "Fixed: merging a clip with both a Primary-audio override AND a separate "
            "Mixed Audio track enabled could fail outright with \"Output with label 'mix' "
            "does not exist in any defined filter graph, or was already used elsewhere\" — "
            "both settings tried to reuse the same internal audio-mix result, which ffmpeg "
            "only allows to be used once. Each now gets its own copy.",
            "Fixed: merging HDR video (common on modern phones — anything shot in HLG/HDR "
            "mode) could fail outright with an ffmpeg error when that clip needed "
            "re-encoding, because the app was feeding one probed color value into three "
            "different settings that each need their own — found and fixed by testing "
            "against real HDR phone footage.",
            "Fixed: right after the above, verifying a merge that included an HDR clip "
            "could wrongly report \"unexpected mismatch, worth a closer look\" for that "
            "clip's video and audio — alarming, but not a real data problem, since that "
            "footage was always going to be re-encoded rather than kept byte-for-byte. "
            "Verification now correctly recognizes this as expected and reports a clean "
            "pass with an honest explanation instead.",
            "Fixed: a clip whose WAV backup runs longer than its own video (common — a "
            "WAV recorder often keeps rolling a beat past the camera stopping; dramatic "
            "for a camera's file-split clips, where the WAV can still carry the ENTIRE "
            "next clip's audio) no longer stretches that clip's segment in the merged "
            "master. Previously the extra audio silently extended the segment, holding "
            "the video on its last frame for the difference and delaying every clip after "
            "it by the same amount — found by investigating a real report of odd playback "
            "around a camera file-split, and confirmed directly against the real numbers "
            "from a real merge before being fixed.",
            "New: Compatible playback master can now re-encode to ProRes (Proxy, Standard, "
            "or HQ) instead of H.264 — useful when your footage is heavy to decode and "
            "re-encode (e.g. 4K 10-bit) and you want an edit-friendly intermediate rather "
            "than a delivery file.",
            "New: Pre-flight can run diagnostic checks on your selected clips before you "
            "merge — pick from container/stream structure, timestamp and keyframe "
            "integrity, stream-copy compatibility, a quick decode sample scan, or a full "
            "decode scan (the last one can take a few minutes per 4K clip, so it's off by "
            "default). Results show up directly on each clip's card. These are purely "
            "informational — a finding never stops you from starting the merge.",
            "Investigated a real report of clip 026 (plays fine, converts to ProRes "
            "cleanly, but was tricky to stream-copy or convert to H.264/upload to "
            "YouTube) in full: packet structure, stream-copy compatibility, and a "
            "complete H.264 re-encode with freeze detection all came back clean on the "
            "clip itself — the freeze-frame cause turned out to be the merge-timeline bug "
            "above, not damage in the file. A separate, not-yet-fixed issue (two copies "
            "of the app sharing one fixed temp folder) was also found and is flagged for "
            "its own fix.",
            "Reverted the pre-flight \"big picture\" diagram (added recently) after feedback "
            "that it wasn't helpful and didn't look good — pre-flight is back to the plain "
            "summary and per-clip breakdown.",
            "New: \"Select all\" / \"Select none\" buttons for the Merge tab's clip list, "
            "matching the Extract tab.",
        ],
    ),
    HistoryEntry(
        date="2026-07-12",
        title="Extract-tab layout fix + a genuinely accurate progress/time-remaining readout",
        summary="The Extract tab's rows could visually overlap at higher display-scaling "
                "settings — fixed. Both the Merge and Extract tabs now show a precise "
                "percentage, the current and expected total data size in GB, and a total-"
                "time-remaining estimate that's deliberately conservative (it would rather "
                "finish early than run late) — including a plain-language \"completes by\" "
                "clock time and date.",
        details=[
            "Fixed: the Extract tab's clip list, format picker, and output-folder rows "
            "could overlap on screen at higher Windows display-scaling percentages. The "
            "tab now scrolls instead of squeezing everything into a fixed height.",
            "New: both tabs now show the percentage complete to two decimal places, plus "
            "how much data has been produced so far against the expected total (e.g. "
            "\"3.19 / 25.89 GB\") — previously only Merge showed a rough running total, "
            "and Extract showed no size information at all.",
            "New: a redesigned time-remaining estimate. The old one assumed every remaining "
            "percent would cost the same time as the average percent so far — which runs "
            "overly optimistic whenever the slow part of a job (like a transcode) comes "
            "after the fast part (like a stream copy), which is the usual case. The new "
            "estimate weighs work by its real size and deliberately leans toward the "
            "slower, safer prediction, then shows it as a total duration and a plain \"will "
            "complete by\" clock time and date.",
            "New: the Extract tab now shows live transfer speed and a size estimate the "
            "same way the Merge tab always has — previously it only showed \"clip 3 of 8\" "
            "with no sense of how much data or time was left.",
        ],
    ),
    HistoryEntry(
        date="2026-07-11",
        title="Fixed audio-only exports (Advanced output → video unchecked)",
        summary="Exporting with video unchecked in Advanced output could fail the merge, and no "
                "crash log was left behind to explain why. Both are fixed, along with two related "
                "gaps found while testing: recovering a clip from an audio-only master (via "
                "Extract, or this app's own MD5 verification) could hit the same kind of error, "
                "and a merge-failure log could occasionally go missing even when the on-screen "
                "error dialog appeared.",
        details=[
            "Fixed: exporting audio-only with Archival master also on could fail with an ffmpeg "
            "\"stream map\" error while assembling the final file. The step that combines the "
            "watchable copy with the originals always expected a video track to exist, even when "
            "the export deliberately had none.",
            "Fixed: recovering a clip from an audio-only master — either through this app's own "
            "verification pass or the Extract tab's \"recover original clips\" feature — could hit "
            "the same kind of error, since the recovery step also assumed video was always "
            "present. Recovery/verification now correctly recognise \"no video in this export\" "
            "and skip the video-specific checks with a clear explanation instead of a raw error.",
            "Hardened the failure-log writer: building the detailed per-clip breakdown for a "
            "failed merge's log entry could, in rare cases, throw an error of its own — which "
            "meant the failure never got logged at all, even though the on-screen error dialog "
            "still appeared. A failed merge now always leaves a log entry, even if the detailed "
            "breakdown can't be built for some reason.",
        ],
    ),
    HistoryEntry(
        date="2026-07-10",
        title="Detect a camera's file-split WAVs, show WAV length, and add per-clip transcode/proxy control",
        summary="Investigating a real \"odd playback\" report on one clip led to a genuinely "
                "interesting finding: a camera splitting one long recording into two video "
                "files while its separate audio backup kept rolling as a single file. The app "
                "now spots that pattern automatically and offers to fix it. Also added: a WAV "
                "Duration column, the ability to manually force an individual clip to "
                "transcode (or use its camera's low-res proxy instead) while always keeping a "
                "byte-exact backup of the original, and an option to preserve that low-res "
                "proxy too.",
        details=[
            "New: when a clip's WAV backup looks like it also covers the NEXT clip (the camera "
            "split one continuous recording into two files, but the audio recorder didn't), "
            "the clips list shows an inline notice with a \"Review & resolve…\" button. It "
            "explains the finding with a visual timeline and offers to split the WAV file and "
            "pair each half correctly, leave things as they are, or dismiss the suggestion.",
            "New \"WAV Dur\" column in the clips list, next to the existing WAV checkmark.",
            "New: click a clip's status badge (the green \"Stream copy\" / orange \"Will "
            "transcode\" pill) to manually control how that clip's video is handled — force it "
            "to transcode even if it already matches your settings, or (if the camera saved a "
            "low-res proxy alongside the original) conform that smaller, faster-to-encode proxy "
            "into the master instead. Either way, the original full-resolution file is always "
            "kept byte-exact on its own recoverable track.",
            "New tickbox in the same dialog (only shown when a low-res proxy exists): \"Also "
            "preserve the LRV proxy on its own track\" — keeps a lossless copy of the camera's "
            "own proxy file as an extra backup, independent of which video actually plays. Off "
            "by default.",
            "Investigated the reported clip 026 playback issue directly: compared it against "
            "every neighbouring clip's encoding, frame timing, and dropped-frame count. Clip 026 "
            "turned out to be one of the cleanest clips in the whole recording — the likely "
            "explanation is the missing-WAV silence already fixed earlier today, not a new issue.",
            "Fixed a real bug found on an actual merge: turning on \"Also preserve the LRV proxy "
            "on its own track\" (or the equivalent WAV option) could fail the whole merge with a "
            "\"Tag text incompatible with output codec id\" error. Caused by that step copying "
            "over a hidden internal chapter marker alongside everything else, which clashed with "
            "the new file's own chapters. Now copies only the video and audio, so nothing hidden "
            "gets carried across.",
        ],
    ),
    HistoryEntry(
        date="2026-07-10",
        title="Extract tab: option to ignore a master's manifest and extract manually instead",
        summary="Added a new \"Ignore manifest — use manual controls instead\" tickbox to the "
                "Extract tab. Even when a master's manifest is found and would normally be used "
                "automatically, you can now switch to the same manual controls a master with no "
                "manifest gets — useful if the manifest is wrong for some reason, or you just "
                "want manual control over audio roles, video track, rotation, and clip boundaries.",
        details=[
            "The tickbox only appears once a manifest has actually been found in the loaded "
            "master. Ticking it switches immediately to manual mode using the same information "
            "already read from the file — no need to reload.",
            "Un-ticking it goes straight back to the manifest's own recovery plan.",
            "Loading a new master always starts trusting its own manifest again — the override "
            "doesn't carry over between files.",
        ],
    ),
    HistoryEntry(
        date="2026-07-10",
        title="Extract tab: recover preserved WAVs, plus manual controls for masters from other apps",
        summary="The \"preserve this WAV in full\" option added earlier can now actually be "
                "recovered from the Extract tab. Also added a full set of manual controls for "
                "when you load a master video that wasn't made by this app: assign which audio "
                "track is the camera mic vs. a WAV backup, pick a video track if there's more "
                "than one, override rotation, and hand-add or edit clip start/end points — "
                "including for a file with no chapter markers at all, which previously couldn't "
                "be recovered from here at all.",
        details=[
            "Any WAV file you chose to \"preserve in full\" is now included automatically when "
            "you extract that clip, saved alongside the video as \"<clip name> (WAV - preserved "
            "original).wav\". The Extract tab's clip list also shows this file will be recovered "
            "before you click Extract.",
            "New \"Manual controls\" panel appears in the Extract tab whenever a loaded master "
            "has no manifest (e.g. it was made by different software, or is an older/simpler "
            "export from this app). You can assign each detected audio track a role — camera "
            "audio, WAV backup, or ignore — pick which video track to use if the file has more "
            "than one, and force a rotation of 0/90/180/270° if the file's own rotation flag is "
            "wrong or missing.",
            "Every clip's boundaries can now be hand-edited (name, start time, duration) via a "
            "small dialog, or removed entirely if a guessed chapter boundary is wrong.",
            "Previously, a master with no manifest AND no chapter markers showed \"nothing to "
            "recover\" with no way forward. Now you can click \"+ Add clip…\" to manually define "
            "clip boundaries yourself, one at a time, making any such file recoverable.",
        ],
    ),
    HistoryEntry(
        date="2026-07-10",
        title="Fixed a silent-audio bug, sped up verification, and added per-clip audio control",
        summary="Fixed a real bug where a clip with no WAV backup could play silent instead of "
                "falling back to its camera audio. Made \"Verify MD5 recovery\" smarter so it no "
                "longer runs a slow, doomed-to-fail check on clips it can already tell are fine. "
                "Removed the word \"Bluetooth\" from audio labels. Added a per-clip \"Primary\" "
                "audio column, a proper warning dialog when a reassigned WAV file doesn't match a "
                "clip, and an opt-in way to keep a WAV file preserved in full on its own track. "
                "Also fixed a display bug where clip timestamps showed the wrong hour during "
                "daylight saving time.",
        details=[
            "Clip audio fallback: if the \"primary\" audio choice was set to WAV and a clip had no "
            "WAV backup, that clip could end up silent even though its camera audio was fine — the "
            "camera audio wasn't being used to fill in for the missing WAV. Now it is, automatically, "
            "the same way a missing camera audio track already fell back to the WAV.",
            "Verify MD5 recovery: previously, video and WAV-backup checks on clips buried in the "
            "middle of the file ran to completion and then failed almost every time, even though the "
            "actual footage/audio was fine — a harmless side effect of how the recovery window is "
            "measured. The app now runs a quick check first to confirm that's really what's "
            "happening, and reports an honest pass instead of running the full slow check just to "
            "fail it — while still catching a genuine problem if one is actually there.",
            "Removed the word \"Bluetooth\" from the primary audio selection — it now reads "
            "\"Camera audio (AAC)\".",
            "New \"Primary\" column on the clips list lets you manually choose Camera, WAV, or Mix "
            "as the default audio for an individual clip, instead of only a folder-wide setting. "
            "Overridden clips are highlighted so you can see at a glance which ones you've changed.",
            "Reassigning or disconnecting a clip's WAV file now checks whether the new file actually "
            "matches that clip's length. If it doesn't, you get a dialog explaining the mismatch with "
            "four choices — trim automatically (recommended), align to the clip's start, align to "
            "its end, or don't use this WAV at all — plus an optional tickbox to also keep the "
            "original WAV file preserved in full on its own track, in case you want it later. This "
            "preserve option is off by default.",
            "Timestamp column: clip times were shown in raw UTC instead of your local time, so every "
            "clip recorded during daylight saving time showed a \"differs from filename\" warning "
            "even though nothing was actually wrong. Timestamps now convert to local time correctly, "
            "and the warning only appears for a genuine clock mismatch.",
        ],
    ),
    HistoryEntry(
        date="2026-07-09",
        title="Fixed a real background-rendering bug behind every checkbox and label, and redesigned Quality target",
        summary="Found and fixed the actual cause of a \"cluttered, lined\" look across the "
                "Merge tab — a faint bar behind every checkbox, label and row. Two layers of "
                "the same problem: a subtle background gradient, and every small widget painting "
                "its own slightly-off background colour on top of the section cards. Both are "
                "now gone — controls sit cleanly on their cards with nothing painted behind "
                "them. Also redesigned the Quality target picker into properly separated, "
                "bordered cards instead of a tight stacked list.",
        details=[
            "Root cause (two layers of the same clash): first, the tab background used a subtle "
            "three-stop radial gradient; second — and the one that persisted after removing the "
            "gradient — every small widget (checkbox, radio, label) painted its OWN flat "
            "background in the page colour, which is a shade off from the lighter section-card "
            "colour it sits on. So each row showed as a faint full-width bar against its card, "
            "in both light and dark. Fixed properly: the tab background is now one flat colour "
            "(no gradient), AND labels/checkboxes/radios now paint no background at all — they "
            "let their card show through, so there's no second shade left to form a bar. "
            "Anything that genuinely needs a fill (a badge, an input, a card) still sets its "
            "own and is unaffected. The three now-unused gradient-stop colours were removed "
            "from the theme definitions rather than left behind.",
            "Quality target (Merge clips → Archival & delivery) is now four separate cards, each "
            "with its own full border, rounded corners and padding, with real spacing between "
            "them — replacing a tightly stacked list where each option only had 1px between it "
            "and the next. The recommended option gets an accent-colored border and an actual "
            "\"Recommended\" badge instead of a plain inline star. Extra spacing was also added "
            "between the Archival/Verification/Delivery checkbox groups so the section reads as "
            "a few distinct clusters rather than one long flat list.",
        ],
    ),
    HistoryEntry(
        date="2026-07-09",
        title="More control over audio sync, a much smarter (and faster) MD5 verify, and a visual Pre-flight",
        summary="Three related upgrades to the Merge tab: pick exactly how a clip's WAV "
                "backup lines up (auto, start, or end) and override its drift correction "
                "by hand; MD5 verification now recognises when a check can't possibly "
                "pass and skips it instead of wasting time proving the obvious; and "
                "Pre-flight now opens with a film-strip/tape-reel diagram of the whole "
                "merge, not just a list of numbers.",
        details=[
            "Advanced sync (double-click a clip's Offset/Drift cell, or the toolbar "
            "button) now has an Alignment control — Auto, \"align to clip start\", or "
            "\"align to clip end\" — for the rare clip where a WAV recorder ran far "
            "longer than its video and the automatic detection needs a nudge in a "
            "specific direction. A new Drift control lets you turn tempo correction off "
            "entirely for the mix track, or dial in your own ms/min if you trust your "
            "ear over the measurement — the lossless WAV backup itself is never "
            "resampled either way. A \"Reassign WAV file…\" button is now right there "
            "too, so fixing a wrongly-paired clip doesn't mean closing the dialog first.",
            "MD5 verification is now smarter about its own limits: some checks are known "
            "before they even run to be unable to pass — a clip that had to be re-encoded "
            "with no archival backup, or camera audio sitting mid-way in a shared "
            "archival track — purely from how the merge is configured, not from what the "
            "footage actually contains. Previously the app extracted and hashed both "
            "sides anyway just to arrive at \"yes, as expected, no match\" — now it skips "
            "straight to that conclusion, saving real time on masters with several such "
            "clips. A new \"Skip checks predicted to fail\" tickbox (on by default) lets "
            "you force full, exhaustive verification instead if you'd rather everything "
            "actually run.",
            "Pre-flight now opens with a static picture of the whole merge above the "
            "usual per-clip numbers — the same film-strip-and-tape-reel visual language "
            "as the animated \"✨ Show me\" button, but frozen at its final frame so "
            "there's no waiting: every clip already sitting on the reel or in the vault, "
            "audio shelves showing where camera/WAV/mix tracks land. Reuses the exact "
            "same visualisation Show Me animates, just shown as a snapshot instead of "
            "played out, so what Pre-flight reports in words is also visible as a "
            "picture at a glance.",
        ],
    ),
    HistoryEntry(
        date="2026-07-09",
        title="Fixed a real WAV sync bug: a wireless mic recorded much longer than its clip could scramble that clip's audio entirely",
        summary="Found and fixed the cause of a reported \"completely mismatched\" audio "
                "problem: when a WAV backup recording runs dramatically longer than its "
                "video (a wireless mic left running across a break, say), the app's sync "
                "detection could pick a wildly wrong alignment — embedding audio from "
                "several minutes away from where it belongs into that one clip. Fixed with "
                "a coarse, whole-clip check that catches this case and finds the real "
                "alignment instead.",
        details=[
            "Root cause: audio sync assumes a camera and its separate WAV/wireless mic "
            "stop recording at roughly the same moment, then fine-tunes from there in a "
            "half-second search window — solid for ordinary handling (measured directly: "
            "real clips differ by 0.3-0.4 seconds). One clip in a real shoot had a WAV "
            "backup 385 seconds longer than its video — the mic had clearly kept running "
            "well beyond that one clip. The half-second search window searched nowhere "
            "near the true alignment, so the app locked onto a badly wrong offset and "
            "embedded audio from over six minutes away from where it actually belonged, "
            "for that clip's entire duration.",
            "Fixed by checking the durations first: when a clip and its WAV backup differ "
            "by more than a few seconds, the app now runs a coarse, whole-clip comparison "
            "before fine-tuning — the loudness pattern across the whole recording (not "
            "exact waveform matching, which the two different microphones don't share "
            "closely enough) — and only trusts that coarse result once it's a clear, "
            "confident match. Verified directly against the real clip that exposed this: "
            "the old approach would have picked a −384.5 second offset (completely "
            "wrong); the fix correctly finds −0.6 seconds, in line with the ordinary "
            "startup lag every other clip in the same shoot shows.",
            "If you're seeing audio suddenly go wrong partway through a master and never "
            "recover for the rest of that clip, it's very likely this exact issue — "
            "re-running the merge will pick up the fix.",
        ],
    ),
    HistoryEntry(
        date="2026-07-08",
        title="Review tab: watch the original clips inside an Archival master, not just the finished movie",
        summary="A master built with Archival master on quietly carries the untouched original "
                "video of every odd-spec clip on its own hidden track — you could only ever "
                "recover them, never actually look at them. A new Video source picker in the "
                "Preview section now lists every one of those originals by name; picking one "
                "plays straight from it, at its own real spec, and stops cleanly at the end of "
                "that clip instead of running on into whatever the archival track holds next.",
        details=[
            "The Preview section gets a new \"Video source:\" dropdown — \"Master (playable)\" "
            "plus one entry per original clip that has its own archival track. It only appears "
            "for a master that actually has archival tracks; a master built with Archival "
            "master off (nothing to show) leaves the Preview section exactly as it was.",
            "Picking a clip switches playback to its real, untouched video — jumping straight "
            "to where that clip actually sits, even when several originals share one archival "
            "track (grouped mode) rather than each getting its own. A readout under the "
            "dropdown names what you're looking at and its real spec, e.g. \"Viewing original: "
            "VID_0042.MP4 (HEVC 3840×2160 10-bit 59.94fps)\" — handy for confirming an odd-spec "
            "clip really was preserved as filmed. Playback automatically stops the instant that "
            "clip's own footage ends, rather than spilling into a neighbouring original spliced "
            "onto the same archival track.",
            "Works identically whether hardware or software decode is active — switching video "
            "source mid-playback, or flipping Software decode while viewing an original, both "
            "keep pointing at the same track. Picking \"Master (playable)\" returns to the "
            "normal finished movie.",
        ],
    ),
    HistoryEntry(
        date="2026-07-08",
        title="Review tab: real thumbnails fixed, Overview moved above Audio and aligned, loading spinners, and a 480p fast-preview mode",
        summary="Fixed a real bug that silently produced zero overview thumbnails for "
                "virtually every real camera clip. The Overview timeline now sits above "
                "Audio tracks and its video row lines up with the waveform lanes below "
                "it. Both sections show an animated spinner while their thumbnails/"
                "waveforms are loading. And a new Fast preview (480p) mode plays a small, "
                "pre-rendered proxy that any GPU decodes instantly, for much smoother "
                "scrubbing on heavy source footage.",
        details=[
            "Root-caused the missing thumbnails: ffmpeg's MJPEG encoder outright rejects "
            "standard \"tv\"/limited-range colour — which is what virtually all real camera "
            "footage is — failing silently (the error text was swallowed) and producing "
            "zero filmstrip tiles. Every real master was affected; only synthetic "
            "full-range test clips happened to work. Fixed by forcing full-range colour "
            "before the thumbnail encode.",
            "Reordered the Review tab: Overview now sits above Audio tracks, reading as "
            "the navigator both the video and the audio lanes are checked against. The "
            "Overview's own video timeline is now offset to start at the exact same x "
            "position as the audio waveforms below it (labelled \"Video\" to match), so "
            "the two read as one continuous, aligned timeline instead of two unrelated "
            "strips.",
            "Both the Overview and Audio tracks section headers now show a small "
            "spinning-arc indicator with a caption (\"Loading thumbnails…\" / \"Loading "
            "waveforms…\") for as long as their background extraction is actually "
            "running, then disappear — a slow pass on heavy footage now visibly reads as "
            "working, not stuck.",
            "New Fast preview (480p) checkbox next to Software decode: the first time a "
            "master loads, a small 480p H.264 proxy renders in the background (cached, so "
            "reloading the same file is instant next time) with the same audio-track "
            "order as the master. Turning it on live-swaps playback to the proxy — a "
            "plain, low-resolution stream any GPU decodes trivially, unlike the master's "
            "own resolution/codec/bit-depth — while the exact-frame scopes reading, "
            "snapshots, waveforms and the finished export all keep using the real master "
            "untouched. The checkbox stays disabled with a \"preparing…\" tooltip until "
            "that master's proxy is ready.",
        ],
    ),
    HistoryEntry(
        date="2026-07-08",
        title="Progress now shows exactly what's happening — and archival defaults adapt to your footage",
        summary="The merge progress bar now names the actual work in progress (stream copy, "
                "transcode, merge, archive, MD5 verify) so a slow step never looks like a "
                "hang. Archival defaults are now chosen automatically from your clips: a "
                "single camera shooting one consistent spec gets Archival master only, "
                "while a folder with varied clips gets the full safety net.",
        details=[
            "A small coloured badge (STREAM COPY / TRANSCODE / MERGE / ARCHIVE / MD5 "
            "VERIFY) plus a plain-language status line now sit above the progress bar, "
            "updated live: “Stream-copying VID_003.MP4 — lossless, no re-encode”, "
            "“Transcoding VID_004.MP4 — GPU: NVENC (different fps)”, “Verifying "
            "VID_002.MP4 (2/8) — MD5 pass against the original”, and so on through every "
            "stage including archival building and the final MD5 pass.",
            "Fixed a related display bug: the MD5 verify pass reused the same clip-progress "
            "numbering as the merge itself, so watching it run made the already-finished "
            "clip pills flicker back to “idle” one by one — as if the merge were undoing "
            "itself. Verify now gets its own pill and leaves the completed ones alone.",
            "Archival defaults: loading a folder where every clip already matches one "
            "spec (typically a single camera) now turns on Archival master only — One "
            "track per clip and Optimize baseline for delivery start off, since nothing "
            "odd-spec exists for them to protect. A folder with varied clips (multiple "
            "cameras or specs) automatically turns all three on, so every odd-spec "
            "original is individually recoverable and the delivered file plays "
            "consistently. Touching any of the three checkboxes yourself switches off "
            "auto-selection for that folder — your choice is never silently overridden; "
            "loading a new folder starts the automatic choice fresh.",
        ],
    ),
    HistoryEntry(
        date="2026-07-08",
        title="New “Show me” animation — watch what your merge will do before it runs",
        summary="A new ✨ Show me button next to Pre-flight plays a short, friendly animation "
                "of exactly what this merge will do with YOUR clips and settings — which are "
                "copied exactly, which get converted and why, and where everything lands "
                "inside the finished file.",
        details=[
            "Your actual clips appear as little film-strip cards (wireless-mic recordings "
            "show as small tape reels). One by one they fly onto the movie reel inside a "
            "keepsake box — the finished MOV file. A clip that already matches the movie's "
            "format sails straight on, labelled “copied exactly, not a single pixel changed”; "
            "one that doesn't passes through a converter ring with the real reason shown "
            "(a different frame rate, resolution, and so on).",
            "The picture adapts to your choices: with Archival master on, each clip's "
            "untouched original also drops into a vault at the bottom (one box per clip, or "
            "grouped by format, matching your setting); camera-sound, wireless-tape and mix "
            "shelves appear only for the audio tracks you've enabled; and with “Compatible "
            "playback master” on, the reel gets a final polish sweep — “re-filmed as one "
            "smooth take, so it plays anywhere”.",
            "A narration line explains each step in plain language as it happens, and Replay "
            "runs it again. Nothing is rendered or touched — it's purely a preview of the "
            "plan, designed so a young person can follow what the app is about to do.",
        ],
    ),
    HistoryEntry(
        date="2026-07-08",
        title="Video verification windows are now measured too — no more false alarms",
        summary="Applied the same measured-boundary fix to the video side: recovery and "
                "verification windows now come from each clip's true measured position in the "
                "master, ending the false ‘decodes differently’ verification failures caused by "
                "a window landing one frame off a clip boundary.",
        details=[
            "Per-frame analysis of a real 8-clip master proved every ‘unexplained’ video "
            "verification failure was the checking window sitting ±1 frame (~33ms) off a clip "
            "boundary — one stray frame from the neighbouring clip changes the checksum even "
            "though the footage itself is pixel-perfect. Masters merged from now on verify "
            "against each clip's measured boundary instead, with tiny safety margins so a "
            "hair of timestamp rounding can never cost a frame.",
            "Verification messages are honest about this now: on older masters the report "
            "explains the estimated-window effect (and points at the diagnostic tool) instead "
            "of saying ‘nothing to explain it’; on new masters a genuine mismatch is flagged "
            "as truly worth a look. The audio message also no longer claims the video decoded "
            "identically without checking that it actually did.",
        ],
    ),
    HistoryEntry(
        date="2026-07-07",
        title="WAV backup recovery now lands on exactly the right samples",
        summary="Fixed the long-standing WAV-backup position drift: recovery and verification "
                "now use the WAV track's own measured position inside the master, instead of "
                "an estimate based on the video that could land a window on the wrong samples.",
        details=[
            "When clips are merged, their segments are laid end-to-end and each one's true "
            "position depends on its own exact length — which the app used to estimate from "
            "the video. If a clip's audio ran even slightly longer or shorter than its video, "
            "recovering its WAV backup could grab the right amount of audio from slightly the "
            "wrong place, and verification would honestly (but confusingly) report a mismatch.",
            "The merge now measures each segment's real position and length as it builds the "
            "master, and records them in the master's recovery manifest. WAV recovery and "
            "verification use those measured values directly. Older masters still work exactly "
            "as before (they fall back to the old estimate); re-merging a folder gives a fully "
            "verifiable WAV backup.",
            "Also added a diagnostic tool (tools/diagnose_midtrack_decode.py) that pins down "
            "WHY a clip inside a merged master decodes differently from its original — "
            "distinguishing a recovery window landing a frame or two off from real damage at "
            "the joins between clips.",
        ],
    ),
    HistoryEntry(
        date="2026-07-07",
        title="Same-camera clips no longer needlessly transcoded over tiny frame-rate noise",
        summary="Fixed clips from one camera at one frame rate being split across different "
                "baselines and transcoded, when they should all stream-copy losslessly.",
        details=[
            "A folder of clips from the same camera at the same nominal frame rate (e.g. "
            "29.97) could show slightly different rates (29.92 / 29.95 / 29.97) and get "
            "flagged for transcoding, because the app read each clip's *measured average* "
            "frame rate — which drifts a few hundredths from clip to clip even when the true "
            "rate is identical. It now reads the clip's stable nominal rate instead, so "
            "same-rate clips group onto one baseline and stream-copy losslessly as intended. "
            "Genuinely variable-frame-rate clips are still detected and conformed as before.",
        ],
    ),
    HistoryEntry(
        date="2026-07-07",
        title="Fixed missing overview thumbnails, and a big batch of Developer options",
        summary="Fixed the Review tab's overview filmstrip (frame thumbnails weren't appearing "
                "at all), and added a large set of new Developer experiments for preview "
                "resolution/window/scaling/speed and the Review overview filmstrip.",
        details=[
            "Fixed a real bug: the frame thumbnails that run along the Review tab's overview "
            "track never appeared. The strip was being reserved and filled correctly, but the "
            "duration signal (which arrives a moment later with GPU decode) was wiping the "
            "reserved slots, so every thumbnail was silently discarded. They now populate as "
            "expected.",
            "Developer window — clip preview: resolution now goes up to 720p, and a new "
            "“window” group lets you set the preview popup size (Small/Medium/Large), video "
            "scaling (fit / stretch / crop to fill), playback speed (0.5× / 1× / 2×), and "
            "whether it loops.",
            "Developer window — Review tab: a new “overview filmstrip” group lets you choose "
            "how many thumbnails run along the overview track (12/24/48) and their resolution "
            "(120/160/240px). Changes regenerate the strip live.",
        ],
    ),
    HistoryEntry(
        date="2026-07-07",
        title="More Developer options, including Review-tab playback experiments",
        summary="Expanded the hidden Developer window into grouped sections: added a preview "
                "resolution choice, and a new Review tab section to experiment with software-"
                "playback smoothness and GPU decode of the tricky 4K 10-bit HEVC profile.",
        details=[
            "The Developer window (triple-click the logo) is now grouped into sections. Clip "
            "preview gained a resolution choice — 160p (default), 240p, or 360p — trading "
            "sharpness against how quickly the preview builds.",
            "New “Review tab playback” section. “Software playback smoothness” sets how often "
            "the picture refreshes in software-decode mode (Smoother / Balanced / Lighter) and "
            "applies live while you're reviewing. “Allow GPU decode for 4K 10-bit HEVC” lets "
            "you experiment with hardware decode on the exact profile that's normally forced "
            "to software for safety — off by default, and it only affects the next master you "
            "open.",
            "As before, every switch is independent and reversible: if one causes a roadblock, "
            "set it back and you're rolled straight back.",
        ],
    ),
    HistoryEntry(
        date="2026-07-07",
        title="Hidden Developer options for experimenting with preview acceleration",
        summary="Added a hidden Developer panel (triple-click the logo, next to the Legacy "
                "toggle) with experimental switches for faster clip previews — each "
                "independent and off by default, so anything that misbehaves can be turned "
                "straight back off.",
        details=[
            "Triple-clicking the logo now reveals a small “⚙ Developer” button next to the "
            "Legacy toggle. It opens a movable window of experimental switches.",
            "First switches are for the per-clip preview: GPU encode (use the graphics card’s "
            "video encoder, with automatic fall-back to the CPU if none works), GPU hardware "
            "decode (-hwaccel), and a fast 2-second ultrafast sample for near-instant preview. "
            "Each is independent and defaults off — turn one on to experiment, off to roll "
            "back if it causes trouble.",
            "Previews are cached per option set, so changing a switch regenerates the preview "
            "with the new settings rather than reusing an old one.",
        ],
    ),
    HistoryEntry(
        date="2026-07-07",
        title="Fixed the clip preview showing a black window",
        summary="The ▶ preview button in the Merge tab opened a window that stayed black "
                "with no video. Fixed how the player loads the preview file.",
        details=[
            "The per-clip preview popup was handing the player a raw Windows file path. The "
            "player reads that as a web-style address, and a path like “C:\\…” makes it think "
            "“C” is the address type — so it never actually loaded the video, leaving a black "
            "window. It now loads the file the correct way and plays the short sample as "
            "intended. If a preview ever genuinely can’t be played, the window now says so "
            "instead of sitting silently black.",
        ],
    ),
    HistoryEntry(
        date="2026-07-07",
        title="Memory shelf follows the theme, comfier dark cards, clearer controls",
        summary="Fixed the Memories shelf not repainting when you switch theme (which left "
                "bright light-mode cards sitting on a dark background), softened the dark-mode "
                "cards so photos rest on a comfortable frame, and made the per-card menu "
                "button clearly visible instead of only appearing on hover.",
        details=[
            "The Memories shelf now repaints instantly when you switch Dark / Light / Auto. "
            "Previously the cards kept whatever look they were first drawn with, so switching "
            "to dark mode could leave harsh bright-white cards on the dark background. Every "
            "card and label now follows the theme the moment you change it.",
            "Dark mode is easier on the eyes: cards sit on a warmer mid-dark surface that "
            "gently frames each photo, instead of bright thumbnails floating on near-black.",
            "The per-card “⋮” menu (rename, reorder, remove) is now always visible as a small "
            "button beside each collection’s name, rather than only showing up on hover.",
        ],
    ),
    HistoryEntry(
        date="2026-07-07",
        title="Readable memory cards in dark mode, and controls to organise them",
        summary="Fixed a dark-mode readability problem on the Memories shelf where each "
                "card's text sat in a harsh dark box, added rename, reorder and remove "
                "controls so you're in charge of your collections, and brought the "
                "smooth-playback option to the classic Merge tab.",
        details=[
            "Dark mode: the title, date and “kept” lines on each memory card were painting "
            "their own dark background over the lighter card, which looked like heavy black "
            "bars and made the text hard to read. The text now sits cleanly on the card in "
            "both light and dark mode.",
            "Every card on the Memories shelf now has a “⋯” menu: rename the collection, move "
            "it left or right to arrange your shelf, or remove it. Removing offers two clear "
            "choices — “Remove from library” simply forgets it here and leaves every file "
            "untouched, while “Delete files…” permanently erases the folder and asks you to "
            "confirm the exact location first.",
            "The classic Merge tab now has a “Compatible playback master” option — the same "
            "clean, one-pass re-encode the guided flow uses to avoid the green/garbled/"
            "freezing playback that stream-copying clips together can cause. Off by default "
            "there (power users often want an exact stream copy); your archival originals are "
            "unaffected either way.",
            "New “Storage options…” decision aid on each collection: a clear side-by-side "
            "comparison of keeping everything as one archival master (the default — one file "
            "that uploads to YouTube and archives long-term) versus also saving every memory "
            "as a separate file plus an album page that opens on any device. The separate-"
            "files layer is entirely optional and added on top; the master is always kept.",
        ],
    ),
    HistoryEntry(
        date="2026-07-07",
        title="Smoother playback everywhere, a friendlier look, and smarter defaults",
        summary="Fixed a real bug where a merged master could play inconsistently across "
                "different video players, gave the app a warmer and more approachable look, "
                "and made the Merge tab suggest an output folder and filename for you.",
        details=[
            "Fixed inconsistent playback: a merged master's main video could freeze, stutter, "
            "or show green/garbled frames on some players and devices, because independently-"
            "encoded clips were being joined without re-encoding — which breaks the video's "
            "internal frame references right at the joins, and every player copes differently. "
            "The guided flow now rebuilds the watchable video as one clean, widely-compatible "
            "stream that plays smoothly everywhere. Your lossless originals are unaffected.",
            "The Merge tab now auto-suggests an output folder (the folder you loaded your "
            "clips from) and a filename based on that folder's name — just a starting point, "
            "always overridable.",
            "A warmer, friendlier look: lighter and airier in light mode, softer and less "
            "severe in dark mode, with gently rounded corners. A hidden switch — triple-click "
            "the logo in the top-right — flips between this new look and the original "
            "\"legacy\" look, so you can compare the two.",
            "Early work on a memories/collections view (the new Memories and Add tabs), part "
            "of a family-friendly rethink of how the app organises and safeguards your footage.",
        ],
    ),
    HistoryEntry(
        date="2026-07-05",
        title="Found and fixed a real recovery bug, plus provable byte-for-byte verification",
        summary="Investigating a real user report found a genuine bug where some clips could "
                "lose their orientation on recovery. Fixed it with a new \"Optimize baseline for "
                "delivery\" option, and added a way to prove — not just promise — that your "
                "originals come back byte-for-byte identical.",
        details=[
            "Root cause: a clip whose picture was rotated (common on some action cameras) but "
            "otherwise matched the merge's target quality was being copied into the shared "
            "master video track as-is — and that sharing process could silently lose the "
            "rotation tag, so the clip would come back sideways when recovered. This only "
            "affected clips sharing the main track, not ones already being converted "
            "individually, and was a pre-existing gap, not something a recent change broke.",
            "New \"Optimize baseline for delivery\" option (Merge tab): converts every clip to "
            "one consistent quality target instead of copying matching ones as-is, closing the "
            "door on this entire category of issue. Requires Archival master and \"one track "
            "per clip\" to also be on, so every original still has a safe, individual backup.",
            "Since the shared track no longer needs to match your camera's exact recording "
            "format, you can now choose the delivery quality yourself: Archival, Master "
            "Quality, YouTube/Streaming (recommended), or Social/Compact — each with a plain-"
            "English explanation of what it's for and how it affects file size.",
            "New \"Verify MD5 recovery\" option: after merging, the app immediately extracts "
            "every clip back out of the finished file and fingerprint-compares it against your "
            "original — video, audio, and backup WAV — and writes a plain-text report you can "
            "keep. If anything doesn't match, you're told immediately, not left to wonder.",
            "The Extract and Recover tab now reads that report automatically: load a verified "
            "master and you'll see a clear confirmation banner before you even start recovering "
            "anything, so you know your footage is safe before you need it.",
        ],
    ),
    HistoryEntry(
        date="2026-07-04",
        title="Extract tab parity, output format choice, and a dark-mode fix",
        summary="The Extract and Recover tab now matches the Merge tab's camera naming, "
                "duration, and preview-button features, plus a new MOV/MP4 output choice and "
                "a fixed dark-mode readability bug.",
        details=[
            "The Spec column now shows the full picture — codec, resolution, frame rate, bit "
            "depth, colour space, rotation, and variable-frame-rate flag — not just codec and "
            "resolution.",
            "The Camera column recognises cameras by their remembered name (see above), even "
            "for masters merged before that camera was named.",
            "Added a Duration column, and the same one-click low-res preview button used in "
            "the Merge tab, so you can check a clip before recovering it.",
            "Added a choice of output format when recovering clips: keep each clip's own "
            "original format, or force everything to MOV or MP4. When forcing MP4, any camera "
            "audio MP4 can't hold (e.g. uncompressed formats some action cameras use) is "
            "automatically split out into its own WAV file instead of being lost or failing.",
            "Added a \"Create folder\" button that suggests an output folder name and location "
            "based on the master file, which you can adjust before creating it.",
            "Added this development history section to the About tab.",
            "Fixed a real readability bug: alternating rows in the Merge and Extract clip "
            "tables showed a bright, washed-out background in dark mode, making text hard to "
            "read. Root cause was a styling rule that only covered one of the two table types "
            "the app uses — now both are covered correctly in both light and dark mode.",
        ],
    ),
    HistoryEntry(
        date="2026-07-04",
        title="Camera memory, resizable columns, quick clip previews",
        summary="The Merge tab now remembers each camera's name for next time, lets you "
                "drag-resize column headers, and adds a one-click low-res preview next to every clip.",
        details=[
            "Once you name a camera, the app remembers it — future folders with the same "
            "camera are recognised automatically, no re-naming needed.",
            "Clip-table columns (Clip, Timestamp, Camera, Duration, WAV) can now be dragged "
            "wider or narrower, e.g. to see a long clip name in full.",
            "A ▶ button next to each clip name plays a short, low-resolution sample starting "
            "from the middle of the clip — a quick way to check footage without opening the "
            "full Review tab.",
            "Looked into using the graphics card to speed up video scaling further; found "
            "this machine's GPU can't support it (a hardware/driver limitation), so scaling "
            "stays on the processor, where it already works reliably.",
        ],
    ),
    HistoryEntry(
        date="2026-07-03",
        title="Real hardware-decode crash found and fixed",
        summary="Tracked down a rare crash/freeze during playback of certain very high-"
                "resolution footage and made the app automatically avoid it.",
        details=[
            "Some 4K-and-above, 10-bit HEVC footage could make the graphics driver stall or "
            "run out of memory during playback, occasionally freezing the whole PC for a "
            "moment.",
            "The app now recognises that specific combination automatically and switches to "
            "a safer, software-only playback mode for those files — your own playback "
            "preference for everything else is untouched, and it switches back automatically "
            "for ordinary footage.",
            "Also fixed a related shutdown crash and made preview/scope rendering during "
            "playback noticeably lighter on memory and CPU.",
        ],
    ),
    HistoryEntry(
        date="2026-07-02",
        title="Faster previews and thumbnails",
        summary="Thumbnails and scrub previews now appear roughly 3-10x faster on large "
                "high-resolution clips.",
        details=[
            "Frame extraction for thumbnails and the Review tab's preview/playback now jumps "
            "straight to the nearest keyframe instead of decoding every frame in between — a "
            "big speed win specifically on 4K 10-bit footage, with no visible difference for "
            "everyday scrubbing.",
        ],
    ),
    HistoryEntry(
        date="2026-07-01",
        title="Real-usage feedback round — bug fixes and polish",
        summary="A batch of fixes and refinements after actually using the app on a real "
                "4-camera multicam shoot: clip loading, camera naming, thumbnails, waveforms, "
                "and more.",
        details=[
            "Fixed a bug where a folder with no unmatched WAV files never got probed at all — "
            "the clip table could stay empty with cameras shown as \"unknown\".",
            "Added a one-time prompt to name each detected camera right after a folder loads.",
            "Fixed overview thumbnails and waveforms/spectrograms not appearing for very large "
            "master files.",
            "Added a loading progress bar for the clip table, and a probe-progress bar so a "
            "slow scan doesn't look identical to a stalled one.",
            "Reordered and renamed tabs for a clearer flow (Merge → Review → Extract and "
            "Recover → Log → About), and moved the \"Share a clip\" tools into the Review tab.",
            "Review tab: locked the preview to a proper 16:9 frame, replaced the zoom controls "
            "with a drag-style slider, and made paused/zoomed-in frames swap to full "
            "resolution automatically.",
            "Extract tab: added chapter-based recovery for master files that don't carry the "
            "app's own recovery metadata.",
        ],
    ),
    HistoryEntry(
        date="2026-06-28",
        title="GPU-accelerated hardware transcoding",
        summary="Added an optional GPU transcode mode that uses your graphics hardware's "
                "video encoder instead of the processor, for much faster merges when clips "
                "need converting.",
        details=[
            "The app automatically detects which GPU video encoders actually work on your "
            "machine (rather than assuming based on hardware name alone) and offers the "
            "fastest one it can confirm.",
            "This is an optional toggle next to the Pre-flight/Start buttons — stream-copied "
            "(already-matching) footage is completely unaffected either way.",
        ],
    ),
    HistoryEntry(
        date="2026-06-24",
        title="Multicam merge overhaul",
        summary="A major rebuild so the Merge tab understands multiple cameras properly: "
                "automatic camera grouping, a chosen quality baseline, and full lossless "
                "originals kept in reserve.",
        details=[
            "Clips are automatically grouped by which camera recorded them (phone, action "
            "camera, drone, gimbal, etc.), using file metadata first and filename patterns as "
            "a fallback — camera groups can be renamed and clips dragged between them.",
            "Instead of forcing every clip to one hardcoded format, the app now looks at all "
            "your footage, recommends the best realistic quality target (without ever "
            "upscaling), and lets you pick a different one if you prefer.",
            "Clips that don't match the chosen format are converted (padded to fit rather "
            "than cropped or stretched, with your choice of black bars or a blurred fill for "
            "vertical clips); matching clips are copied through losslessly.",
            "An optional \"archival master\" keeps every original clip's exact video losslessly "
            "recoverable later, alongside the everyday playback master.",
            "Clips now order correctly by actual recording time (not just filename guesswork), "
            "and audio/video pairing across different camera brands' naming conventions is "
            "far more reliable.",
        ],
    ),
    HistoryEntry(
        date="2026-06-18",
        title="Extract and Recover tab",
        summary="A new tab that can pull the original, camera-native clips back out of a "
                "merged master file — losslessly.",
        details=[
            "Reads the recovery information saved inside a master file (or, for older/foreign "
            "masters, works out chapter boundaries directly) and lets you select which "
            "original clips to recover.",
            "Recovered clips come back out lossless, with their original rotation intact.",
        ],
    ),
    HistoryEntry(
        date="2026-06-10",
        title="Review tab overhaul",
        summary="A proper preview/scrubbing experience: waveform and spectrogram views, a "
                "zoomable timeline, thumbnail filmstrip, and a snapshot tool.",
        details=[
            "Added scroll/pinch zoom on the preview, a timestamp ruler, and audio waveform "
            "lanes that crop to whatever part of the timeline you're viewing.",
            "Added a thumbnail filmstrip along the overview timeline and a styled snapshot/"
            "camera icon for saving a still frame.",
        ],
    ),
    HistoryEntry(
        date="2026-06-01",
        title="Stability pass",
        summary="Fixed a rare bug where the app could close itself unexpectedly, and added "
                "crash logging so any future issue leaves real evidence.",
        details=[
            "A background task's thread wasn't always given time to finish cleanly before "
            "the app let go of it, which could occasionally abort the whole program. Every "
            "background task now waits for a clean handoff, and the app also raised its dark/"
            "light theme contrast and consistency across every screen.",
            "Added a crash log (crash.log, saved next to the app's settings) that captures "
            "unexpected errors so a real problem can be diagnosed with evidence rather than "
            "guesswork.",
        ],
    ),
]
