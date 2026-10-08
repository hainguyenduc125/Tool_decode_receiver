"""LoRa Console & Decoder — entry point.

Chạy:
    python main.py
    hoặc  .\\run.ps1

Ứng dụng console LoRa độc lập: đọc UART và giải mã frame theo data_process.cpp.
"""

import sys
from pathlib import Path


def main() -> int:
    try:
        import PyQt5
        from PyQt5.QtCore import QCoreApplication
        from PyQt5.QtWidgets import QApplication
    except ImportError:
        sys.stderr.write(
            "Thiếu PyQt5. Cài dependencies:\n"
            "    pip install -r requirements.txt\n"
            "hoặc chạy run.ps1\n")
        return 1

    # Qt có thể không tự tìm thấy qwindows.dll khi project nằm dưới đường dẫn
    # Unicode trên Windows. Dùng plugin directory đi kèm đúng bản PyQt5 đang chạy.
    plugin_dir = Path(PyQt5.__file__).resolve().parent / "Qt5" / "plugins"
    if not (plugin_dir / "platforms" / "qwindows.dll").is_file():
        sys.stderr.write(
            "Không tìm thấy Qt platform plugin:\n"
            f"    {plugin_dir / 'platforms' / 'qwindows.dll'}\n"
            "Hãy cài lại dependencies bằng: python -m pip install -r requirements.txt\n")
        return 1
    QCoreApplication.addLibraryPath(str(plugin_dir))

    try:
        from ui.lora_console_window import LoraConsoleWindow
    except ImportError as e:
        sys.stderr.write("Import lỗi: %s\n" % e)
        return 1

    app = QApplication(sys.argv)
    app.setApplicationName("LoRa Console & Decoder")
    win = LoraConsoleWindow()
    win.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
