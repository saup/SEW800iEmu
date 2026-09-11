"""Empty Memory Stick socket must not be mistaken for an inserted card."""
import unittest
import struct
from w800.backend import QemuBackend, ROOT
from w800.tests.test_storage import Bus


@unittest.skipUnless((ROOT / 'build/qemu-system-arm').exists(), 'Local QEMU build required')
class GPIOTests(unittest.TestCase):
    def test_empty_socket_pullup_is_independent_of_output_latch(self):
        with QemuBackend(bus_test=True, exploratory=True) as q:
            bus = Bus(q)
            try:
                q.command('cont')
                # Original GPIO4 read uses a halfword; card-detect is active-low.
                bus.put(0xf9000028, 0x80, 'w')
                self.assertEqual(bus.get(0xf9000028, 'w'), 0x84)
                bus.put(0xf9000028, 0, 'w')
                self.assertEqual(bus.get(0xf9000028, 'w'), 4)
                self.assertEqual(bus.get(0xf9000028), 4)
                self.assertEqual(bus.get(0xf9000029), 0)
                # A write of adjacent output bits cannot suppress the input pin.
                bus.put(0xf9000029, 0xa5)
                self.assertEqual(bus.get(0xf9000028, 'w'), 0xa504)
                q.command('system_reset')
                self.assertEqual(bus.get(0xf9000028, 'w') & 4, 4)
            finally:
                bus.close()

    def test_original_empty_socket_reply_unblocks_filesystem_startup(self):
        from w800.tools.debug import Debugger
        with QemuBackend(exploratory=True, debug=True, mmio_limit=1000000) as q:
            debug = Debugger(q)
            try:
                # Physical GPIO sampling, then the natural MemStick status
                # reply received by FSU, then the pre-MMI initializer return.
                checkpoints = (0x4487f2b0, 0x4498fbf0, 0x450db1f8)
                for pc in checkpoints:
                    debug.breakpoint(pc, thumb=True)
                    debug.run()
                    regs = debug.registers()
                    self.assertEqual(regs[15], pc)
                    if pc == 0x4498fbf0:
                        self.assertEqual(struct.unpack('<I', debug.read(regs[0], 4))[0], 0x5499)
                    else:
                        self.assertEqual(regs[0], 0)
                    debug.breakpoint(pc, enabled=False, thumb=True)
            finally:
                debug.close()
