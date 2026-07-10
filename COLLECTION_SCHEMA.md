# FuseBox — collection and registry schema

> The data model behind Home / Collection / Memory (see `BUILD_PLAN.md` Phase 0).
> Snapshot 2026-07-06; revise freely. Illustrative JSON below uses `//` comments
> for annotation — the real files are plain JSON with no comments.

## The one principle

**The collection folder is the source of truth. The registry is a rebuildable
local cache.** Every collection folder is fully self-describing — hand it to
someone with no FuseBox and it still makes sense. The app keeps a local
`catalog.json` only so Home can render instantly and offline; if that catalog is
lost, or a folder moves, you re-point at the folder and it re-links by its stable
id. Nothing about a person's memories depends on the app's private state.

Consequences that fall out of this principle:
- A collection carries its own identity and metadata **inside** the folder.
- The catalog stores lightweight pointers plus a small display cache (including a
  copied-out cover thumbnail), so Home works even when the folder is offline.
- "Add an existing collection" = point at a folder; the app reads its identity
  and links it. Re-adding a moved folder matches by id → relinks, no duplicate.

## The collection folder

Self-describing, walk-away, openable by anyone. `collection.json` is the new
organisational record; `manifest.json` stays the authoritative per-clip recovery
record (already written today), and is *not* duplicated into `collection.json`.

```
Grandpa's 90th — 3 Aug 2026/
  ├─ collection.json          organisation + provenance (NEW — described below)
  ├─ Grandpa's 90th.mov       the verified vault (the master)
  ├─ Grandpa's 90th.manifest.json   per-clip recovery record (existing)
  ├─ Grandpa's 90th.restore.log     plain-English recovery notes (existing)
  ├─ verified.txt             plain-English "safe" proof
  ├─ thumbs/                  per-clip thumbnails + contact-sheet.jpg
  ├─ clips/        (portable mode only) real playable memory files
  └─ album.html    (portable mode only) no-app browsable album page
```

### collection.json  (schema `fusebox.collection/1`)

The album-card + provenance data. Small, human-legible, sidecar only (never
embedded in the master — the master already carries the technical manifest, which
has a size limit).

```jsonc
{
  "schema": "fusebox.collection/1",
  "id": "col_9f2c1a7e4b8d",        // stable id (uuid4, prefixed); survives moves + renames
  "name": "Grandpa's 90th",         // user-facing; auto-named, editable
  "created_utc": "2026-08-03T18:31:44Z",   // when the archive was made
  "captured": {                     // when the footage was FILMED (from clip creation_time)
    "start": "2026-08-03",          // drives Home's year grouping + the default name
    "end":   "2026-08-03"
  },
  "cover": "thumbs/03 - the cake.jpg",   // relative path to the cover image
  "memory_count": 40,
  "master": "Grandpa's 90th.mov",        // relative — the vault
  "storage_mode": "compact",             // "compact" (thumbnails only) | "portable" (clips/ kept)
  "clips_dir": null,                     // relative dir when portable, e.g. "clips"; else null
  "cloud_backed": true,                  // folder sits in a detected cloud-sync location
  "verified": {                          // PROVENANCE — a fact about creation, not live monitoring
    "at_utc": "2026-08-03T18:31:44Z",
    "passed": 40,
    "total": 40,
    "fidelity": { "byte-exact": 12, "decode-lossless": 28, "transcoded": 0 }
  }
}
```

`verified.fidelity` aggregates the per-clip `recovery_fidelity` tags now written
into the manifest — so the collection can honestly say how much of it is
byte-for-byte vs exactly-as-filmed, without re-opening every clip.

## The registry

App-local, rebuildable, never authoritative. Lives beside `settings.json` (same
app-data dir as `settings._settings_path()`), with a small cover cache so the
shelf is never blank when a collection is offline.

```
<app data>/
  ├─ settings.json            existing
  ├─ catalog.json             the registry (NEW)
  └─ covers/<collection-id>.jpg   copied-out cover per collection (renders offline)
```

