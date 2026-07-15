from __future__ import annotations

import struct
import unittest

from hwk_tactile.protocol import CHANNEL_DATA, Frame, parse_pressure_payloads


class HWKProtocolPhysicalValueTests(unittest.TestCase):
    def test_compound_payload_separates_pressure_map_and_float_force_summary(self):
        pressure_values = (1, 64, 128, 256)
        payload = b"".join(
            (
                bytes((2, 1, 2, 2)),
                b"".join(value.to_bytes(2, "little") for value in pressure_values),
                bytes((2, 3, 4)),
                struct.pack("<fff", 1.5, -2.25, 42.75),
            )
        )
        frame = Frame(device_id=1, channel=CHANNEL_DATA, flags=0, payload=payload)

        pressure_map, force_summary = parse_pressure_payloads(frame, received_at=123.0)

        self.assertEqual(pressure_map.data_type, "pressure_map")
        self.assertEqual(pressure_map.unit, "g")
        self.assertEqual(pressure_map.matrix, ((1, 64), (128, 256)))
        self.assertEqual(force_summary.data_type, "force_summary")
        self.assertEqual(force_summary.unit, "g")
        self.assertEqual(force_summary.labels, ("Fx", "Fy", "Fnormal_sum"))
        self.assertAlmostEqual(force_summary.values[0], 1.5)
        self.assertAlmostEqual(force_summary.values[1], -2.25)
        self.assertAlmostEqual(force_summary.values[2], 42.75)


if __name__ == "__main__":
    unittest.main()
