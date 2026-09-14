"""Test cho serial_manager.list_serial_ports — auto-scan COM.

Mục tiêu: đảm bảo khi `serial.tools.list_ports.comports()` lỗi/trả rỗng, hàm
fallback đọc registry Windows (winreg) để KHÔNG mất cổng khi auto-scan.
"""

from __future__ import annotations

import sys
import types
import unittest
from unittest import mock


class FakeListPortsError(Exception):
    pass


def _install_fake_tools(comports_result):
    """Đặt `serial.tools` (giả) vào sys.modules, có attribute list_ports."""
    tools = types.ModuleType("serial.tools")
    lp = mock.MagicMock()
    tools.list_ports = lp
    if isinstance(comports_result, BaseException):
        lp.comports.side_effect = comports_result
    else:
        lp.comports.return_value = comports_result
    sys.modules["serial.tools"] = tools
    return lp


class TestListSerialPorts(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from lora_io import serial_manager

        cls.serial_manager = serial_manager

    def test_returns_empty_when_comports_and_registry_fail(self) -> None:
        _install_fake_tools(FakeListPortsError())
        with mock.patch.dict(sys.modules, {"winreg": None}):
            # winreg giả None -> import winreg thất bại -> bị except nuốt.
            self.assertEqual(self.serial_manager.list_serial_ports(), [])
        sys.modules.pop("serial.tools", None)

    def test_fallback_registry_when_comports_empty(self) -> None:
        _install_fake_tools([])  # comports() trả rỗng

        class FakeKey:
            def __init__(self, vals) -> None:
                self._vals = vals

        vals = ["COM3", r"\\.\COM10"]

        class FakeWinreg:
            HKEY_LOCAL_MACHINE = 1

            @staticmethod
            def OpenKey(*args, **kwargs):
                return FakeKey(vals)

            @staticmethod
            def EnumValue(key, i):
                if i >= len(key._vals):
                    raise OSError("end")
                return (0, key._vals[i], 1)

            @staticmethod
            def CloseKey(key):
                return None

        with mock.patch.dict(sys.modules, {"winreg": FakeWinreg}):
            ports = self.serial_manager.list_serial_ports()
        # COM10 ở dạng \\.\COM10 được rút thành COM10.
        self.assertEqual(ports, ["COM3", "COM10"])
        sys.modules.pop("serial.tools", None)

    def test_normal_comports_used_first(self) -> None:
        class FakeCom:
            def __init__(self, device) -> None:
                self.device = device

        _install_fake_tools([FakeCom("COM5"), FakeCom("COM5")])

        def _no_open(*a, **k):
            raise AssertionError("registry fallback must not be used")

        with mock.patch.dict(sys.modules, {"winreg": _no_open}):
            ports = self.serial_manager.list_serial_ports()
        self.assertEqual(ports, ["COM5"])  # de-dup
        sys.modules.pop("serial.tools", None)


if __name__ == "__main__":
    unittest.main()