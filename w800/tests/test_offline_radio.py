import unittest
from w800.offline_radio import OfflineRadio, RadioStatus


class OfflineRadioTests(unittest.TestCase):
    def test_lifecycle_does_not_imply_cellular_registration(self):
        radio = OfflineRadio()
        self.assertFalse(radio.status.service_ready)
        self.assertTrue(radio.start().accepted)
        self.assertTrue(radio.status.service_ready)
        self.assertFalse(radio.status.radio_powered)
        self.assertFalse(radio.status.sim_present)
        self.assertFalse(radio.status.registered)
        self.assertEqual(radio.status.signal_strength, 0)
        self.assertIsNone(radio.status.operator)
        self.assertEqual(radio.networks(), ())
        radio.stop()
        self.assertEqual(radio.status, RadioStatus())

    def test_requests_fail_explicitly_without_creating_connections(self):
        radio = OfflineRadio()
        self.assertEqual(radio.cellular_request('call').reason, 'service_not_started')
        radio.start()
        for operation in ('enable_radio', 'register', 'call', 'sms', 'packet_data'):
            with self.subTest(operation=operation):
                result = radio.cellular_request(operation)
                self.assertFalse(result.accepted)
                self.assertEqual(result.reason, 'unavailable_in_offline_mode')
        self.assertEqual(radio.cellular_request('unknown').reason, 'unsupported_operation')
        self.assertFalse(radio.status.registered)

    def test_repeated_start_reset_and_event_delivery(self):
        radio = OfflineRadio()
        radio.start(); radio.start()
        self.assertEqual(len(radio.drain_events()), 1)
        self.assertEqual(radio.drain_events(), ())
        radio.stop(); radio.start(); radio.reset()
        self.assertEqual(radio.status, RadioStatus())
        self.assertEqual(radio.drain_events(), ())
        self.assertFalse(radio.snapshot()['guest_adapter_connected'])


if __name__ == '__main__':
    unittest.main()
