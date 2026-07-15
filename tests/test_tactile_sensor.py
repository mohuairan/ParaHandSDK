from __future__ import annotations

import threading
import time
import unittest
from types import SimpleNamespace

from hwk_tactile.protocol import PressurePacket, PressureReading
from tactile_sensor import FSR402TactileAdapter, HWKTactileAdapter


class FakeFSR402Sensor:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        self.frame = SimpleNamespace(
            channels=[
                SimpleNamespace(channel=index, force_g=float(index + 1) * 10.0)
                for index in range(5)
            ],
            timestamp=123.0,
        )

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def read(self):
        return self.frame


class FakeHWKBus:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.opened = False
        self.closed = False
        self.calibrations = []

    def open(self):
        self.opened = True
        return self

    def close(self):
        self.closed = True

    def iter_readings(self, device_ids, *, stop_event, **_kwargs):
        for device_id in device_ids:
            packet = PressurePacket(
                device_id=device_id,
                request_id=0,
                total_packets=2,
                packet_index=1,
                columns=2,
                rows=2,
                values=(device_id, device_id + 1, device_id + 2, device_id + 3),
                received_at=100.0 + device_id,
                unit="g",
            )
            force_summary = PressurePacket(
                device_id=device_id,
                request_id=0,
                total_packets=2,
                packet_index=2,
                columns=3,
                rows=1,
                values=(1.25, -2.5, 30.0 + device_id),
                received_at=100.0 + device_id,
                bytes_per_value=4,
                unit="g",
                data_type="force_summary",
                labels=("Fx", "Fy", "Fnormal_sum"),
            )
            yield PressureReading(device_id, 0, (packet, force_summary))
        stop_event.wait(1.0)

    def send_zero_calibration(self, device_id, command):
        self.calibrations.append((device_id, command))


class TactileAdapterTests(unittest.TestCase):
    def test_fsr402_snapshot_preserves_grams_and_finger_mapping(self):
        created = []

        def factory(**kwargs):
            sensor = FakeFSR402Sensor(**kwargs)
            created.append(sensor)
            return sensor

        adapter = FSR402TactileAdapter(
            port="COM8",
            channel_fingers=("little", "ring", "middle", "index", "thumb"),
            sensor_factory=factory,
        )
        adapter.start()
        snapshot = adapter.read()
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.mode, "fsr402")
        self.assertEqual(snapshot.unit, "g")
        self.assertEqual(snapshot.value_map["little"], 10.0)
        self.assertEqual(snapshot.value_map["thumb"], 50.0)
        adapter.stop()
        self.assertTrue(created[0].stopped)

    def test_hwk_snapshot_keeps_matrices_and_uses_primary_peak(self):
        created = []

        def factory(**kwargs):
            bus = FakeHWKBus(**kwargs)
            created.append(bus)
            return bus

        adapter = HWKTactileAdapter(
            port="COM9",
            device_regions={1: "thumb", 2: "index"},
            bus_factory=factory,
        )
        adapter.start()
        deadline = time.monotonic() + 1.0
        snapshot = None
        while snapshot is None and time.monotonic() < deadline:
            snapshot = adapter.read()
            threading.Event().wait(0.01)
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.mode, "hwk")
        self.assertEqual(snapshot.unit, "g")
        self.assertEqual(snapshot.value_map["thumb"], 4.0)
        self.assertEqual(snapshot.matrices["index"][0], ((2.0, 3.0), (4.0, 5.0)))
        self.assertEqual(len(snapshot.matrices["index"]), 1)
        self.assertEqual(snapshot.force_summaries["thumb"]["Fx"], 1.25)
        self.assertEqual(snapshot.force_summaries["index"]["Fnormal_sum"], 32.0)
        adapter.calibrate(1)
        self.assertEqual(created[0].calibrations, [(1, 2)])
        adapter.stop()
        self.assertTrue(created[0].closed)


if __name__ == "__main__":
    unittest.main()
