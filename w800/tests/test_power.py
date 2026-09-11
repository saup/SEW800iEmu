"""Vincenne physical bus behavior, without guest firmware modifications."""
import unittest
from w800.backend import QemuBackend, ROOT
from w800.tests.test_storage import Bus


@unittest.skipUnless((ROOT/'build/qemu-system-arm').exists(), 'Local QEMU build required')
class PowerTests(unittest.TestCase):
    def setUp(self):
        self.q = QemuBackend(bus_test=True)
        self.q.start()
        self.bus = Bus(self.q)
        self.q.command('cont')

    def tearDown(self):
        self.bus.close()
        self.q.close()

    def control(self, value):
        self.bus.put(0xf9040002, value, 'w')
        self.bus.command('clock_step 20000')

    def data(self, value):
        self.bus.put(0xf9040000, value, 'w')
        self.control(0)

    def select(self, register):
        self.control(0x20)
        self.data(0x94)
        self.data(register)

    def put(self, register, value):
        self.select(register)
        self.data(value)
        self.control(0x10)

    def get(self, register):
        self.select(register)
        self.control(0x20)
        self.data(0x95)
        self.control(0)
        value = self.bus.get(0xf9040000, 'w')
        self.control(0x10)
        return value

    def test_identification_and_physical_status_are_read_only(self):
        self.assertEqual(self.get(0x20), 0xe0)
        self.assertEqual(self.get(0x23), 0)
        self.put(0x20, 0x55)
        self.put(0x23, 0xff)
        self.assertEqual(self.get(0x20), 0xe0)
        self.assertEqual(self.get(0x23), 0)

    def test_powerup_event_mask_and_clear(self):
        self.assertEqual(self.bus.get(0xfb020100, 'l'), 0)
        self.bus.put(0xfb020104, ~(1 << 25) & 0xffffffff, 'l')
        self.put(0x26, 0xfe)
        self.assertEqual(self.bus.get(0xfb020100, 'l'), 1 << 25)
        self.assertEqual(self.bus.get(0xfb020110, 'l'), 25)
        self.assertEqual(self.bus.get(0xfb020100, 'l'), 0)
        # Vector acknowledgement does not consume the PMIC's event cause.
        # Unrelated IRQ recomputation while its wire remains high cannot
        # continually relatch the same assertion and starve the I2C worker.
        self.bus.put(0xfb020104, ~(1 << 25) & 0xffffffff, 'l')
        self.assertEqual(self.bus.get(0xfb020100, 'l'), 0)
        self.assertEqual(self.get(0x21), 1)
        self.assertEqual(self.bus.get(0xfb020100, 'l'), 0)
        self.assertEqual(self.get(0x21), 0)
        self.assertEqual(self.get(0x22), 0)

    def test_masked_controller_keeps_external_edge(self):
        self.put(0x26, 0xfe)
        self.assertEqual(self.bus.get(0xfb020100, 'l'), 1 << 25)
        # Clearing the PMIC while INTC is masked must not lose a captured edge.
        self.assertEqual(self.get(0x21), 1)
        self.bus.put(0xfb020104, ~(1 << 25) & 0xffffffff, 'l')
        self.assertEqual(self.bus.get(0xfb020110, 'l'), 25)
        self.assertEqual(self.bus.get(0xfb020100, 'l'), 0)

    def test_nominal_adc_conversion_latches_result(self):
        self.assertEqual(self.get(0x71), 0)
        self.put(0x70, 0xa8)
        self.assertEqual(self.get(0x71), 0x80)
        self.put(0x71, 0xff)
        self.assertEqual(self.get(0x71), 0x80)

    def test_host_power_key_generates_physical_edges(self):
        self.get(0x21)  # acknowledge the latched cold-start cause
        self.put(0x26, 0xfe)
        self.bus.put(0xfb020104, ~(1 << 25) & 0xffffffff, 'l')
        def key(down):
            self.q.command('input-send-event', {'events': [{
                'type': 'key', 'data': {'down': down, 'key': {
                    'type': 'qcode', 'data': 'power'}}}]})
        key(True)
        self.assertEqual(self.get(0x23), 1)
        self.assertEqual(self.get(0x21), 1)
        key(True)
        self.assertEqual(self.get(0x21), 0)
        key(False)
        self.assertEqual(self.get(0x23), 0)
        self.assertEqual(self.get(0x21), 1)
        self.assertEqual(self.get(0x21), 0)
