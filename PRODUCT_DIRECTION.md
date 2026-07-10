# FuseBox — product direction

> A living design reference. Snapshot as of 2026-07-06, meant to be revised as
> ideas change — nothing here is frozen. Captures the decisions made while
> re-thinking who FuseBox is for and how it should feel. Engineering detail on
> the recovery internals lives in `DEVELOPMENT.md` (Task 78); this doc is the
> product/experience layer.

## One-line product

FuseBox turns the scattered clips of a meaningful moment into one tidy, verified
archive you can always pull your originals back out of — and it proves it by
letting you watch a memory come back, byte for byte.

## Target user

- **Primary — the family / life archivist.** An ordinary person who films both
  big occasions (birthdays, holidays) and small quiet moments (a toddler with
  toys, a sunny afternoon on the decking) and never wants to lose them. Values
  permanence, simplicity, and reassurance over pro controls. Their design needs
  win when a trade-off comes up.
- **Secondary — the "somewhat-pro" owner-type.** Comfortable with the idea of
  verification and manual checking; served through an *advanced layer*, not the
  main path.
- **Anti-user.** The professional mic-shooter / editor chasing a forensic
  multicam pipeline. Still supported (advanced), but no longer who we build the
  front door for.

## Positioning

**Preservation, not editing.** A category of its own, positioned against
edit-first NLEs (Resolve/Premiere) and dumb cloud backup. The differentiator is
trust: *consolidate → verify → recover*, provable. This trades a **technical
moat** (the two-mic sync algorithms) for a **brand/positioning moat** — a
knowing choice: family markets are won on trust, simplicity, and reputation, so
the moat now lives in experience, and must be protected there.

## The promise, and the voice

Honest wording only. We guarantee a **verified, self-contained copy at the
moment of creation, portable and always recoverable** — not open-ended
permanence.

