from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


def _install_fake_serial_modules():
    if "serial" in sys.modules:
        return
    serial_module = types.ModuleType("serial")
    serial_module.__path__ = []
    serial_module.Serial = object
    serial_module.SerialException = OSError
    serial_module.SerialTimeoutException = TimeoutError
    tools_module = types.ModuleType("serial.tools")
    tools_module.__path__ = []
    list_ports_module = types.ModuleType("serial.tools.list_ports")
    list_ports_module.comports = lambda: []
    tools_module.list_ports = list_ports_module
    serial_module.tools = tools_module
    sys.modules["serial"] = serial_module
    sys.modules["serial.tools"] = tools_module
    sys.modules["serial.tools.list_ports"] = list_ports_module


_install_fake_serial_modules()

from parahand import ParaHand  # noqa: E402


class ParaHandTactileConfigTests(unittest.TestCase):
    def setUp(self):
        config_path = Path(__file__).resolve().parents[1] / "config_hand.yaml"
        self.hand = ParaHand(str(config_path))

    def test_nested_default_selects_fsr402(self):
        self.hand.tactile_config = self.hand._extract_tactile_config(
            {
                "tactile": {
                    "enabled": True,
                    "fsr402": {"port": "COM8"},
                }
            }
        )
        self.assertEqual(self.hand.tactile_config.mode, "fsr402")
        self.assertEqual(self.hand.tactile_config.fsr402.port, "COM8")
        self.assertEqual(self.hand.tactile_unit, "g")

    def test_legacy_flat_fsr402_config_is_still_accepted(self):
        tactile = self.hand._extract_tactile_config(
            {
                "tactile": {
                    "enabled": True,
                    "port": "COM10",
                    "baudrate": 57600,
                    "timeout_s": 0.5,
                    "contact_threshold_g": 25,
                    "channel_fingers": [
                        "thumb",
                        "index",
                        "middle",
                        "ring",
                        "little",
                    ],
                }
            }
        )
        self.assertEqual(tactile.mode, "fsr402")
        self.assertEqual(tactile.fsr402.port, "COM10")
        self.assertEqual(tactile.fsr402.baudrate, 57600)
        self.assertEqual(tactile.fsr402.contact_threshold_g, 25)

    def test_mode_update_preserves_other_mode_configuration(self):
        fsr_port = self.hand.tactile_config.fsr402.port
        self.hand.update_tactile_config(
            True,
            "COM9",
            921600,
            0.25,
            mode="hwk",
            contact_threshold_g=42,
        )
        self.assertEqual(self.hand.tactile_config.mode, "hwk")
        self.assertEqual(self.hand.tactile_config.hwk.port, "COM9")
        self.assertEqual(self.hand.tactile_config.hwk.contact_threshold_g, 42)
        self.assertEqual(self.hand.config["tactile"]["hwk"]["contact_threshold_g"], 42)
        self.assertNotIn("contact_threshold_raw", self.hand.config["tactile"]["hwk"])
        self.assertEqual(self.hand.tactile_config.fsr402.port, fsr_port)
        self.assertEqual(self.hand.tactile_unit, "g")

    def test_updating_legacy_config_does_not_drop_fsr_mapping(self):
        legacy_tactile = {
            "enabled": False,
            "port": "COM10",
            "baudrate": 57600,
            "timeout_s": 0.5,
            "contact_threshold_g": 25,
            "channel_fingers": [
                "little",
                "ring",
                "middle",
                "index",
                "thumb",
            ],
        }
        self.hand.config["tactile"] = legacy_tactile
        self.hand.tactile_config = self.hand._extract_tactile_config(self.hand.config)
        self.hand.update_tactile_config(
            True,
            "COM9",
            921600,
            0.2,
            mode="hwk",
            contact_threshold_g=42,
        )
        fsr = self.hand.tactile_config.fsr402
        self.assertEqual(fsr.port, "COM10")
        self.assertEqual(fsr.baudrate, 57600)
        self.assertEqual(
            fsr.channel_fingers,
            ("little", "ring", "middle", "index", "thumb"),
        )
        self.assertEqual(fsr.contact_threshold_g, 25)

    def test_start_tactile_keeps_old_positional_arguments_and_adds_mode_keyword(self):
        self.hand.tactile_config = self.hand._extract_tactile_config(
            {
                "tactile": {
                    "enabled": True,
                    "mode": "fsr402",
                    "fsr402": {"port": "COM8"},
                }
            }
        )

        class FakeAdapter:
            is_running = True
            last_error = None

            def __init__(self):
                self.started = False
                self.stopped = False

            def start(self):
                self.started = True

            def stop(self):
                self.stopped = True

            def read(self):
                return None

        fsr_adapter = FakeAdapter()
        hwk_adapter = FakeAdapter()
        with patch(
            "parahand.create_tactile_sensor",
            side_effect=(fsr_adapter, hwk_adapter),
        ) as factory:
            self.assertTrue(self.hand.start_tactile("COM12", 230400, 0.4))
            self.assertEqual(factory.call_args.args[0], "fsr402")
            self.assertEqual(factory.call_args.kwargs["port"], "COM12")
            self.assertEqual(factory.call_args.kwargs["timeout_s"], 0.4)
            self.assertTrue(fsr_adapter.started)

            self.assertTrue(
                self.hand.start_tactile(
                    port="COM13",
                    timeout_s=0.3,
                    mode="hwk",
                )
            )
            self.assertTrue(fsr_adapter.stopped)
            self.assertEqual(factory.call_args.args[0], "hwk")
            self.assertEqual(factory.call_args.kwargs["port"], "COM13")
            self.assertEqual(factory.call_args.kwargs["response_timeout_s"], 0.3)
            self.hand.stop_tactile()
            self.assertTrue(hwk_adapter.stopped)


if __name__ == "__main__":
    unittest.main()
