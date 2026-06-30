"""audio_sample_player.py — render and audition a 10s mix sample (QtMultimedia).

Renders an audio-only sample of the combined mix for the selected clip with
ffmpeg, then plays it in-app via QMediaPlayer/QAudioOutput — no extra window, no
ffplay binary. A small modeless dialog offers Stop.
"""

import subprocess
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal, QUrl
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput

from ffmpeg_runner import get_ffmpeg, get_app_dir
from core.binaries import no_window
from core.ffmpeg_cmd import MixSpec, build_mix_sample_cmd

_active: Optional["SamplePlayerDialog"] = None   # keep a reference alive


class _RenderThread(QThread):
    done  = Signal(str)
    error = Signal(str)

    def __init__(self, clip, mix: MixSpec, out_path: str):
        super().__init__()
        self._clip = clip
        self._mix = mix
        self._out = out_path

    def run(self):
        try:
            ff, _ = get_ffmpeg()
            cmd = build_mix_sample_cmd(ff, self._clip, self._mix, self._out, seconds=10.0)
            r = subprocess.run(cmd, capture_output=True, timeout=60, **no_window())
            if r.returncode == 0 and Path(self._out).exists():
                self.done.emit(self._out)
            else:
                self.error.emit(r.stderr.decode(errors="ignore")[-200:])
        except Exception as e:
            self.error.emit(str(e))


class SamplePlayerDialog(QDialog):
    def __init__(self, clip, kind: str, match_levels: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Mix sample")
        self.setMinimumWidth(300)
        lay = QVBoxLayout(self)
        self._label = QLabel("Rendering 10-second sample…")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._label)
        row = QHBoxLayout()
        row.addStretch()
        self._stop = QPushButton("Stop")
        self._stop.clicked.connect(self.close)
        row.addWidget(self._stop)
        lay.addLayout(row)

        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._player.setAudioOutput(self._audio)

        mix = MixSpec(
            kind=kind, match_levels=match_levels,
            drift_ratio=clip.sync_drift_ratio if clip.sync_done else 1.0,
            polarity_inverted=clip.sync_polarity_inverted if clip.sync_done else False,
        )
        temp_dir = get_app_dir() / "_temp"
        temp_dir.mkdir(exist_ok=True)
        out = str(temp_dir / "mix_sample.m4a")

        self._render = _RenderThread(clip, mix, out)
        self._render.done.connect(self._on_rendered)
        self._render.error.connect(self._on_error)
        self._render.start()

    def _on_rendered(self, path: str):
        self._label.setText("Playing sample…")
        self._player.setSource(QUrl.fromLocalFile(path))
        self._player.play()

    def _on_error(self, msg: str):
        self._label.setText(f"Could not render sample\n{msg[:120]}")

    def closeEvent(self, event):
        try:
            self._player.stop()
        except Exception:
            pass
        super().closeEvent(event)


def play_mix_sample(clip, kind: str, match_levels: bool, parent=None):
    global _active
    _active = SamplePlayerDialog(clip, kind, match_levels, parent)
    _active.show()
