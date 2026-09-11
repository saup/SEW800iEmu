"""Original decoder resource protocol uses real bounded stream storage."""
import unittest
import sys
import struct

from w800.backend import QemuBackend, ROOT
from w800.tests.test_storage import Bus


@unittest.skipUnless((ROOT / 'build/qemu-system-arm').exists(), 'Local QEMU build required')
class AACResourceTests(unittest.TestCase):
    def test_input_stream_ownership_open_write_and_release(self):
        with QemuBackend(bus_test=True) as q:
            bus = Bus(q)
            q.command('cont')
            try:
                def put(offset, value):
                    bus.put(0xfa040000 + offset, value, 'l')
                def get(offset):
                    return bus.get(0xfa040000 + offset, 'l')
                def send(channel, *words):
                    for i, value in enumerate([((len(words) + 1) << 11) | 10,
                                                *words, channel]):
                        bus.put(0xfa040040 + i * 2, value, 'w')
                    put(0, 1)
                    self.assertEqual(get(0) & 1, 0)
                # Known uploaded kernel establishes this transport endpoint.
                words = {0xae41: 0xec31, 0xae42: 0xbe00, 0xae43: 0xb2dc,
                         0xae57: 0xfb41, 0xae58: 0x0202, 0xb32a: 2}
                for pair in sorted({address & ~1 for address in words}):
                    put(0x28, pair)
                    put(0x2c, (words.get(pair, 0) << 16) | words.get(pair + 1, 0))
                    put(0x24, 1)
                send(18, 0x0e04, 3)
                self.assertEqual(get(0x80), 0x0304180a)
                self.assertEqual(get(0x84), 0x00140400)
                # The control reply cannot overwrite an unread stream-open.
                bus.command('clock_step 200000')
                self.assertEqual(get(0x80), 0x0304180a)
                put(4, 0xfffffffe)
                bus.command('clock_step 200000')
                self.assertEqual(get(0x80), 0x0e05180a)
                self.assertEqual(get(0x84), 0x00120001)
                put(4, 0xfffffffe)
                if sys.platform == 'darwin':
                    # Observed original AAC-LC configuration, backed by a
                    # real decoder allocation; response includes its clock.
                    configuration = [0x0e00,3,1,1,0,3,1,3,0,0,1,0,0,32,
                                     0,0,0,0xffff,0xffff,1,0,1,0,0xcc01]
                    usec = q.command('qom-get', {'path':'/machine',
                        'property':'virtual-time-ns'}) // 1000
                    send(18, *configuration)
                    bus.command('clock_step 200000')
                    self.assertEqual(get(0x80), 0x0e01280a, q.diagnostics())
                    self.assertEqual(get(0x84) & 0xffff, 1)
                    self.assertEqual((get(0x84) >> 16) << 16 | (get(0x88) & 0xffff), usec)
                    self.assertIn('AAC_CONFIG codec=AAC-LC rate=16000 channels=2', q.diagnostics())
                    put(4, 0xfffffffe)
                    # Original coefficient upload is a padded long DMA
                    # message. Only its48 indicated coefficients belong to
                    # the retained3:1 FIR resource; padding is ignored.
                    coefficients = [((i * 701) % 24000) - 12000 for i in range(48)]
                    payload = struct.pack('<4H48h', 0x0e02, 0, 0, 48, *coefficients) + bytes([0xa5] * 24)
                    source = 0x4c100000
                    bus.command(f'write {source:#x} 128 0x{payload.hex()}')
                    bus.put(0xf2000140, source, 'l')
                    bus.put(0xf2000144, 0xfa040020, 'l')
                    bus.put(0xf200014c, 0xa4492020, 'l')
                    bus.put(0xf2000150, 0xc8c7, 'l')
                    put(0x40, 0x00021814)
                    put(0x44, 0x00400012)
                    put(0, 1)
                    self.assertEqual(bus.get(0xf2000140, 'l'), source + 128)
                    bus.command('clock_step 200000')
                    self.assertEqual(get(0x80), 0x0e03100a)
                    self.assertIn('AAC_FILTER slot=0 half_taps=48 history=32 ratio=3:1', q.diagnostics())
                    put(4, 0xfffffffe)
                send(20, 0x0404, 300)
                self.assertEqual(get(4), 0)
                send(20, 0x0804, 4, 0x1234, 0x5678, 0xabcd, 0xef01)
                bus.command('clock_step 200000')
                self.assertEqual(get(4), 0)  # Copying is not consumption.
                self.assertIn('AAC_INPUT words=4 queued=4 first=1234', q.diagnostics())
                send(18, 0x0e04, 4)
                self.assertEqual(get(0x80), 0x0604100a)
                put(4, 0xfffffffe)
                bus.command('clock_step 200000')
                self.assertEqual(get(0x80), 0x0e05180a)
                self.assertIn('AAC_RESOURCE operation=4 capacity=0', q.diagnostics())
                # Reopen starts with an empty allocation, preserving no data.
                put(4, 0xfffffffe)
                send(18, 0x0e04, 3)
                put(4, 0xfffffffe)
                bus.command('clock_step 200000')
                put(4, 0xfffffffe)
                send(20, 0x0404, 300)
                send(20, 0x0804, 2, 0xbeef, 0xdead)
                self.assertIn('AAC_INPUT words=2 queued=2 first=beef', q.diagnostics())
            finally:
                bus.close()