- **Say:** checked · kept · verified · recovered · yours.
- **Avoid:** *forever, permanent, never lose* (permanence we don't guarantee);
  *backup* (implies redundancy we don't provide); *lossless / byte-exact / MD5*
  up front (they live behind a "verify yourself" disclosure).
- **Voice:** speaks warmly to *you* (gentle imperatives, second person — not a
  chatty "I"); confirms in past tense ("kept", "saved", never "successfully");
  buttons are verbs, sentence case, no full stops.
- **Working taglines:** "Your memories, safely kept — and provably yours." ·
  "A verified home for the moments that matter."

## Design philosophy

- **Progressive disclosure** — simple by default, every power still reachable
  underneath.
- **Friendly and approachable** — lighter and warmer than the current
  dark-technical look. The reassurance lives in the copy and a quiet recurring
  "safe" motif (a green shield/check thread), not in extra chrome.
- **Auto-everything, quietly editable** — the app names the collection, picks a
  cover, builds thumbnails, groups by date; the user changes anything they care
  about, after the fact.

## Core mechanic — vault-first archiver

Keep the engine that's already built: fuse → verify → recover. But the payoff
that was quiet plumbing (verification + byte-exact recovery) becomes the
**centrepiece**, and the previous hero (two-mic sync) steps back to advanced.

Technical truth that shapes the promise (see `DEVELOPMENT.md` Task 78):

- A clip on its **own un-concatenated track** (per-clip archival) recovers
  **byte-for-byte** — validated on real 4K footage (4/5 clips byte-identical;
  the 5th was a WAV-backup edge case, not the footage).
- A clip pulled from a **concatenated** track (shared archival, or the baseline)
  is **decode-lossless** (identical pixels/samples) but *not* byte-identical —
  the concat demuxer strips SEI/AUD metadata NALs, and audio has an AAC-priming
  seam. The verification now reports this honestly rather than as a failure.
- **Implication for the "byte for byte" onboarding promise (resolved):** making
  it true for *every* memory is best done by **keeping the originals as files**
  (byte-exact by construction — the "make fully portable" form), NOT by embedding
  conforming clips as archival tracks (that duplicates the baseline, saves no
  space over keeping files, and explodes track counts). The compact default stays
  decode-lossless for conforming clips, described honestly as "exactly as you
  filmed it"; "byte for byte" is reserved for clips that genuinely are (the
  provable demo, the pro layer, keep-originals mode). Each clip now carries a
  `recovery_fidelity` tag (`byte-exact` / `decode-lossless` / `transcoded`) so the
  app can badge and promise per clip.

## The unit — collections

Not a "shoot" or "a day's master." A flexible **collection** that scales from a
single clip to a whole wedding. Auto-grouped by date/location, given a gentle
default name ("Pool day · 3 Jul"), always renameable and splittable. Big
occasions are named deliberately; quiet moments just fall into a sensible bucket
with zero effort. Big events and tiny moments are the *same kind of thing* here
— no ceremony.

## The archive artifact — a self-describing folder

Vault-first, with a browsable face wrapped around it. A collection is a standard
folder anyone can open, on any machine, without FuseBox:

```
Grandpa's 90th — 3 Aug 2026/
  ├─ Grandpa's 90th.mov     the verified vault (one file, safe, compact)
  ├─ clips/ (optional)      the browsable face — real, playable memory files
  ├─ contact-sheet.jpg      one glance = the whole day
  ├─ album.html (optional)  double-click → an album page, no app needed
  └─ verified.txt           plain-English proof
```

- **Walk-away, not exclusive.** The master `.mov` is a standard, chaptered file
  that plays in any player — nothing is ever locked to FuseBox. FuseBox is the
  *preferred* way to browse, never the *required* way to access.
- **Portability on demand.** Default compact (thumbnails); a one-click *"make
  fully portable"* writes real clip files + `album.html` when you want to hand
  the folder to family.

## First-run flow — the seven moments

The pivotal design move: **prove it on one memory in seconds, before committing
to the full archive.** Trust arrives before the wait.

0. **Welcome** — one warm promise, no feature tour.
1. **Add your videos** — one action, zero config; auto-detect does the rest.
2. **What was found** — auto-organised into a named collection, made visible.
3. **The proof — "see a memory come back"** — one memory into the vault and
   straight back, proven identical. The whole pitch in seconds. *(This is the
   centrepiece; it's genuinely byte-exact because a lone clip is its own track.)*
4. **Keep them all** — the full archive runs, now trusted; calm honest progress;
   the compact-vs-portable storage nudge sits quietly inline.
5. **Safe** — a concrete "all 40 memories kept and verified", with a quiet
   "verify yourself" for the pro layer.
6. **Yours to keep** — ends on ownership and no lock-in; "make fully portable"
   available.

Practical note: the moment-3 demo must be *fast* — auto-pick the shortest clip
(or a few seconds) so the aha is near-instant.

## Second visit — the everyday app

The app stops being a tool you operate and becomes a place you return to.

- **Home is a shelf of collections** — cover, name, date, count, a quiet "kept"
  badge. Auto-grouped by year, like a photo library. Feels like opening a
  photo-album drawer, not a file manager.
- **IA shift.** The old power-tool tabs (Merge / Review / WhatsApp / Log)
  dissolve into the right places: *Merge → add memories*, *Review → tap a
  collection to watch*, *share → a per-clip action*, *Log/About → a settings
  menu*.
- **Adding again** is the seven moments *minus the teaching* — skip Welcome and
  the proof (they've felt the aha once): pick a folder → what was found → keep →
  safe. The proof stays available on demand, never forced twice.
- **Revisiting** is a tap → the album face; inside any memory, recovery is one
  click away — *play it, pull the original out, verify yourself*. The thing that
  sells the app on day one keeps earning trust on day 200.
- **Restraint.** The "kept" badges and "all kept" are **past-tense provenance,
  not a live health monitor** — honouring that ongoing integrity is out of
  scope. The home must never look like it's scanning and might flash red.

### App map

A shallow hub-and-spoke, not a tab bar. Home is the room you return to; the
everyday path is a short spine, and everything advanced sits off to the side,
reachable but never blocking.

```
                 Home  (your collections — the hub)
                  │
   add memories   │  open              settings
   ┌──────────────┼───────────────────────┐
   ▼              ▼                        ▼
 Add flow      Collection              Settings          [advanced]
 (seven         (the album)            (cloud · two-mic
  moments)         │  a memory          sync · theme)
                   ▼
                 Memory                 ──►  More         [advanced]
                 (play · save ·              (trim · grade
                  share)                      · verify)
```

- Everyday path (Home → Collection → Memory) is the whole core experience.
- `save` on Memory is the recovery act — "save the original", the everyday face
  of the byte-exact engine.
- Advanced (Settings, More) is where the demoted two-mic sync and edit tools
  live — off the spine, behind disclosure.
- Add flow is the front door: all seven moments for new users, condensed for
  returning ones, landing back on Home with a new collection.

## Storage and cloud

- **Provider-agnostic: "cloud is just a folder."** Keep collections in any
  cloud's desktop-sync folder (Jottacloud, Dropbox, OneDrive, iCloud, Google
  Drive). FuseBox uses the local path; the sync client handles upload/download.
  No per-provider connectors, no lock-in. *(Deep in-app cloud connectors / share
  links = a much bigger, provider-specific build — deferred.)*
- **Light local index + heavy remote payload.** Kept on-device (tiny):
  thumbnails, manifest, verify record, album metadata — so the shelf and album
  render **instantly and offline**. Kept in the cloud (gigabytes): the master
  and/or clip files — **fetched only when you play, pull out, or verify.**
- **Context-aware storage default.** Local-only collection → thumbnails-only
  (compact). Cloud-backed collection → keep per-clip files, so playing one
  memory fetches ~40 MB, not the whole 12 GB vault. FuseBox can auto-detect a
  cloud-sync destination (online-only file attributes / known sync roots) and
  pre-pick the right default; the save-time nudge then explains itself. Always
  overridable ("quietly editable").
- **Ongoing integrity is out of scope** — no background re-hashing (which over a
  metered cloud link would be a bandwidth problem). Manual "verify this
  collection" stays a deliberate, occasional action.

## What recedes to "advanced"

- **Two-mic wireless sync** (GCC-PHAT / drift / slow-mo WAV stretch) — the old
  hero, now a power feature for the few who shoot with a separate mic + WAV.
- **Edit tools** — LUT grading, scopes, waveforms — mostly hidden; they're
  edit-y, not preservation-y.

## Open questions / to revisit

- **Byte-exact for every clip — resolved.** Delivered by keeping originals as
  files (Phase 5 portability), not by embedding conforming clips as tracks. Each
  clip now carries a `recovery_fidelity` tag so the app promises honestly per
  clip; compact-mode copy for conforming clips is "exactly as you filmed it".
- **WAV-backup verification edge case** (a non-first shared-track clip; window
  alignment) — `DEVELOPMENT.md` task #4. Low priority; per-clip mode avoids it.
- **Built as working skeletons, not yet visually polished:** the in-collection
  view, the returning-user "add" flow, and the "make fully portable" branch
  (see `BUILD_PLAN.md`'s "known limitations" for the honest state of each).
  Still genuinely undesigned: the empty-then-populated first-run states.
- **Moat watch.** Having traded technical edge for brand, protect trust and
  simplicity deliberately — that's now the defensible thing.

## Decision log (this session)

| Question | Decision |
|---|---|
| Primary user | Family / life archivist |
| Design balance | Progressive disclosure |
| Differentiator | Preservation, not editing |
| Aesthetic | Friendly and approachable |
| Two-mic sync | Demote to advanced |
| Unit of preservation | Collections (events *and* quiet moments) |
| Ongoing integrity | Out of scope |
| First-run aha | "See a memory come back" (recovery) |
| Core mechanic | Vault-first archiver (keep the engine) |
| Walk-away form | Thumbnails by default; portable on demand |
| Auto vs ask | Auto-everything, quietly editable |
| Cloud approach | Provider-agnostic — "point at your synced folder" |
| Cloud storage default | Keep clip files when cloud-backed |
