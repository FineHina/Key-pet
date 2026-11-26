"""Verify real OGG playback in Qt with audio output muted."""
import sys
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
app = QApplication([])
player = QMediaPlayer()
output = QAudioOutput()
output.setMuted(True)
output.setVolume(0)
player.setAudioOutput(output)
result = {"loaded": False, "error": None}


def status(value):
    if value == QMediaPlayer.MediaStatus.LoadedMedia:
        result["loaded"] = True
        print("Qt decoded OGG; duration ms:", player.duration(), "audio tracks:", len(player.audioTracks()))
        player.play()
        QTimer.singleShot(200, finish)


def finish():
    print("Muted:", output.isMuted(), "loaded:", result["loaded"], "error:", result["error"])
    player.stop()
    app.quit()


player.mediaStatusChanged.connect(status)
player.errorOccurred.connect(lambda error, text: result.update(error=text))
player.setSource(QUrl.fromLocalFile(str(ROOT / "voices/ch0335_exskill_2.ogg")))
QTimer.singleShot(5000, finish)
app.exec()
if not result["loaded"] or result["error"]:
    raise SystemExit(1)
