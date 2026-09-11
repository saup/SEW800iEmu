"""Original KNC20115 control packets, with no fabricated camera frames."""
import unittest

from w800.backend import QemuBackend, ROOT
from w800.tests.test_storage import Bus


class CameraTransportBackend(QemuBackend):
    def _machine_option(self):
        return super()._machine_option() + ',camera-transport=on'


@unittest.skipUnless((ROOT / 'build/qemu-system-arm').exists(), 'Local QEMU build required')
class CameraSensorTests(unittest.TestCase):
    BASE = 0xf9040000
    CONTROL = 0xf7000300

    def setUp(self):
        self.q = CameraTransportBackend(bus_test=True)
        self.q.start()
        self.bus = Bus(self.q)

    def tearDown(self):
        self.bus.close()
        self.q.close()

    def control(self, value):
        self.bus.put(self.BASE + 2, value, 'w')
        return self.bus.get(self.BASE + 6, 'w')

    def address(self, reading=False, expected=None):
        self.assertIn(self.control(0xa4), (8, 0x10))
        self.bus.put(self.BASE, 0x3e | reading, 'w')
        result = self.control(0x80)
        if expected is not None:
            self.assertEqual(result, expected)
        return result

    def stop(self):
        self.assertEqual(self.control(0x90), 0xf8)

    def send(self, data, last=0x28):
        self.address(expected=0x18)
        for index, byte in enumerate(data):
            self.bus.put(self.BASE, byte, 'w')
            self.assertEqual(self.control(0x80), last if index == len(data) - 1 else 0x28)
        self.stop()

    def read(self, count):
        self.address(reading=True, expected=0x40)
        values = []
        for index in range(count):
            final = index == count - 1
            self.assertEqual(self.control(0x80 if final else 0x84), 0x58 if final else 0x50)
            values.append(self.bus.get(self.BASE, 'w'))
        self.stop()
        return bytes(values)

    def test_power_reset_and_native_register_packet_round_trip(self):
        self.address(expected=0x20)
        self.stop()
        self.bus.put(self.CONTROL, 0x100, 'l')
        # Literal original JPEG retry limit writes: 0x07d0 = 2000 ms.
        self.send(bytes.fromhex('05 02 00 0b d0'))
        self.send(bytes.fromhex('05 02 00 0c 07'))
        self.send(bytes.fromhex('05 01 00 0b 02'))
        self.assertEqual(self.read(3), bytes.fromhex('03 d0 07'))
        self.bus.put(self.CONTROL, 0, 'l')
        self.address(expected=0x20)
        self.stop()
        self.bus.put(self.CONTROL, 0x100, 'l')
        self.send(bytes.fromhex('05 01 00 0b 02'))
        self.assertEqual(self.read(3), bytes.fromhex('03 00 00'))

    def test_pending_mode_has_no_capture_event_or_receiver_irq(self):
        self.bus.put(self.CONTROL, 0x100, 'l')
        self.send(bytes.fromhex('05 02 00 07 06'))  # 176x144 selector
        self.send(bytes.fromhex('06 02 00 01 01 34'))  # draft, every frame
        for _ in range(2):
            self.send(bytes.fromhex('05 01 00 03 03'))
            self.assertEqual(self.read(4), bytes.fromhex('04 00 00 00'))
        self.bus.put(0xf7000224, 0xff, 'l')
        self.bus.put(0xf7000200, 1, 'l')
        self.bus.put(0xf7000220, 3, 'l')  # status is not guest writable
        self.assertEqual(self.bus.get(0xf7000220, 'l'), 0)
        self.assertEqual(self.bus.get(0xf7000224, 'l'), 0xff)
        self.assertEqual(self.bus.get(0xf7000200, 'l'), 1)

    def test_programmed_camera_dma_waits_for_real_source(self):
        self.bus.put(self.CONTROL, 0x100, 'l')
        self.send(bytes.fromhex('06 02 00 01 01 34'))
        for address, value in ((0xf7000204, 8), (0xf700020c, 8),
                               (0xf7000210, 1), (0xf7000214, 0xb1),
                               (0xf7000200, 1), (0x4c001000, 0x12345678),
                               (0x4c001004, 0x87654321),
                               (0xf2000120, 0xf7000308),
                               (0xf2000124, 0x4c001000), (0xf2000128, 0),
                               (0xf200012c, 0x88489000), (0xf2000130, 0xf109)):
            self.bus.put(address, value, 'l')
        self.assertEqual(self.bus.get(0x4c001000, 'l'), 0x12345678)
        self.assertEqual(self.bus.get(0x4c001004, 'l'), 0x87654321)
        self.assertEqual(self.bus.get(0xf2000130, 'l'), 0xf109)
        self.assertEqual(self.bus.get(0xf2000014, 'l') & 2, 0)
        self.assertEqual(self.bus.get(0xf7000220, 'l'), 0)
        self.send(bytes.fromhex('05 01 00 03 03'))
        self.assertEqual(self.read(4), bytes.fromhex('04 00 00 00'))

    def test_unsupported_packet_and_partial_transaction_reset(self):
        self.bus.put(self.CONTROL, 0x100, 'l')
        self.send(bytes.fromhex('03 55 aa'), last=0x30)  # debug interface not implemented
        self.send(bytes.fromhex('05 01 00 00 ff'), last=0x30)  # length cannot fit
        self.address(expected=0x18)
        for value in (5, 2, 0):
            self.bus.put(self.BASE, value, 'w')
            self.assertEqual(self.control(0x80), 0x28)
        self.stop()
        self.bus.put(self.CONTROL, 0, 'l')
        self.bus.put(self.CONTROL, 0x100, 'l')
        self.send(bytes.fromhex('05 02 00 07 06'))
        self.send(bytes.fromhex('05 01 00 07 01'))
        self.assertEqual(self.read(2), bytes.fromhex('02 06'))
        self.q.command('system_reset')
        self.address(expected=0x20)
        self.assertEqual(self.bus.get(0xf7000224, 'l'), 0)


class CameraDefaultTests(unittest.TestCase):
    def test_incomplete_transport_remains_disabled_by_default(self):
        q = QemuBackend(bus_test=True)
        bus = None
        try:
            q.start()
            bus = Bus(q)
            self.assertFalse(q.command('qom-get', {'path': '/machine', 'property': 'camera-transport'}))
            self.assertFalse(q.command('qom-get', {'path': '/machine', 'property': 'host-camera'}))
            self.assertFalse(q.command('qom-get', {'path': '/machine', 'property': 'camera-paused'}))
            bus.put(0xf7000300, 0x100, 'l')
            bus.put(0xf9040002, 0xa4, 'w')
            bus.put(0xf9040000, 0x3e, 'w')
            bus.put(0xf9040002, 0x80, 'w')
            self.assertEqual(bus.get(0xf9040006, 'w'), 0x20)
        finally:
            if bus:
                bus.close()
            q.close()
