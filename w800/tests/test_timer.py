"""Regression for the provisional, firmware-startup-verified timer model.

Rejected rollover experiments are archived under reports/media-clock.
"""
import unittest
from w800.backend import QemuBackend, ROOT
from w800.tests.test_storage import Bus


@unittest.skipUnless((ROOT / 'build/qemu-system-arm').exists(), 'Local QEMU build required')
class TimerTests(unittest.TestCase):
    def test_reprogramming_deadline_does_not_restart_elapsed_counter(self):
        with QemuBackend(bus_test=True, exploratory=True) as q:
            bus = Bus(q)
            q.command('cont')
            try:
                bus.put(0xf9010004, 130, 'l')
                bus.put(0xf9010000, 1, 'l')
                bus.command('clock_step 1000000')
                self.assertEqual(bus.get(0xf901001c, 'l'), 32)
                bus.put(0xf9010004, 261, 'l')
                self.assertEqual(bus.get(0xf901001c, 'l'), 32)
                bus.command('clock_step 1000000')
                self.assertEqual(bus.get(0xf901001c, 'l'), 65)
                bus.put(0xf9010004, 130, 'l')
                self.assertEqual(bus.get(0xf901001c, 'l'), 65)
                bus.command('clock_step 2000000')
                self.assertEqual(bus.get(0xf9010018, 'l'), 1)
                self.assertEqual(bus.get(0xf9010018, 'l'), 0)
                self.assertEqual(bus.get(0xf901001c, 'l'), 131)
                bus.command('clock_step 1000000')
                self.assertEqual(bus.get(0xf901001c, 'l'), 163)
                bus.put(0xf9010004, 130, 'l')
                self.assertEqual(bus.get(0xf901001c, 'l'), 0)
                bus.command('clock_step 4000000')
                self.assertEqual(bus.get(0xf9010018, 'l'), 1)
                self.assertEqual(q.command('qom-get', {'path': '/machine',
                    'property': 'virtual-time-ns'}), 9000000)
            finally:
                bus.close()

    def test_second_channel_reload_irq_and_disable(self):
        with QemuBackend(bus_test=True, exploratory=True) as q:
            bus = Bus(q)
            q.command('cont')
            try:
                bus.put(0xf9010008,31,'l')
                bus.put(0xf9010000,2,'l')
                bus.command('clock_step 1000000')
                self.assertTrue(bus.get(0xfb020200,'l') & (1<<26))
                self.assertEqual(bus.get(0xf9010018,'l'),2)
                self.assertEqual(bus.get(0xf9010018,'l'),0)
                bus.put(0xf9010008,32,'l')
                bus.command('clock_step 1000000')
                self.assertEqual(bus.get(0xf9010018,'l'),0)
                bus.command('clock_step 10000')
                self.assertEqual(bus.get(0xf9010018,'l'),2)
                bus.put(0xf9010008,32,'l')
                bus.put(0xf9010000,0,'l')
                bus.command('clock_step 2000000')
                self.assertEqual(bus.get(0xf9010018,'l'),0)
            finally:
                bus.close()
