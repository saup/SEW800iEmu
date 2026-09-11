import json
from pathlib import Path
import time
import unittest
from w800.backend import QemuBackend,ROOT
from w800.firmware import parse_babe
from w800.tools.debug import Debugger

@unittest.skipUnless((ROOT/'build/qemu-system-arm').exists(),'Local QEMU build required')
class QemuTests(unittest.TestCase):
    def test_original_kernel_irq_and_filesystem_startup(self):
        with QemuBackend(trace=True, exploratory=True, debug=True, mmio_limit=200000) as q:
            debug = Debugger(q)
            # Original callsite prints "FS: Partition %s mounted" only after
            # the FAT mount succeeds. Stop before that call and read its path.
            callsite = 0x4510f66e
            debug.breakpoint(callsite, thumb=True)
            mounted = []
            for _ in range(5):
                debug.run()
                regs = debug.registers()
                self.assertEqual(regs[15], callsite)
                mounted.append(debug.read(regs[1], 128).split(b'\0', 1)[0].decode())
                debug.step_over_breakpoint(callsite, thumb=True)
            self.assertEqual(mounted, ['/', '/tpa/system/bg_images/cache', '/ifs', '/system', '/smsdata'])
            debug.breakpoint(callsite, enabled=False, thumb=True)
            # Initialization writes the power-controller register sequence
            # through the original IRQ-driven I2C driver, then returns here.
            debug.breakpoint(0x44acea62, thumb=True)
            debug.run()
            self.assertEqual(debug.registers()[15], 0x44acea62)
            self.assertEqual(debug.registers()[0], 1)
            report=q.snapshot()
            trace=(q.directory/'trace.log').read_text()
            self.assertIn('Taking exception 5 [IRQ]',trace)
            self.assertIn('0x448e0a2c:',trace)  # Original OS-tick handler
            self.assertIn('0x4490d730:',trace)  # Original I2C interrupt handler
            self.assertIn('Exception return from AArch32 irq to svc',trace)
            self.assertFalse(report['firmware_display_verified'])
            self.assertFalse(report['keypad_verified'])
            debug.close()

    def test_original_vector_bytes_and_bounded_strict_stop(self):
        with QemuBackend() as q:
            vector=q.command('human-monitor-command',{'command-line':'xp /2wx 0x44020000'})
            self.assertIn('0xe59ff018',vector)
            q.command('cont');deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                if not q.command('query-status')['running']:break
                time.sleep(.01)
            self.assertFalse(q.command('query-status')['running'])
            self.assertIn('addr=0x14000000',q.diagnostics())
            self.assertIn('pc=0x44020050',q.diagnostics())
            q.command('system_reset')
            regs=q.command('human-monitor-command',{'command-line':'info registers'})
            self.assertIn('R15=44020000',regs)
    def test_all_prepared_segments_match_originals(self):
        flash=(ROOT/'firmware/prepared/flash.bin').read_bytes()
        for path in ROOT.glob('firmware/W800_R1L002_*.bin'):
            image=parse_babe(path.read_bytes())
            for segment in image.segments:
                offset=segment.address-0x44000000
                self.assertEqual(flash[offset:offset+len(segment.data)],segment.data)
