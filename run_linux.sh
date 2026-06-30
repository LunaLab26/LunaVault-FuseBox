#!/usr/bin/env bash
# LunaVault FuseBox — run from source on Linux / Steam Deck.
# Creates a Python venv in this folder on first run, installs deps, then launches.
set -e
cd "$(dirname "$0")"

VENV=".venv"

# 1. First-run setup: virtual environment + dependencies
if [ ! -d "$VENV" ]; then
    echo "First run — creating virtual environment and installing dependencies…"
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install --upgrade pip
    "$VENV/bin/pip" install -r requirements.txt
fi

# 2. Make the bundled Linux ffmpeg executable
chmod +x bin/ffmpeg bin/ffprobe 2>/dev/null || true

# 3. Launch
exec "$VENV/bin/python" src/main.py
