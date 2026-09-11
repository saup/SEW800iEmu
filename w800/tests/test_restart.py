"""The original firmware's forced-reset sequence must reset the machine."""
import time
import unittest
from w800.backend import QemuBackend, ROOT
from w800.tests.test_storage import Bus


@unittest.skipUnless((ROOT / 'build/qemu-system-arm').exists(), 'Local QEMU build required')
class RestartTests(unittest.TestCase):
    def test_original_forced_reset_sequence_and_storage_retention(self):
        with QemuBackend(bus_test=True, exploratory=True) as q:
            bus = Bus(q)
            try:
                # Seed a real NOR write in the GDFS spare block. The following
                # reset must retain it, as does QMP system_reset.
                target = 0x45fe0000
                bus.put(target, 0x40, 'w')
                bus.put(target, 0x1234, 'w')
                bus.put(target, 0xff, 'w')
                # Ordinary watchdog service sequence from original 4490cfa4.
                bus.put(0xf9010010, 0xaa, 'l')
                bus.put(0xf9010024, 0x5a, 'l')
                bus.put(0xf901002c, 0xa5, 'l')
                q.command('query-status')
                self.assertFalse(any(e['event'] == 'RESET' for e in q.events))
                # Original forced-reset routine 4490cf80 disables interrupts,
                # clears these two registers and repeatedly writes control=1.
                bus.put(0xf9010030, 0, 'l')
                bus.put(0xf901002c, 0, 'l')
                bus.put(0xf9010014, 1, 'l')
                deadline = time.monotonic() + 1
                while time.monotonic() < deadline:
                    q.command('query-status')
                    if any(e['event'] == 'RESET' for e in q.events):
                        break
                    time.sleep(.01)
                resets = [e for e in q.events if e['event'] == 'RESET']
                self.assertEqual(len(resets), 1, 'Original reset request did not reset QEMU')
                self.assertTrue(resets[0]['data']['guest'])
                self.assertEqual(bus.get(target, 'w'), 0x1234)
                self.assertEqual(bus.get(0xf9010014, 'l'), 0)
            finally:
                bus.close()
