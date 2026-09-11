"""Exercise QMP input through DB2010 matrix and interrupt registers."""
import unittest
from w800.backend import QemuBackend, ROOT
from w800.tests.test_storage import Bus


@unittest.skipUnless((ROOT/'build/qemu-system-arm').exists(), 'Local QEMU build required')
class KeypadTests(unittest.TestCase):
    def setUp(self):
        self.q = QemuBackend(bus_test=True)
        self.q.start()
        self.bus = Bus(self.q)
        self.q.command('cont')

    def tearDown(self):
        self.bus.close()
        self.q.close()

    def key(self, qcode, down=True):
        self.q.command('input-send-event', {'events': [
            {'type': 'key', 'data': {'down': down,
                                   'key': {'type': 'qcode', 'data': qcode}}}]})
        self.bus.command('clock_step 6000000')

    def scan(self, row):
        mask = 0x3f if row == 6 else 0x3f & ~(1 << row)
        self.bus.put(0xfb010002, mask, 'w')
        return (self.bus.get(0xfb010002, 'w') >> 6) | 0xe0

    def test_matrix_press_release_and_grounded_power_key(self):
        self.assertEqual([self.scan(i) for i in range(7)], [0xff] * 7)
        self.key('1')
        self.assertEqual([self.scan(i) for i in range(7)],
                         [0xff, 0xff, 0xff, 0xfe, 0xff, 0xff, 0xff])
        self.key('5')
        self.assertEqual(self.scan(2), 0xfd)
        self.assertEqual(self.scan(3), 0xfe)
        self.key('1', False)
        self.assertEqual(self.scan(3), 0xff)
        self.key('5', False)
        self.key('power')
        self.assertEqual([self.scan(i) for i in range(7)], [0xfe] * 7)
        self.key('power', False)
        self.assertEqual([self.scan(i) for i in range(7)], [0xff] * 7)

    def test_joystick_codes_match_original_firmware_decoder(self):
        for key, expected in [('up', 0xe5), ('down', 0xf2),
                              ('left', 0xf1), ('right', 0xe6), ('ret', 0xf7)]:
            self.key(key)
            self.assertEqual(self.scan(4), expected)
            self.key(key, False)
            self.assertEqual(self.scan(4), 0xff)
        self.key('up')
        self.key('left')
        self.assertEqual(self.scan(4), 0xe7)
        self.key('up', False)
        self.assertEqual(self.scan(4), 0xf1)

    def test_irq60_cascade_acknowledge_and_release(self):
        # Bank2 source0 corresponds to guest IRQ60; cascade enters root30.
        self.bus.put(0xfb020104, 0xbfffffff, 'l')
        self.bus.put(0xfb020304, 0xfffffffe, 'l')
        self.bus.put(0xfb010000, 0x280, 'w')
        self.key('f1')
        self.assertEqual(self.bus.get(0xfb020300, 'l') & 1, 1)
        self.assertEqual(self.bus.get(0xfb020100, 'l') & (1 << 30), 1 << 30)
        self.assertEqual(self.bus.get(0xfb020310, 'l'), 0)
        self.bus.put(0xfb010000, 0x380, 'w')
        self.assertEqual(self.bus.get(0xfb020300, 'l') & 1, 0)
        self.assertEqual(self.bus.get(0xfb010000, 'w') & 0x100, 0)
        self.key('f1')  # Host key repeat is not a second physical edge.
        self.assertEqual(self.bus.get(0xfb020300, 'l') & 1, 0)
        self.key('f1', False)
        self.assertEqual(self.bus.get(0xfb020300, 'l') & 1, 1)
        self.assertEqual(self.scan(1), 0xff)
        self.bus.put(0xfb010000, 0x100, 'w')
        self.assertEqual(self.bus.get(0xfb020300, 'l') & 1, 0)

    @unittest.skipUnless((ROOT / 'firmware/prepared/flash-gdfs.bin').exists(),
                         'Prepared GDFS image required')
    def test_original_firmware_process_receives_press_and_release(self):
        from w800.tools.trace_keypad import capture, NAVIGATION_KEYS
        evidence = capture(ROOT / 'firmware/prepared/flash-gdfs.bin')
        events = [row for row in evidence if row['phase'] == 'original_key_signal']
        self.assertEqual([(row['key'], row['event'], row['scan_code']) for row in events],
                         [(key, edge, code) for key, code in NAVIGATION_KEYS for edge in (0, 1)])
        self.assertEqual([row['pc'] for row in evidence if row['phase'] == 'irq'],
                         ['0x44912014'] * (len(NAVIGATION_KEYS) * 2))
