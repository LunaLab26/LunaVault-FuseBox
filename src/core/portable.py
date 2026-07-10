"""core/portable.py — "make fully portable".

Writes real clip files plus a self-contained album.html so a collection browses
and plays on any device with no FuseBox (PRODUCT_DIRECTION.md — the walk-away
form, and the cloud-friendly default). The album page references only relative
paths (thumbs/ and clips/), so the folder stays portable.

`album_html` is pure and testable; `make_portable` runs the recovery.
"""

import html
from pathlib import Path
from typing import Optional

from core import collection as collection_mod
from core import recover


def _memory_name(clip, index: int) -> str:
    src = getattr(clip, "source_filename", "") or ""
    return Path(src).stem if src else f"Memory {index + 1}"


def album_html(collection, manifest, clip_names) -> str:
    """A self-contained gallery page. `clip_names[i]` is the recovered file name in
    clips/ for memory i, or None if it wasn't written (thumbnail-only)."""
    name = html.escape(getattr(collection, "name", "") or "Memories")
    clips = getattr(manifest, "clips", [])
    total = getattr(getattr(collection, "verified", None), "total", len(clips))
    subtitle = f"{total} memories · kept and verified" if total else "kept and verified"

    tiles = []
    for i, clip in enumerate(clips):
        mem = html.escape(_memory_name(clip, i))
        thumb = f"thumbs/{i + 1:03d}.jpg"
        clip_file = clip_names[i] if (clip_names and i < len(clip_names)) else None
        img = f'<img src="{thumb}" alt="{mem}" loading="lazy">'
        inner = (f'<a href="clips/{html.escape(clip_file)}">{img}</a>'
                 if clip_file else img)
        tiles.append(f'<figure>{inner}<figcaption>{mem}</figcaption></figure>')

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name}</title>
<style>
  body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
         margin: 0; padding: 24px; color: #2c2c2a; background: #faf7f2; }}
  header h1 {{ margin: 0 0 4px; font-weight: 500; }}
  header p {{ margin: 0 0 24px; color: #7a7a76; }}
  .grid {{ display: grid; gap: 16px;
          grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); }}
  figure {{ margin: 0; }}
  figure img {{ width: 100%; aspect-ratio: 16/9; object-fit: cover;
               border-radius: 10px; background: #eae5dd; display: block; }}
  figcaption {{ margin-top: 6px; font-size: 14px; }}
  a {{ text-decoration: none; color: inherit; }}
</style></head>
<body>
<header><h1>{name}</h1><p>{html.escape(subtitle)}</p></header>
<div class="grid">
{chr(10).join(tiles)}
</div>
</body></html>
"""


def write_album_html(folder, collection, manifest, clip_names,
                     filename: str = "album.html") -> Path:
    p = Path(folder) / filename
    p.write_text(album_html(collection, manifest, clip_names), encoding="utf-8")
    return p


def make_portable(ff: str, folder, manifest, master_path, **kwargs):
    """Recover every memory into clips/, write album.html, and flip the
    collection's storage_mode to portable. Returns the updated Collection (or None
    if the folder has no collection.json). `kwargs` pass to subprocess."""
    folder = Path(folder)
    clips_dir = folder / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    names = []
    for i in range(len(getattr(manifest, "clips", []))):
        out = recover.recover_clip(ff, str(master_path), manifest, i, clips_dir, **kwargs)
        names.append(out.name if out else None)

    col = collection_mod.read_collection(folder)
    write_album_html(folder, col, manifest, names)
    if col is not None:
        col.storage_mode = collection_mod.STORAGE_PORTABLE
        col.clips_dir = "clips"
        collection_mod.write_collection(col, folder)
    return col
