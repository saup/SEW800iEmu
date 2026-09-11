import unittest
from w800.backend import QemuBackend, ROOT
from w800.tests.test_storage import Bus


@unittest.skipUnless((ROOT/'build/qemu-system-arm').exists(), 'Local QEMU build required')
class RTCTests(unittest.TestCase):
    def setUp(self):
        self.q=QemuBackend(bus_test=True);self.q.start();self.bus=Bus(self.q)
        self.q.command('cont')
    def tearDown(self):self.bus.close();self.q.close()
    def put(self,offset,value):self.bus.put(0xf9050000+offset,value,'w')
    def get(self,offset):return self.bus.get(0xf9050000+offset,'w')
    def set_date(self):
        # Original SetTime writes year, century, month/day, hour/min/sec,
        # then commits by writing the fraction register.
        for offset,value in ((0x10,24),(0x12,20),(0xe,2),(0xc,28),(8,23),(6,59),(4,59),(2,0)):
            self.put(offset,value)
    def test_calendar_rollover_and_fraction(self):
        self.set_date();self.bus.command('clock_step 2000000000')
        self.assertEqual([self.get(i) for i in (0x12,0x10,0xe,0xc,8,6,4)],[20,24,2,29,0,0,1])
        self.bus.command('clock_step 500000000');self.assertEqual(self.get(2),64)
    def test_oscillator_and_control_bits_do_not_reset_clock(self):
        self.set_date();before=self.get(0)
        self.bus.command('clock_step 1000000');self.assertNotEqual(self.get(0),before)
        for i in range(10):
            self.bus.command('clock_step 100000000');self.put(0x58,0x8c|(i&1))
        self.assertEqual(self.get(4),0)
    def test_soc_reset_retains_battery_domain(self):
        self.set_date();self.put(0x50,0x5a)
        self.q.command('system_reset')
        self.assertEqual([self.get(i) for i in (0x12,0x10,0xe,0xc)],[20,24,2,28])
        self.assertEqual(self.get(0x50),0x5a)
    def test_alarm_validity_tracks_fields(self):
        self.set_date()
        self.assertEqual(self.get(0x1e)&0xd0,0xd0)
        for offset,value in ((0x30,15),(0x32,20),(0x34,7),(0x36,29),(0x38,2),(0x3a,24)):
            self.put(offset,value)
        self.assertEqual(self.get(0x1e)&8,8)
        self.put(0x3a,23)
        self.assertEqual(self.get(0x1e)&8,0)
        self.put(0x2c,60)
        self.assertEqual(self.get(0x1e)&0x10,0)

    def test_daily_match_latches_masked_then_routes_irq43_and_read_acknowledges(self):
        self.set_date()  # 2024-02-28 23:59:59; daily alarm at midnight.
        for offset in (0x2a, 0x2c, 0x2e): self.put(offset, 0)
        self.put(0x16, 0x20)
        self.bus.command('clock_step 2000000000')
        self.assertEqual(self.bus.get(0xfb020200, 'l') & (1 << 15), 0)
        self.put(0x18, 4)
        self.assertEqual(self.bus.get(0xfb020200, 'l') & (1 << 15), 1 << 15)
        self.bus.put(0xfb020204, 0xffffffff ^ (1 << 15), 'l')
        self.assertEqual(self.bus.get(0xfb020100, 'l') & (1 << 28), 1 << 28)
        self.assertEqual(self.get(0x1c) & 0x24, 4)
        self.assertEqual(self.get(0x1c) & 0x24, 0)
        self.assertEqual(self.bus.get(0xfb020200, 'l') & (1 << 15), 0)
        # Keeping the comparator enabled does not repeat within this day.
        self.bus.command('clock_step 2000000000')
        self.assertEqual(self.get(0x1c) & 0x24, 0)
        self.bus.command('clock_step 86400000000000')
        self.assertEqual(self.get(0x1c) & 0x24, 4)

    def test_absolute_alarm_matches_leap_date_once_and_sets_enabled_wake_latch(self):
        self.set_date()
        for offset, value in ((0x30, 1), (0x32, 0), (0x34, 0),
                              (0x36, 29), (0x38, 2), (0x3a, 24)):
            self.put(offset, value)
        self.put(0x16, 0x80); self.put(0x18, 0x20); self.put(0x5a, 0x84)
        self.bus.command('clock_step 1000000000')
        self.assertEqual(self.get(0x1c) & 0x24, 0)
        self.bus.command('clock_step 1000000000')
        self.assertEqual(self.get(0x1c) & 0x24, 0x20)
        self.assertEqual(self.get(0x5c) & 5, 4)
        self.put(0x5c, 0)
        self.put(0x18, 0); self.put(0x18, 0x20)
        self.bus.command('clock_step 86400000000000')
        self.assertEqual(self.get(0x1c) & 0x24, 0)
        self.assertEqual(self.get(0x5c) & 5, 0)

    def test_disabled_or_invalid_comparator_does_not_fire(self):
        self.set_date()
        for offset, value in ((0x30, 0), (0x32, 0), (0x34, 0),
                              (0x36, 30), (0x38, 2), (0x3a, 24)):
            self.put(offset, value)
        self.put(0x16, 0x80); self.put(0x18, 0x24)
        self.bus.command('clock_step 86400000000000')
        self.assertEqual(self.get(0x1c) & 0x24, 0)
        self.put(0x16, 0x20)
        self.put(0x16, 0)
        self.bus.command('clock_step 86400000000000')
        self.assertEqual(self.get(0x1c) & 0x24, 0)

    def test_stopped_clock_preserves_fraction_and_defers_alarm_until_resumed(self):
        self.set_date()
        for offset in (0x2a, 0x2c, 0x2e): self.put(offset, 0)
        self.put(0x16, 0x20); self.put(0x18, 4)
        self.bus.command('clock_step 123000000')
        before = [self.get(i) for i in (0, 2, 4)]
        self.put(0x58, 0)
        self.bus.command('clock_step 2000000000')
        self.assertEqual([self.get(i) for i in (0, 2, 4)], before)
        self.assertEqual(self.get(0x1c) & 0x24, 0)
        self.put(0x58, 0x8c)
        self.bus.command('clock_step 876000000')
        self.assertEqual(self.get(0x1c) & 0x24, 0)
        self.bus.command('clock_step 1000000')
        self.assertEqual(self.get(0x1c) & 0x24, 4)

    def test_soc_reset_retains_armed_alarm_and_latched_match(self):
        self.set_date()
        for offset in (0x2a, 0x2c, 0x2e): self.put(offset, 0)
        self.put(0x16, 0x20); self.put(0x18, 4)
        self.q.command('system_reset')
        self.bus.command('clock_step 1000000000')
        self.assertEqual(self.bus.get(0xfb020200, 'l') & (1 << 15), 1 << 15)
        self.q.command('system_reset')
        self.assertEqual(self.get(0x1c) & 0x24, 4)
        self.assertEqual(self.get(0x1c) & 0x24, 0)
