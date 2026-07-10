# FuseBox — build plan

> How the product direction (`PRODUCT_DIRECTION.md`) maps onto the *actual*
> codebase, as a phased roadmap. Snapshot 2026-07-06; revise freely. No calendar
> estimates — phases are ordered by dependency and sized small / medium / large.

> **Status (2026-07-06): all seven phases have a first pass landed.** Backend/logic
> phases (0, 1, 3, 5) are complete and unit-tested; UI phases (2, 4) are working,
> offscreen-tested skeletons driving the proven engine; Phase 6 applied the honest
> voice + repositioned the README + kept theme discipline. 26/26 test files green;
> the app imports and runs. Remaining polish is called out per phase and in the
> "known limitations" note at the end.

## The big picture: ~70% re-layer, ~30% new

The recovery engine you and I hardened this session is the hard part, and it's
**done**. The vision is mostly a re-skin plus a new library/navigation layer plus
onboarding — not a rewrite.

### Reuse as-is (the engine)
- `ffmpeg_runner.MergeWorker` — builds the verified vault (the merge).
- `ffmpeg_runner.ThumbnailThread` — frame extraction → thumbnails / contact sheet.
- `core/verify` — MD5 + the decode-lossless verification (the "safe" proof).
- `core/extract` + `extract_workers.ExtractWorker` — real recovery = "save the
  original". The `Extract and Recover` tab already exposes this.
- `core/manifest` — the manifest, which seeds the **local index**.
- `core/ffmpeg_cmd` — mux / concat / transcode / recover command builders.
- `review_playback` / `review_workers` — playback = Memory "play".
- `grade_manager` (LUTs) = "grade"; `whatsapp_tab` export = "share".
- `settings.py` JSON store (extend for the collection registry), plus `probe`,
  `camera_id`, `thread_utils`, `theme`, `crash_log`, `log_manager`.

### Re-layer (UI / IA)
- `main.py` `QTabWidget` (5 tabs) → hub-and-spoke shell (Home / Collection /
  Memory / Add flow / Settings). Do it **additively** — new shell alongside a
  "classic tabs" mode — to avoid a big-bang rewrite.
- `merge_tab.py` (~2,200 lines, ~62 controls) → the Add flow (auto-everything) +
  an advanced panel. Wrap and drive the existing `MergeTab` under the hood at
  first; don't rewrite it up front.
- `review_tab.py` → the Collection album + Memory playback.
- `whatsapp_tab.py` (the "Extract and Recover" tab) → Memory "share" and "save
  original" actions.
- `log_tab.py`, `about_tab.py` → Settings menu.

### Genuinely new
- **Collection model** + on-disk self-describing folder layout.
- **Collection registry / catalog** (the load-bearing new abstraction) — powers
  Home. Nothing like it exists today.
- **Home shelf**, **Collection album**, **Memory** views.
- **Onboarding** seven-moment flow, incl. the "see a memory come back" proof.
- **Browsable face** generation (contact sheet, `album.html`, optional clip files).
- **Context-aware cloud storage** default + sync-folder detection; keeping the
  light index local while the heavy payload lives remote.
- **Byte-exact for every clip** engine change (conforming clips get their own
  byte-exact track) — the honest prerequisite for the "byte for byte" promise.

## Phased roadmap

### Phase 0 — Foundations and the honesty layer *(small–medium)*
- **Per-clip recovery fidelity — DONE.** `ClipEntry.recovery_fidelity` is now set
  at build time to `byte-exact` (own un-concatenated track), `decode-lossless`
  (concat/baseline track — identical picture and sound), or `transcoded`
  (re-encoded, a high-quality copy). It round-trips in the manifest and prints a
  plain-English line in the restore log. This lets the app badge each memory and
  the onboarding promise honestly, per clip.
- **Correction to the original plan:** *don't* give conforming clips their own
  archival track. It duplicates the baseline, saves nothing over keeping the
  original file, and explodes track counts on large collections. The honest
  mechanism for "byte-exact for every memory" is **keeping the originals as
  files** (byte-exact by construction, scales, = the "make fully portable" form
  in Phase 5). The compact default stays decode-lossless for conforming clips,
  and the copy for that mode is "exactly as you filmed it" — reserving the literal
  "byte for byte" for clips that genuinely are (the provable onboarding demo, the
  pro layer, and keep-originals mode).
- Design the **collection data model** + on-disk folder layout + a **registry**
  (extend the `settings.py` JSON store or a sibling catalog file). Store portable
  path references and handle "moved / offline" from day one. This unblocks
  everything.
- **Collection + registry schema — DONE.** Designed in `COLLECTION_SCHEMA.md`:
  the collection folder is the source of truth (self-describing `collection.json`
  beside the existing `manifest.json`); `catalog.json` is a rebuildable local
  cache with copied-out covers so Home renders offline; stable ids relink moved
  folders; portable path hints + graceful offline/missing status.
- Define **auto-grouping / naming** rules (date/location → "Pool day · 3 Jul").
- Optionally clear WAV-backup task #4 while in the engine.
- **Data model — DONE.** `core/collection.py` (Collection record + `collection.json`
  read/write + `build_collection` from a manifest, rolling up the per-clip
  fidelity) and `core/catalog.py` (the rebuildable registry: relink-by-id upsert,
  offline/missing status, cover cache, JSON store beside `settings.json`), both
  with standalone tests (`test_collection.py`, `test_catalog.py`). Pure modules,
  no app-layer dependency.
