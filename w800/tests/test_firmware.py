import struct
import unittest
from w800.firmware import parse_babe, FLASH_BASE


def fixture(blocks=None, version=3):
    blocks = blocks or [(FLASH_BASE + 0x20000, b'boot')]
    data = bytearray(0x380 + len(blocks) * (20 if version == 4 else 1))
    data[:4] = bytes([0xba, 0xbe, 0, version])
    struct.pack_into('<I', data, 8, 0x00100000)
    struct.pack_into('<I', data, 0x10, 49)
    struct.pack_into('<I', data, 0x2e8, len(blocks))
    for addr, content in blocks:
        data.extend(struct.pack('<II', addr, len(content)))
        data.extend(content)
    return bytes(data)

class BabeTests(unittest.TestCase):
    def test_versions_and_addresses(self):
        for version in (3, 4):
            image = parse_babe(fixture(version=version))
            self.assertEqual(image.segments[0].address, 0x44020000)
            self.assertEqual(image.segments[0].data, b'boot')
            self.assertEqual(image.cid, 49)
    def test_truncation_at_every_boundary(self):
        data = fixture()
        for end in (0, 1, 0x37f, 0x380, 0x381, len(data)-1):
            with self.assertRaises(ValueError): parse_babe(data[:end])
    def test_outside_flash_and_wrap(self):
        for addr in (0, 0x43ffffff, 0x45ffffff, 0xfffffffe):
            with self.assertRaises(ValueError): parse_babe(fixture([(addr,b'abcd')]))
    def test_overlap_and_order(self):
        with self.assertRaises(ValueError):
            parse_babe(fixture([(FLASH_BASE,b'abcd'),(FLASH_BASE+2,b'abcd')]))
        image = parse_babe(fixture([(FLASH_BASE+4,b'ef'),(FLASH_BASE,b'abcd')]))
        self.assertEqual(len(image.segments), 2)
    def test_foreign_platform(self):
        data = bytearray(fixture()); struct.pack_into('<I',data,8,0x10000000)
        with self.assertRaises(ValueError): parse_babe(data)
    def test_huge_count_and_trailer(self):
        data = bytearray(fixture()); struct.pack_into('<I',data,0x2e8,0xffffffff)
        with self.assertRaises(ValueError): parse_babe(data)
        with self.assertRaises(ValueError): parse_babe(fixture()+b'junk')
    def test_zero_length(self):
        with self.assertRaises(ValueError): parse_babe(fixture([(FLASH_BASE,b'')]))

if __name__ == '__main__': unittest.main()
