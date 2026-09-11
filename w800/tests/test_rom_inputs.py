"""Host ROM input boundaries. Fixtures are diagnostic bytes, never real ROMs."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from w800.backend import QemuBackend


class RomInputTests(unittest.TestCase):
    def test_nor_otp_requires_rom_and_exact_raw_length(self):
        with self.assertRaisesRegex(ValueError, 'requires a mask ROM'):
            QemuBackend(nor_otp_path='diagnostic-otp.bin')
        with tempfile.TemporaryDirectory() as directory:
            rom = Path(directory) / 'diagnostic-rom.bin'
            otp = Path(directory) / 'diagnostic-otp.bin'
            rom.write_bytes(b'\xa5' * 16384)
            for size in (0, 1, 16, 17, 19, 4096):
                otp.write_bytes(b'\xa5' * size)
                q = QemuBackend(mask_rom_path=rom, nor_otp_path=otp)
                try:
                    with self.subTest(size=size), self.assertRaisesRegex(ValueError, 'exactly 18 bytes'):
                        q._machine_option()
                    self.assertFalse((q.directory / 'nor-otp.bin').exists())
                finally:
                    q.close()
            for path in (Path(directory), Path(directory) / 'missing.bin'):
                q = QemuBackend(mask_rom_path=rom, nor_otp_path=path)
                try:
                    with self.assertRaisesRegex(ValueError, 'readable raw image'):
                        q._machine_option()
                finally:
                    q.close()

    def test_nor_otp_stages_exact_input_and_records_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            rom = Path(directory) / 'diagnostic-rom.bin'
            otp = Path(directory) / 'diagnostic,chip-id=0x8000.bin'
            rom.write_bytes(b'\xa5' * 16384)
            data = bytes(range(18))  # Diagnostic pattern, not real identity data.
            otp.write_bytes(data)
            q = QemuBackend(mask_rom_path=rom, nor_otp_path=otp)
            try:
                q.directory = q.directory / 'private,comma'
                q.directory.mkdir()
                machine = q._machine_option()
                staged = q.directory / 'nor-otp.bin'
                self.assertTrue(machine.endswith(',nor-otp=' + str(staged).replace(',', ',,')))
                self.assertNotIn(otp.name, machine)
                otp.write_bytes(b'changed after staging')
                self.assertEqual(staged.read_bytes(), data)
                with patch.object(q, 'command', return_value={}), \
                     patch.object(q, 'guest_diagnostics', return_value=[]):
                    report = q.snapshot()
                self.assertEqual(report['nor_otp'], {
                    'source_path': str(otp.resolve()), 'sha256': hashlib.sha256(data).hexdigest(),
                    'size': 18, 'format': 'little-endian 16-bit words 0x80–0x88'})
                self.assertFalse(report['original_boot_verified'])
            finally:
                q.close()

    def test_raw_image_size_and_profile_validation(self):
        for chip_id in (0, 0x8001, '0x8040', 32832.0):
            with self.subTest(chip_id=chip_id), self.assertRaises(ValueError):
                QemuBackend(chip_id=chip_id)
        with tempfile.TemporaryDirectory() as directory:
            rom = Path(directory) / 'diagnostic.bin'
            for size in (0, 4096, 16383, 16385, 65537):
                rom.write_bytes(b'\xa5' * size)
                q = QemuBackend(mask_rom_path=rom)
                try:
                    with self.subTest(size=size), self.assertRaises(ValueError):
                        q._machine_option()
                    self.assertFalse((q.directory / 'mask-rom.bin').exists())
                finally:
                    q.close()
            for path in (Path(directory), Path(directory) / 'missing.bin'):
                q = QemuBackend(mask_rom_path=path)
                try:
                    with self.assertRaises(ValueError):
                        q._machine_option()
                finally:
                    q.close()
            for size in (16384, 20480, 65536):
                rom.write_bytes(b'\xa5' * size)
                q = QemuBackend(mask_rom_path=rom)
                try:
                    q._machine_option()
                    self.assertEqual(q.mask_rom_info['size'], size)
                finally:
                    q.close()

    def test_staging_snapshot_and_machine_option_escaping(self):
        with tempfile.TemporaryDirectory() as directory:
            # Deliberately resembles injected machine options. It is a filename.
            rom = Path(directory) / 'diagnostic,chip-id=0x8000,space name.bin'
            data = bytes(range(256)) * 64
            rom.write_bytes(data)
            q = QemuBackend(mask_rom_path=rom, chip_id=0x8040, exploratory=True)
            try:
                q.directory = q.directory / 'private,comma'
                q.directory.mkdir()
                machine = q._machine_option()
                staged = q.directory / 'mask-rom.bin'
                self.assertEqual(machine, 'w800,exploratory=on,mmio-limit=10000,mask-rom=' +
                                 str(staged).replace(',', ',,') + ',chip-id=0x8040')
                self.assertNotIn(rom.name, machine)
                self.assertEqual(staged.read_bytes(), data)
                rom.write_bytes(b'changed after staging')
                with patch.object(q, 'command', return_value={}), \
                     patch.object(q, 'guest_diagnostics', return_value=[]):
                    snapshot = q.snapshot()
                self.assertEqual(snapshot['mask_rom'], {
                    'source_path': str(rom.resolve()), 'sha256': hashlib.sha256(data).hexdigest(),
                    'size': 16384, 'base': '0xffff0000', 'chip_id': '0x8040',
                    'profile': 'DB2010 chip-id 0x8040'})
                self.assertEqual(staged.read_bytes(), data)
                self.assertFalse(snapshot['original_boot_verified'])
            finally:
                q.close()

    def test_without_rom_preserves_default_machine(self):
        for chip_id in (0x8000, 0x8040):
            q = QemuBackend(chip_id=chip_id)
            try:
                self.assertEqual(q._machine_option(), 'w800')
                self.assertIsNone(q.mask_rom_info)
                self.assertIsNone(q.nor_otp_info)
            finally:
                q.close()



if __name__ == '__main__':
    unittest.main()
