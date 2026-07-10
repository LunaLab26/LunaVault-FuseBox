<p align="center">
  <img src="lunavault_fusebox_logo.png" alt="LunaVault FuseBox" width="520">
</p>

<p align="center"><em>Your memories, safely kept — and provably yours.</em></p>

# LunaVault FuseBox

A safe home for your memories. Point FuseBox at a folder of videos — a birthday,
a holiday, a quiet afternoon at home — and it checks every one, keeps them
together as a collection, and lets you get any of them back exactly as you filmed
it. Nothing is locked in: a collection is an ordinary folder you can open on any
machine, and FuseBox is there whenever you want to browse, play, or pull a
memory out.

It doesn't promise "forever". It promises something you can check: a verified,
self-contained copy the moment you make it, that you can keep anywhere and always
recover.

> **Direction:** FuseBox is being re-shaped from a wireless-mic merge tool into a
> preservation-first keeper of memories. See [`PRODUCT_DIRECTION.md`](PRODUCT_DIRECTION.md)
> for the vision, [`BUILD_PLAN.md`](BUILD_PLAN.md) for the phased build, and
> [`COLLECTION_SCHEMA.md`](COLLECTION_SCHEMA.md) for the data model.

---

## What it does

- **Keeps your memories, verified** — merges a folder of clips into one
  losslessly-archived master, and checks that every memory can be recovered. Each
  memory is tagged with how faithfully it comes back: *byte for byte*, *exactly as
  filmed* (identical picture and sound), or a *high-quality copy*.
- **See a memory come back** — the app proves it: pull any original back out of
  the archive, exactly as you filmed it.
- **Collections, organised for you** — footage is auto-grouped into a named,
  dated collection with a cover; the Home shelf shows them all, and renders
  instantly even when the files live on a drive or in the cloud.
- **Walk-away, not locked in** — a collection is a standard folder; the master
  plays in any player. "Make fully portable" writes each memory as its own file
  plus a no-app `album.html` you can browse anywhere.
- **Cloud, provider-agnostic** — keep collections in any sync folder (Jottacloud,
  Dropbox, OneDrive…); FuseBox treats it as a normal path and keeps a light local
  index so the shelf works offline.

### Advanced (for power users)

- **Two-mic audio** — for footage shot with a separate wireless mic + WAV: the
  two mics are kept as separate tracks, aligned (GCC-PHAT + drift correction,
  slow-motion aware), with optional L/R-split or 50/50 mix.
- **Review + grade** — frame-step playback, per-track audio audition/mix, colour
  and audio scopes, full-res snapshots, and trim/LUT export for sharing.
- **Pre-flight** — see exactly what a merge will do (tracks, sizes, time) before
  committing, with live MB/s + ETA.
- **Light / dark / system theme.**

## Platforms

| Platform | Status |
|----------|--------|
| Windows | Supported |
| Steam Deck / Linux | Supported (run from source or PyInstaller; Flatpak planned) |
| macOS | Planned |
| Web (in-browser) | Planned (ffmpeg.wasm companion) |

## Run from source

Requires Python 3.10+, plus `ffmpeg`/`ffprobe` binaries in `bin/` (not bundled
in the repo — see below).

```bash
pip install -r requirements.txt
python src/main.py
```

On Linux / Steam Deck the helper script sets up a virtual environment for you:

```bash
./run_linux.sh
```

### ffmpeg binaries

Place a static `ffmpeg` + `ffprobe` (GPL build, with libx264/libx265) in `bin/`:

- Windows: from https://www.gyan.dev/ffmpeg/builds/ → `bin/ffmpeg.exe`, `bin/ffprobe.exe`
- Linux: from https://johnvansickle.com/ffmpeg/ → `bin/ffmpeg`, `bin/ffprobe`

## Build a standalone app

- Windows: `build.bat` → `dist/LunaVaultFuseBox/`
- Linux / Steam Deck: `./build_linux.sh` → `dist/LunaVaultFuseBox/`

## Tests

```bash
python tests/test_ffmpeg_cmd.py
python tests/test_sync_advanced.py
# ...etc — each test file runs standalone, or use pytest
```

## Licensing

The application code is **MIT** (see [LICENSE](LICENSE)). It bundles third-party
components under their own licenses — notably **FFmpeg (GPL)** — see
[licenses/THIRD-PARTY-LICENSES.md](licenses/THIRD-PARTY-LICENSES.md).

## Credits

Built with the help of Claude (Anthropic). Powered by FFmpeg, PySide6/Qt, and NumPy.
