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
LAST_UPDATED = "2026-07-10 16:56"


@dataclass
class HistoryEntry:
    date: str
    title: str
    summary: str
    details: list = field(default_factory=list)


HISTORY: list = [
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
