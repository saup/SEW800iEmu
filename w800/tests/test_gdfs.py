"""Validate GDFS backup boundaries and the original driver's NOR record layout."""
from collections import Counter
from pathlib import Path
import struct
import tempfile
import unittest
from w800.gdfs import (BLOCK_SIZE, GDFS_BASE, GDFS_SIZE, BANK_COUNT,
                       Unit, parse_backup, encode_banks, combine_flash, prepare)
from w800.firmware import FLASH_BASE, FLASH_SIZE
from w800.backend import ROOT


def backup(*records):
    return struct.pack('<I', len(records)) + b''.join(
        struct.pack('<BHI', u.bank, u.number, len(u.data)) + u.data for u in records)


class GdfsTests(unittest.TestCase):
    def test_backup_truncation_duplicates_and_bank_limits(self):
        one = backup(Unit(0, 5, b'abc'))
        self.assertEqual(parse_backup(one), (Unit(0, 5, b'abc'),))
        for truncated in [one[:i] for i in range(len(one))]:
            with self.assertRaises(ValueError): parse_backup(truncated)
        for invalid in [one + b'\0', backup(Unit(7, 1, b'x')),
                        backup(Unit(0, 1, b'')),
                        backup(Unit(0, 1, b'x'), Unit(0, 1, b'y')),
                        b'\xff' * 4]:
            with self.assertRaises(ValueError): parse_backup(invalid)
        # The guest indexes units globally; duplicate IDs in separate banks
        # would alias that table even though the backup includes bank tags.
        with self.assertRaises(ValueError):
            parse_backup(backup(Unit(0, 1, b'x'), Unit(1, 1, b'y')))

    def test_bank_record_layout_alignment_spare_and_overlap(self):
        region = encode_banks((Unit(0, 0x1234, b'abc'), Unit(0, 9, b'hello'),
                               Unit(6, 2, b'Z')))
        self.assertEqual(len(region), GDFS_SIZE)
        for bank in range(BANK_COUNT):
            self.assertEqual(region[bank * BLOCK_SIZE:bank * BLOCK_SIZE + 4],
                             struct.pack('<BBH', 0x1f, bank, 0))
        self.assertEqual(region[4:16], b'abc\xffhello\xff\xff\xff')
        self.assertEqual(struct.unpack_from('<HHIII', region, BLOCK_SIZE - 16),
                         (0x3ff, 0x1234, 4, 3, 0xffffffff))
        self.assertEqual(struct.unpack_from('<HHIII', region, BLOCK_SIZE - 32),
                         (0x3ff, 9, 8, 5, 0xffffffff))
        self.assertEqual(region[7 * BLOCK_SIZE:], b'\xff' * BLOCK_SIZE)
        with self.assertRaisesRegex(ValueError, 'overlap'):
            encode_banks((Unit(0, 1, b'x' * (BLOCK_SIZE - 19)),))
        with self.assertRaisesRegex(ValueError, 'overlap'):
            encode_banks((Unit(0, 1, b'x' * (BLOCK_SIZE - 20)), Unit(0, 2, b'x')))

    def test_flash_preservation_and_output_input_collision(self):
        flash = b'\xa5' * (FLASH_SIZE - GDFS_SIZE) + b'\xff' * GDFS_SIZE
        combined = combine_flash(flash, (Unit(2, 3, b'test'),))
        self.assertEqual(combined[:FLASH_SIZE - GDFS_SIZE], flash[:FLASH_SIZE - GDFS_SIZE])
        with self.assertRaises(ValueError): combine_flash(flash[:-1], ())
        with self.assertRaisesRegex(ValueError, 'contains data'): combine_flash(combined, ())
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.bin'; source.write_bytes(flash)
            units = Path(directory) / 'backup.bin'; units.write_bytes(backup(Unit(2, 3, b'test')))
            for target in [source, units]:
                with self.assertRaisesRegex(ValueError, 'differ'): prepare(source, units, target)
            out = Path(directory) / 'flash-gdfs.bin'
            result = prepare(source, units, out)
            self.assertEqual(out.read_bytes(), combined)
            self.assertEqual(source.read_bytes(), flash)
            self.assertEqual(result['unit_count'], 1)
            self.assertTrue(out.with_suffix('.manifest.json').exists())

    @unittest.skipUnless((ROOT / 'firmware/W800_GDFS.bin').exists(), 'Original backup required')
    def test_all_982_original_unit_payloads_survive_encoding(self):
        records = parse_backup((ROOT / 'firmware/W800_GDFS.bin').read_bytes())
        self.assertEqual(len(records), 982)
        self.assertEqual(Counter(u.bank for u in records),
                         {0: 249, 1: 422, 2: 262, 3: 16, 4: 14, 5: 13, 6: 6})
        region = encode_banks(records)
        # Decode the on-flash index independently, as the guest does.
        observed = {}
        for bank in range(BANK_COUNT):
            block = region[bank * BLOCK_SIZE:(bank + 1) * BLOCK_SIZE]
            descriptor = BLOCK_SIZE - 16
            while struct.unpack_from('<H', block, descriptor)[0] != 0xffff:
                state, number, offset, size, reserved = struct.unpack_from('<HHIII', block, descriptor)
                self.assertEqual(state, 0x3ff)
                self.assertEqual(reserved, 0xffffffff)
                self.assertEqual(offset % 4, 0)
                self.assertGreaterEqual(offset, 4)
                self.assertLessEqual(offset + size, descriptor)
                observed[bank, number] = block[offset:offset + size]
                descriptor -= 16
        self.assertEqual(observed, {(u.bank, u.number): u.data for u in records})
        flash_path = ROOT / 'firmware/prepared/flash.bin'
        if flash_path.exists():
            original = flash_path.read_bytes()
            combined = combine_flash(original, records)
            self.assertEqual(combined[:GDFS_BASE - FLASH_BASE], original[:GDFS_BASE - FLASH_BASE])

    @unittest.skipUnless((ROOT / 'build/qemu-system-arm').exists() and
                         (ROOT / 'firmware/prepared/flash-gdfs.bin').exists(),
                         'QEMU build and prepared GDFS image required')
    def test_original_guest_reads_complete_backup_unit(self):
        from w800.tools.trace_gdfs import capture
        result = capture()
        self.assertEqual((result['bank'], result['unit'], result['length']), (0, 29, 84))
        self.assertEqual(result['result'], 0)
        self.assertEqual(result['guest_sha256'], result['backup_sha256'])
