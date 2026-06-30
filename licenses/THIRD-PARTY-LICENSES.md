# Third-party licenses — LunaVault FuseBox

LunaVault FuseBox bundles or depends on the open-source components below. The
application's own code is MIT-licensed (see the top-level `LICENSE`). FFmpeg is
invoked as a separate executable (a subprocess), not linked into the app.

The full text of each license should ship in this `licenses/` folder. At package
time, drop in `GPLv2.txt`, `GPLv3.txt`, `LGPLv3.txt`, `PSF.txt`, `Apache-2.0.txt`,
and the BSD/HPND texts referenced below (placeholders are listed in
`licenses/README.txt`).

| Component | Version (record at build) | License | Source |
|-----------|---------------------------|---------|--------|
| FFmpeg / ffprobe (bundled binaries) | run `bin/ffmpeg -version` and record the printed version **and** `configuration:` line | **GPL v2-or-later** (build includes libx264/libx265) | https://ffmpeg.org/download.html |
| Qt 6 (via PySide6) | record PySide6 version | **LGPL v3** | https://www.qt.io/download-open-source |
| PySide6 | from `requirements.txt` | LGPL v3 | https://pypi.org/project/PySide6/ |
| NumPy | from `requirements.txt` | BSD-3-Clause | https://numpy.org |
| Pillow | from `requirements.txt` | HPND (MIT-style) | https://python-pillow.org |
| qrcode | from `requirements.txt` | BSD | https://pypi.org/project/qrcode/ |
| requests | from `requirements.txt` | Apache-2.0 | https://requests.readthedocs.io |
| Python | the interpreter PyInstaller bundles | PSF License | https://python.org |

## FFmpeg — GPL corresponding source (written offer)

The bundled `bin/ffmpeg` and `bin/ffprobe` are distributed under the GNU General
Public License. You are entitled to the complete corresponding source code for
the exact build distributed with this application.

- The binaries were obtained from a public builder (e.g. gyan.dev or BtbN).
  **Record here the exact download URL and the version string from
  `ffmpeg -version` for the build you shipped.**
- Build version: `__________________________`  (fill in at package time)
- Build configuration (`configuration:` line): `__________________________`
- Corresponding source: the matching source tarball from the builder above, or
  from https://ffmpeg.org/releases/ , kept for at least three years and
  available on request to the contact in the About tab.

No additional restrictions are placed on the GPL-covered binaries.

## Qt / PySide6 — LGPL relinking

LunaVault FuseBox ships as a PyInstaller **onedir** bundle, so the Qt shared
libraries are distributed as separate files that a user may replace with their
own compatible build, satisfying the LGPL's relinking requirement. Qt source is
available from the Qt download page above.

## LUTs

The colour-grade `.cube` files in `luts/` are distributed under descriptive,
characteristic-based names and are provided for use within this application.
