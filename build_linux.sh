#!/usr/bin/env bash
# LunaVault FuseBox v1.3 — Linux / Steam Deck build script
set -e

echo ""
echo "  ╔════════════════════════════════════════════════╗"
echo "  ║  LunaVault FuseBox v1.3 — Linux / Steam Deck    ║"
echo "  ╚════════════════════════════════════════════════╝"
echo ""

APP="LunaVaultFuseBox"

# 1. Dependencies (PySide6 — NOT PyQt6; the code imports PySide6)
echo "  [1/5] Installing Python dependencies..."
if [ -d "wheels/linux" ]; then
    echo "        Using offline wheels from wheels/linux/ where available"
    pip install --no-index --find-links wheels/linux PySide6 numpy pyinstaller \
        --break-system-packages 2>/dev/null \
    || pip install -r requirements.txt --break-system-packages 2>/dev/null \
    || pip install -r requirements.txt
else
    echo "        Downloading from PyPI (internet required)..."
    pip install --break-system-packages -r requirements.txt \
        2>/dev/null || pip install -r requirements.txt
fi
echo ""

# 2. Check ffmpeg
echo "  [2/5] Checking bundled ffmpeg..."
MISSING=0
[ ! -f "bin/ffmpeg" ]  && echo "  ERROR: bin/ffmpeg not found."  && MISSING=1
[ ! -f "bin/ffprobe" ] && echo "  ERROR: bin/ffprobe not found." && MISSING=1
if [ "$MISSING" -eq 1 ]; then
    echo ""
    echo "  Download a Linux amd64 static build (GPL, with libx264/libx265):"
    echo "    https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz"
    echo "  Extract ffmpeg + ffprobe into bin/ then re-run."
    exit 1
fi
chmod +x bin/ffmpeg bin/ffprobe
echo "  $(bin/ffmpeg -version 2>&1 | head -1)"
echo ""

# 3. PyInstaller (onedir keeps Qt libs separate → satisfies LGPL relinking)
echo "  [3/5] Building with PyInstaller..."
pyinstaller \
  --noconfirm \
  --onedir \
  --windowed \
  --name "$APP" \
  --paths src \
  --hidden-import PySide6.QtXml \
  --hidden-import PySide6.QtNetwork \
  --hidden-import PySide6.QtSvg \
  --hidden-import PySide6.QtSvgWidgets \
  --hidden-import PySide6.QtMultimedia \
  --hidden-import numpy \
  --exclude-module PyQt6 \
  --exclude-module PyQt5 \
  --exclude-module tkinter \
  --add-data "src/assets:assets" \
  --add-data "luts:luts" \
  --icon src/assets/lunavault.png \
  src/main.py

# 4. Copy data the app reads from get_app_dir() (next to the executable)
echo ""
echo "  [4/5] Copying luts/, licenses/ and ffmpeg to dist/$APP/..."
cp -r luts      "dist/$APP/"
cp -r licenses  "dist/$APP/"
cp    LICENSE   "dist/$APP/" 2>/dev/null || true

# 5. Copy ffmpeg binaries
mkdir -p "dist/$APP/bin"
cp bin/ffmpeg  "dist/$APP/bin/"
cp bin/ffprobe "dist/$APP/bin/"
chmod +x "dist/$APP/bin/ffmpeg" "dist/$APP/bin/ffprobe"

echo ""
echo "  ════════════════════════════════════════════════"
echo "   Build complete!"
echo "   Executable: dist/$APP/$APP"
echo "  ════════════════════════════════════════════════"
echo ""
