"""core — UI-agnostic engine for LunaVault FuseBox.

Pure-Python, Qt-free modules that build ffmpeg commands and perform audio
sync analysis. Kept free of PySide6 so the same logic can be unit-tested and,
later, reused behind a web/WASM or CLI front-end. The QThread workers in
ffmpeg_runner.py are thin wrappers over these functions.
"""
