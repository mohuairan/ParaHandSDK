import unittest

import motor


class MotorStreamTest(unittest.TestCase):
    def setUp(self):
        self.transport = motor.Motor()

    def status(self, motor_id, payload=b"\0" * 8):
        return self.transport._build_frame(motor_id << 8, payload)

    def feed(self, data):
        self.transport.receive_buffer.extend(data)
        self.transport._process_receive_buffer()

    def test_noise_between_status_frames_does_not_stall_receiver(self):
        self.feed(self.status(1) + b"\xff" + self.status(2))
        self.assertEqual(set(self.transport._latest_status_response), {1, 2})
        self.assertEqual(len(self.transport.receive_buffer), 0)

    def test_partial_headers_and_frames_survive_each_possible_split(self):
        frame = self.status(9)
        for split in range(1, len(frame)):
            with self.subTest(split=split):
                self.transport = motor.Motor()
                self.feed(b"noise" + frame[:split])
                self.assertEqual(self.transport.response_queue.qsize(), 0)
                self.feed(frame[split:])
                self.assertEqual(self.transport.response_queue.qsize(), 1)
                self.assertIn(9, self.transport._latest_status_response)

    def test_payload_header_is_not_a_frame_boundary(self):
        self.feed(self.status(1, motor.Motor.FRAME_HEAD + b"\0" * 4))
        self.assertEqual(self.transport.response_queue.qsize(), 1)
        self.assertEqual(len(self.transport.receive_buffer), 0)

    def test_broadcast_and_status_can_share_the_same_serial_read(self):
        seen = []

        def callback(response):
            # This callback also verifies it is not called under buffer_lock.
            seen.append(self.transport.get_buffer_status())

        self.transport.add_response_callback(
            motor.ResponseType.ANGLE_BROADCAST, callback,
        )
        self.feed(
            self.status(1) + motor.Motor.ANGLE_BROADCAST_HEAD1
            + b"\0" * 18 + self.status(16)
        )
        self.assertEqual(len(seen), 1)
        self.assertEqual(self.transport.response_queue.qsize(), 3)
        self.assertEqual(set(self.transport._latest_status_response), {1, 16})

    def test_unrecognized_bytes_cannot_grow_buffer_forever(self):
        self.feed(b"\xff" * 4096 + b"\x55\xaa")
        self.assertEqual(self.transport.receive_buffer, b"\x55\xaa")


if __name__ == "__main__":
    unittest.main()
