"""Q版桌宠 entry point. Run: python main.py"""
from pathlib import Path
import sys

from PySide6.QtCore import QLockFile, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from pet.character_art import brand_icon
from pet.window import PetWindow


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)   # tray app: hide to tray instead
    app.setApplicationDisplayName("KEI Assistant")
    app.setWindowIcon(brand_icon())
    data = Path(__file__).resolve().parent / "data"
    data.mkdir(exist_ok=True)
    lock = QLockFile(str(data / "desktop-pet.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        QMessageBox.information(None, "桌宠已运行", "已有桌宠实例正在运行，请从托盘打开助手。")
        return
    win = PetWindow()
    win.assistant.grant_startup_gift()
    win.show()
    app.aboutToQuit.connect(win.assistant.shutdown)
    QTimer.singleShot(0, win.assistant.play_startup_greeting)
    try:
        app.exec()
    finally:
        win.assistant.shutdown()
        lock.unlock()


if __name__ == "__main__":
    main()
