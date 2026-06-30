licenses/ — what ships here
===========================

THIRD-PARTY-LICENSES.md   The index of all bundled components and their licenses,
                          plus the FFmpeg GPL corresponding-source written offer.

Before publishing a release, drop the FULL license texts into this folder so the
distribution is self-contained:

  GPLv2.txt      https://www.gnu.org/licenses/old-licenses/gpl-2.0.txt
  GPLv3.txt      https://www.gnu.org/licenses/gpl-3.0.txt   (if your ffmpeg build is GPLv3)
  LGPLv3.txt     https://www.gnu.org/licenses/lgpl-3.0.txt
  PSF.txt        https://docs.python.org/3/license.html
  Apache-2.0.txt https://www.apache.org/licenses/LICENSE-2.0.txt
  BSD-3-Clause.txt (NumPy/qrcode)   https://opensource.org/license/bsd-3-clause
  Pillow-HPND.txt  https://github.com/python-pillow/Pillow/blob/main/LICENSE

Also fill in, in THIRD-PARTY-LICENSES.md:
  - the exact ffmpeg version + `configuration:` line you shipped, and
  - the download URL / corresponding-source location for that exact build.

The About tab's "Open-source licenses" button opens THIRD-PARTY-LICENSES.md.
