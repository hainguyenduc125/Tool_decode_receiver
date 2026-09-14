"""Serial worker (QThread + pyserial) đọc log gateway qua USB CDC 115200.

Không block UI. Phát từng dòng text (đã bỏ CR/LF). Mất kết nối → emit status
error, không crash; reconnect do người dùng bấm Connect lại. Counter KHÔNG reset
khi reconnect (chỉ CLEAR mới reset).
"""

from __future__ import annotations

from PyQt5.QtCore import QThread, pyqtSignal


def list_serial_ports() -> list[str]:
    """Liệt kê cổng COM trên PC.

    Ưu tiên `serial.tools.list_ports.comports()` (trả thêm VID/PID/desc). Nếu
    API này lỗi hoặc trả rỗng, fallback đọc registry Windows
    (HKLM\\HARDWARE\\DEVICEMAP\\SERIALCOMM) để không mất cổng khi auto-scan.
    """
    devices: list[str] = []
    try:
        from serial.tools import list_ports
    except Exception:
        pass
    else:
        try:
            devices = [p.device for p in list_ports.comports()]
        except Exception:
            devices = []

    # Fallback Windows registry nếu pyserial không liệt kê được.
    if not devices:
        try:
            import winreg  # Windows only
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DEVICEMAP\SERIALCOMM",
            )
            vals: list[str] = []
            i = 0
            while True:
                try:
                    vals.append(winreg.EnumValue(key, i)[1])
                    i += 1
                except OSError:
                    break
            winreg.CloseKey(key)
            # Lọc dòng hợp lệ kiểu COM3 / \\.\COM10
            for v in vals:
                s = v.strip()
                if s:
                    if s.upper().startswith("\\\\.\\"):
                        s = s[4:]
                    devices.append(s)
        except Exception:
            pass

    # De-dup giữ thứ tự.
    seen: set[str] = set()
    out: list[str] = []
    for d in devices:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


class SerialWorker(QThread):
    line = pyqtSignal(str)
    status = pyqtSignal(str)

    def __init__(self, port: str, baudrate: int = 115200, parent=None) -> None:
        super().__init__(parent)
        self.port = port
        self.baudrate = baudrate
        self._stop = False
        self._ser = None

    def stop(self) -> None:
        self._stop = True
        try:
            if self._ser:
                self._ser.close()
        except Exception:
            pass
        self.wait(3000)

    def write(self, data: bytes) -> bool:
        """Gửi lệnh xuống gateway qua USB CDC (Serial). Không đồng bộ với read
        loop nên lỗi nhỏ, gateway xử lý theo dòng JSON."""
        ser = self._ser
        if ser is None:
            return False
        try:
            ser.write(data)
            ser.flush()
            return True
        except Exception:
            return False

    def run(self) -> None:
        import serial

        self._stop = False
        self.status.emit("CONNECTING")
        try:
            self._ser = serial.Serial(self.port, self.baudrate, timeout=0.1)
        except Exception as e:
            self.status.emit("ERROR: %s" % e)
            self._ser = None
            return

        self.status.emit("CONNECTED")
        buf = b""
        try:
            while not self._stop:
                data = self._ser.read(4096)
                if not data:
                    continue
                buf += data
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    line = raw.decode("utf-8", errors="replace").rstrip("\r")
                    self.line.emit(line)
        except Exception as e:
            if not self._stop:
                self.status.emit("DISCONNECTED: %s" % e)
        finally:
            try:
                if self._ser:
                    self._ser.close()
            except Exception:
                pass
            self._ser = None
            if not self._stop:
                self.status.emit("DISCONNECTED")
