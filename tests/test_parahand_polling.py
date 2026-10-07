import threading
import unittest

from parahand import JointDefinition, ParaHand


class _Motor:
    connected = True
    timeout_s = 0.05

    def __init__(self):
        self.requested = []
        self.batches = []

    @staticmethod
    def _status(motor_id):
        return {
            "angle": float(motor_id),
            "online": True,
            "fsm_state": 1,
            "error_code": 0,
            "temp": 30.0,
            "bus_voltage": 12.0,
            "received_at_monotonic_s": 123.0 + motor_id,
        }

    def get_motor_status(self, motor_id, *, sync, timeout):
        self.requested.append((motor_id, sync, timeout))
        return self._status(motor_id)

    def get_motor_statuses_sync(self, motor_ids, *, timeout):
        motor_ids = tuple(motor_ids)
        self.batches.append((motor_ids, timeout))
        return {
            motor_id: self._status(motor_id)
            for motor_id in motor_ids
        }


def _make_hand():
    hand = ParaHand.__new__(ParaHand)
    hand.motor = _Motor()
    hand.ctrl_frequency = 50
    hand.joint_to_motor = {
        "first": JointDefinition(1, -90.0, 90.0, False, True),
        "second": JointDefinition(2, -90.0, 90.0, False, True),
    }
    hand.motor_to_joint = {1: "first", 2: "second"}
    hand._feedback_lock = threading.Lock()
    hand._motor_feedback = {}
    hand._poll_failure_counts = {}
    hand._poll_next_allowed_at = {}
    hand._poll_bus_pause_until = 0.0
    hand._poll_cursor = 0
    return hand


class ParaHandPollingTest(unittest.TestCase):
    def test_poll_next_spends_one_status_transaction_per_tick(self):
        hand = _make_hand()

        first = hand.poll_next()
        second = hand.poll_next()

        self.assertEqual([item[0] for item in hand.motor.requested], [1, 2])
        self.assertEqual(set(first), {1})
        self.assertEqual(set(second), {2})
        self.assertIsNotNone(
            first[1]["position_updated_at_monotonic_s"]
        )

    def test_poll_once_refreshes_every_motor_in_one_pipelined_batch(self):
        hand = _make_hand()

        feedback = hand.poll_once()

        self.assertEqual(hand.motor.requested, [])
        self.assertEqual(hand.motor.batches, [((1, 2), 0.05)])
        self.assertEqual(set(feedback), {1, 2})
        self.assertEqual(
            feedback[1]["position_updated_at_monotonic_s"],
            124.0,
        )


if __name__ == "__main__":
    unittest.main()
