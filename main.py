"""Q版桌宠 entry point. Run: python main.py"""
from pathlib import Path
import sys

from PySide6.QtCore import QLockFile
from PySide6.QtWidgets import QApplication, QMessageBox

from pet.window import PetWindow


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)   # tray app: hide to tray instead
    app.setApplicationDisplayName("桌宠")
    data = Path(__file__).resolve().parent / "data"
    data.mkdir(exist_ok=True)
    lock = QLockFile(str(data / "desktop-pet.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        QMessageBox.information(None, "桌宠已运行", "已有桌宠实例正在运行，请从托盘打开助手。")
        return
    win = PetWindow()
    win.show()
    app.aboutToQuit.connect(win.assistant.shutdown)
    try:
        app.exec()
    finally:
        win.assistant.shutdown()
        lock.unlock()


if __name__ == "__main__":
    main()