### catalog.json  (schema `fusebox.catalog/1`)

```jsonc
{
  "schema": "fusebox.catalog/1",
  "collections": [
    {
      "id": "col_9f2c1a7e4b8d",
      "path": "G:/Memories/Grandpa's 90th — 3 Aug 2026",   // last-known folder path
      "locate": {                        // aids to re-find the folder if the path breaks
        "volume_label": "Photos",        // drive/volume label (survives drive-letter changes)
        "cloud": "jottacloud",           // detected provider, or null
        "relative_hint": "Memories/Grandpa's 90th — 3 Aug 2026"
      },
      "cached": {                        // display cache — Home renders from this alone
        "name": "Grandpa's 90th",
        "date": "2026-08-03",
        "cover": "col_9f2c1a7e4b8d.jpg", // filename under <app data>/covers/
        "memory_count": 40,
        "verified": "40/40"
      },
      "status": "available",             // "available" | "offline" | "missing"
      "added_utc":   "2026-08-03T18:31:50Z",
      "last_seen_utc": "2026-08-05T09:12:00Z"
    }
  ]
}
```

## Design decisions

- **Stable id, everywhere.** `id` lives in both `collection.json` and the catalog
  entry. Moves, renames, and re-adds match by id → relink, never duplicate.
- **Cache the cover into app data.** Home must render instantly and *offline*
  (drive unplugged, cloud file not hydrated). A tiny copied-out cover per
  collection means the shelf is never blank; the heavy master is only touched to
  play / pull out / verify.
- **Portable path references.** Store the absolute `path` plus `locate` hints
  (volume label, cloud provider, relative hint) so a drive-letter change or a
  re-mounted cloud folder can be re-found instead of going "missing".
- **Graceful status.** `available` / `offline` / `missing` drive the UI: an
  offline collection still shows on the shelf (from cache), just muted, with a
  "reconnect / relocate" affordance — it never vanishes.
- **`verified` is provenance, not monitoring.** It records the creation-time
  result and never implies a background scan (ongoing integrity is out of scope).
- **Two files, two jobs.** `collection.json` = organisation (walk-away, in
  folder); `manifest.json` = recovery (technical, existing). No overlap, no
  duplication.

## Lifecycle

- **Create** (end of a merge): write the folder (master + manifest + thumbs +
  `collection.json` + `verified.txt`), copy the cover into `covers/`, append a
  catalog entry.
- **Add existing**: point at a folder → read `collection.json` → link by id
  (update path if it moved, else add).
- **Open**: read from cache for the shelf; open the folder's manifest/thumbs for
  the album; touch the master only on play / save-original / verify.
- **Relocate**: folder moved → user re-points → match by id, update `path` +
  `locate`, status back to `available`.
- **Rebuild**: catalog lost → re-add folders; each folder's `collection.json`
  restores its identity and metadata.

## Maps to code

- New `core/collection.py`: a `Collection` dataclass + `to_json` / `from_json` +
  `write_collection_json` / `read_collection_json`, mirroring the proven pattern
  in `core/manifest.py`. Includes an aggregator that rolls the manifest's
  per-clip `recovery_fidelity` into the `verified.fidelity` counts.
- New `core/catalog.py`: load/save `catalog.json`, add / relink-by-id / update /
  set-status, and a "resolve folder" helper that uses `locate` hints. Follows the
  `settings.py` JSON-store style.
- `ffmpeg_runner` (or a thin post-merge step) calls both at the end of a merge to
  emit the folder and register it.

## Deferred

- **Appending to a collection** (adding more footage later): needs the manifest +
  `collection.json` + master to grow; the id/schema already allow it, but the
  merge/append mechanics are Phase 1+.
- **Duplicate detection** (same footage added twice) and **light tags/people**:
  later; the schema leaves room (add fields under a versioned bump).
