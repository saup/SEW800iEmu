"""ROM mapping tests use synthetic diagnostics, never substitute handset ROM."""
import hashlib
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

from w800.backend import QemuBackend, ROOT
from w800.tests.test_storage import Bus
from w800.tools.debug import Debugger


@unittest.skipUnless((ROOT / 'build/qemu-system-arm').exists(), 'Local QEMU build required')
class ROMTests(unittest.TestCase):
    def test_supplied_bytes_are_read_only_and_retained_on_reset(self):
        data = bytes(range(256)) * 64
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'diagnostic,not-a-phone-rom.bin'
            source.write_bytes(data)
            for chip_id in (0x8000, 0x8040):
                with self.subTest(chip_id=chip_id), QemuBackend(
                        mask_rom_path=source, chip_id=chip_id, bus_test=True) as q:
                    bus = Bus(q)
                    try:
                        readback = bytes.fromhex(bus.command('read 0xffff0000 0x4000').removeprefix('0x'))
                        self.assertEqual(readback, data)
                        bus.put(0xffff12a2, 0x1234, 'w')
                        self.assertEqual(bus.get(0xffff12a2, 'w'), 0xa3a2)
                        self.assertEqual(bus.get(0xf9090000, 'w'), chip_id)
                        bus.put(0xf9090000, 0, 'w')
                        self.assertEqual(bus.get(0xf9090000, 'w'), chip_id)
                        # This profile advertises a real-sized but unprovisioned
                        # first NOR protection field. It contains no forged ID.
                        bus.put(0x44000000, 0x90, 'w')
                        ext = bus.get(0x4400002a, 'w')
                        self.assertEqual([bus.get(0x44000000 + 2*(ext+0xf+i), 'w') for i in range(4)],
                                         [0x80, 0, 3, 3])
                        self.assertEqual(bus.get(0x44000102, 'w'), 0xffff)
                        q.command('system_reset')
                        self.assertEqual(bus.get(0xffff12a2, 'w'), 0xa3a2)
                        self.assertEqual(q.mask_rom_info['sha256'], hashlib.sha256(data).hexdigest())
                    finally:
                        bus.close()
            self.assertEqual(source.read_bytes(), data)

    def test_standalone_diagnostic_instructions_execute_from_rom(self):
        # This three-instruction fixture is tested in isolation at the ROM
        # base. It implements no original ROM API and never boots MAIN.
        data = struct.pack('<3I', 0xe3a0005a, 0xe2800007, 0xeafffffe) + bytes(16384 - 12)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'standalone-diagnostic.bin'
            source.write_bytes(data)
            with QemuBackend(mask_rom_path=source, debug=True) as q:
                debug = Debugger(q)
                try:
                    regs = bytearray.fromhex(debug.packet('g'))
                    struct.pack_into('<I', regs, 15 * 4, 0xffff0000)
                    self.assertEqual(debug.packet('G' + regs.hex()), 'OK')
                    debug.breakpoint(0xffff0008)
                    debug.run()
                    self.assertEqual(debug.registers()[15], 0xffff0008)
                    self.assertEqual(debug.registers()[0], 97)
                finally:
                    debug.close()

    def test_supplied_protection_words_are_read_back_without_modification(self):
        # Arbitrary bus-test words; no guest CPU or identity check is run.
        words = tuple(range(0x1200, 0x1209))
        data = struct.pack('<9H', *words)
        with tempfile.TemporaryDirectory() as directory:
            rom = Path(directory) / 'diagnostic.bin'
            otp = Path(directory) / 'diagnostic-words.bin'
            rom.write_bytes(bytes(16384))
            otp.write_bytes(data)
            with QemuBackend(mask_rom_path=rom, nor_otp_path=otp, bus_test=True) as q:
                bus = Bus(q)
                try:
                    for reset in (False, True):
                        if reset:
                            q.command('system_reset')
                        bus.put(0x44000000, 0x90, 'w')
                        self.assertEqual([bus.get(0x44000100 + 2*i, 'w') for i in range(9)], list(words))
                        self.assertEqual(bytes(bus.get(0x44000100 + i) for i in range(18)), data)
                    self.assertEqual(q.nor_otp_info['sha256'], hashlib.sha256(data).hexdigest())
                finally:
                    bus.close()
            self.assertEqual(otp.read_bytes(), data)

    def test_unbacked_rom_access_stops_exploratory_execution(self):
        with QemuBackend(bus_test=True, exploratory=True) as q:
            bus = Bus(q)
            try:
                q.command('cont')
                bus.get(0xffff12a2, 'w')
                self.assertIn('W800_MASK_ROM_UNAVAILABLE', q.diagnostics())
                self.assertFalse(q.command('query-status')['running'])
            finally:
                bus.close()

    def test_qemu_rejects_unsupported_raw_image_sizes(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'invalid.bin'
            for size in (0, 16383, 16385, 65537):
                source.write_bytes(bytes(size))
                command = [str(ROOT / 'build/qemu-system-arm'), '-M',
                           'w800,mask-rom=' + str(source).replace(',', ',,'),
                           '-bios', str(ROOT / 'firmware/prepared/flash-gdfs.bin'),
                           '-display', 'none', '-monitor', 'none', '-serial', 'none', '-S']
                result = subprocess.run(command, capture_output=True, text=True, timeout=5)
                with self.subTest(size=size):
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('mask-rom must be a raw', result.stderr)
