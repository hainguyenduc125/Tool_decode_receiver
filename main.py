"""Gateway LoRa Message Counter — entry point.

Chạy:
    python main.py
    hoặc  .\\run.ps1

Lưu ý: tool này KHÔNG sửa firmware; chỉ đọc Serial + subscribe MQTT theo đúng
topic/payload đã có trong firmware gateway (xem README.md).
"""

import sys


def main() -> int:
    try:
        from PyQt5.QtWidgets import QApplication
    except ImportError:
        sys.stderr.write(
            "Thiếu PyQt5. Cài dependencies:\n"
            "    pip install -r requirements.txt\n"
            "hoặc chạy run.ps1\n")
        return 1

    try:
        from ui.main_window import MainWindow
    except ImportError as e:
        sys.stderr.write("Import lỗi: %s\n" % e)
        return 1

    app = QApplication(sys.argv)
    app.setApplicationName("Gateway LoRa Message Counter")
    win = MainWindow()
    win.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