- *Exit:* Phase 0 complete — fidelity plumbing, schema, and the collection +
  catalog data model, all tested. Ready for Phase 1 (a merge emits a collection
  folder and registers it).

### Phase 1 — A merge becomes a "collection" *(medium)*
- Have `MergeWorker` (or a thin wrapper) emit the **collection folder**: master +
  manifest + thumbnails + `verified.txt`, and register it in the catalog.
- Generate thumbnails / contact sheet via `ThumbnailThread`.
- Keep the index (thumbnails + manifest) local so it renders without the master.
- *Exit:* running a merge produces a self-describing collection the catalog lists.

### Phase 2 — Walking skeleton of the spine *(medium)*
- Minimal, unpolished **Home → Collection → Memory** wired end to end: Home lists
  the catalog; Collection renders the grid from the local index; Memory does
  **play** (`review_playback`) and **save the original** (`ExtractWorker`).
- *Exit:* the whole everyday loop works, ugly but real — architecture de-risked
  before any polish.

### Phase 3 — The emotional core *(small–medium)*
- **"See a memory come back"** proof: orchestrate a fast one-clip
  vault→recover→verify (reuses verify/extract; auto-pick the shortest clip).
- Polish **save the original** into the one-tap friendly verb.
- *Exit:* the pitch is demonstrable in seconds; the aha lands.

### Phase 4 — The Add flow *(large)*
- Collapse `merge_tab` into the **seven moments** with auto-everything and
  progressive disclosure; move the ~62 controls to an advanced panel.
- Progress screen with the inline storage nudge → safe → yours-to-keep. Returning
  users get the condensed flow (skip welcome + proof).
- *Exit:* a first-time and a returning user can both create a collection through
  the guided flow.

### Phase 5 — Cloud and portability *(medium)*
- Context-aware storage default (detect a cloud-sync destination via file
  attributes / known roots); light index local, heavy payload fetched on demand.
- **Make fully portable** — write `album.html` + real clip files on demand.
- Graceful offline / online states on Home and in a Collection.
- *Exit:* a collection in a Jottacloud/Dropbox folder browses and plays cleanly.

### Phase 6 — Voice, aesthetic, positioning *(medium)*
- Apply the honest copy deck across every surface.
- Friendly / approachable visual pass (lighten from dark-technical).
- Demote two-mic sync and edit tools into advanced; reposition README / branding.
- *Exit:* the app looks and speaks like the product in `PRODUCT_DIRECTION.md`.

## Recommended first move: a thin vertical slice

Build **Phases 0→1→2 as a walking skeleton** before deepening. It forces the two
riskiest new pieces early — the collection registry and the hub navigation — and
proves the whole spine works against the real engine. Everything after that is
deepening a proven loop, not discovering whether it holds.

## Risks / watch-items

- **The registry is load-bearing.** Get its schema right early; assume paths
  move (external drives, cloud). Store portable references; design "offline /
  moved" states up front, not later.
- **Navigation rewrite → do it additively.** New hub shell alongside the classic
  tabs, switchable, so nothing breaks mid-transition.
- **`merge_tab` is the biggest surgery.** Wrap-and-drive the existing tab first;
  only rewrite it once the guided flow's shape is settled.
- **Byte-exact-for-every-clip grows archives** (every clip stored verbatim). Tie
  it to the save-time storage guidance so users understand the size.
- **Scope drift.** The engine tempts gold-plating; the value now is the
  experience layer. Protect the phase order.

## Known limitations of the first pass (2026-07-06)

Honest accounting of what's skeleton vs finished after building all phases:

- **UI phases are functional skeletons, not polished.** Home / Collection /
  Memory (`library_view.py`) and the Add flow (`add_flow.py`) are real, wired, and
  offscreen-tested for construction + navigation, but they haven't had a visual
  design pass and the guided merge hasn't been run end-to-end here (a real merge
  is ~20 min; the engine itself is validated separately by the matrix + unit
  tests).
- **Two MergeTab instances exist** — the classic tab and the one `AddFlow` drives
  hidden. Fine for the transition; later, the Add flow should drive a single
  shared engine or the classic tab should retire.
- **save-the-original and make-portable run synchronously** on the UI thread with
  a wait cursor. Correct, but should move to a worker thread for large clips.
- **Phase 6 aesthetic is voice + positioning only** — the honest copy is in the
  new surfaces and the README is repositioned, but the "friendly light" visual
  redesign is a design pass still to do; theme discipline (no hardcoded colours)
  is maintained. **Scope expanded (2026-07-06):** the visual pass should cover
  the WHOLE app, not just the new Home/Collection/Memory/Add-flow screens — the
  classic tabs (Merge clips, Review, Extract and Recover, Log, About) and shared
  chrome get the friendly-light treatment too, via `theme.py`'s palettes so both
  light/dark modes stay covered. The hidden Legacy-mode toggle should keep
  showing a real pre-overhaul look for comparison, so lean on mode-aware/additive
  styling rather than overwriting the old palette outright. Tracked as task #12.
- **New modules are fully unit-tested** (collection, catalog, cloudsync, proof,
  recover, portable, library_view, add_flow); the recovery/proof were also
  validated against real 4K footage during the build.
