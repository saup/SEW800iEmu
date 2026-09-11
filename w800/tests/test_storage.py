"""Exercise real QEMU device transactions without patching guest instructions."""
import hashlib
import socket
import unittest
from w800.backend import QemuBackend, ROOT


class Bus:
    def __init__(self, backend):
        self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        self.sock.settimeout(2)
        self.sock.connect(str(backend.directory/'qtest.sock'))
        self.file=self.sock.makefile('rb')
    def command(self, text):
        self.sock.sendall(text.encode()+b'\n')
        response=self.file.readline().decode().strip()
        if not response.startswith('OK'):
            raise RuntimeError(response)
        return response[2:].strip()
    def put(self, addr, value, width='b'):
        self.command(f'write{width} {addr:#x} {value:#x}')
    def get(self, addr, width='b'):
        return int(self.command(f'read{width} {addr:#x}'),0)
    def close(self):self.file.close();self.sock.close()


@unittest.skipUnless((ROOT/'build/qemu-system-arm').exists(),'Local QEMU build required')
class StorageTests(unittest.TestCase):
    def setUp(self):
        self.q=QemuBackend(bus_test=True);self.q.start();self.bus=Bus(self.q)
    def tearDown(self):self.bus.close();self.q.close()
    def nand_cmd(self, cmd):self.bus.put(0x50000010,cmd)
    def address(self,*values):
        for value in values:self.bus.put(0x50000008,value)
    def program(self,row,column,data,area=0):
        self.nand_cmd(area);self.nand_cmd(0x80)
        self.address(column,row&255,row>>8)
        for byte in data:self.bus.put(0x50000000,byte)
        self.nand_cmd(0x10);self.nand_cmd(0x70)
        self.assertEqual(self.bus.get(0x50000000),0xc0)
    def read_page(self,row,column,count,area=0):
        self.nand_cmd(area);self.address(column,row&255,row>>8)
        return bytes(self.bus.get(0x50000000) for _ in range(count))

    def test_nand_id_program_spare_and_erase_boundaries(self):
        self.nand_cmd(0x90);self.address(0)
        self.assertEqual([self.bus.get(0x50000000) for _ in range(2)],[0xec,0x75])
        self.program(0x123,4,b'\x5a\x7f')
        self.program(0x123,0,b'\x12\x34',area=0x50)
        self.program(0x100,4,b'\x77')
        self.assertEqual(self.read_page(0x123,4,2),b'\x5a\x7f')
        self.assertEqual(self.read_page(0x123,0,2,area=0x50),b'\x12\x34')
        self.program(0x123,4,b'\xaa\xff')
        self.assertEqual(self.read_page(0x123,4,2),b'\x0a\x7f')
        self.nand_cmd(0x60);self.address(0x20,1);self.nand_cmd(0xd0)
        self.assertEqual(self.read_page(0x123,4,2),b'\xff\xff')
        self.assertEqual(self.read_page(0x123,0,2,area=0x50),b'\xff\xff')
        self.assertEqual(self.read_page(0x100,4,1),b'\x77')

    def test_nor_identifier_geometry_and_read_while_programming(self):
        base=0x44000000;dest=0x45fe0000  # Erased GDFS spare block
        vector=self.bus.get(base+0x20000,'l')
        self.bus.put(base,0x90,'w')
        self.assertEqual(self.bus.get(base,'w'),0x89)
        self.assertEqual(self.bus.get(base+2,'w'),0x880d)
        self.assertEqual(bytes(self.bus.get(base+0x20+i) for i in range(6)),b'Q\0R\0Y\0')
        self.assertEqual(self.bus.get(base+0x54,'w'),6)
        self.bus.put(base,0xff,'w')
        self.bus.put(dest,0x40,'w');self.bus.put(dest,0x1234,'w')
        self.assertEqual(self.bus.get(dest,'w')&0x80,0x80)
        self.assertEqual(self.bus.get(base+0x20000,'l'),vector)
        self.bus.put(dest,0xff,'w')
        self.assertEqual(self.bus.get(dest,'w'),0x1234)
        self.assertEqual(self.bus.get(dest+2,'w'),0xffff)

    def test_nand_internal_copyback_preserves_data_and_spare(self):
        self.program(0x120,2,b'\x12\x34\x56')
        self.program(0x120,6,b'\x78\x9a',area=0x50)
        self.nand_cmd(0);self.address(0,0x20,1)
        self.nand_cmd(0x70)
        self.assertEqual(self.bus.get(0x50000000),0xc0)
        self.nand_cmd(0x8a);self.address(0,0x40,1)
        self.nand_cmd(0x70)
        self.assertEqual(self.bus.get(0x50000000),0xc0)
        self.assertEqual(self.read_page(0x140,2,3),b'\x12\x34\x56')
        self.assertEqual(self.read_page(0x140,6,2,area=0x50),b'\x78\x9a')
        self.assertEqual(self.read_page(0x120,2,3),b'\x12\x34\x56')

    def test_nor_mixed_erase_regions_match_physical_bootlog(self):
        base=0x44000000
        self.bus.put(base,0x98,'w')
        def cfi(offset):return self.bus.get(base+2*offset,'w')
        self.assertEqual(cfi(0x2c),2)
        regions=[]
        for offset in (0x2d,0x31):
            regions.append((1+cfi(offset)+(cfi(offset+1)<<8),
                            256*(cfi(offset+2)+(cfi(offset+3)<<8))))
        self.assertEqual(regions,[(255,0x20000),(4,0x8000)])
        self.assertEqual(sum(count*size for count,size in regions),0x2000000)
        ext=cfi(0x15)+(cfi(0x16)<<8)
        self.assertEqual(bytes(cfi(ext+i) for i in range(3)),b'PRI')
        self.bus.put(base,0xff,'w')

    def test_nor_erase_preserves_neighbors_in_both_region_sizes(self):
        def program(address):
            self.bus.put(address,0x40,'w');self.bus.put(address,0,'w')
            self.bus.put(address,0xff,'w')
            self.assertEqual(self.bus.get(address,'w'),0)
        def erase(address):
            self.bus.put(address,0x20,'w');self.bus.put(address,0xd0,'w')
            self.bus.put(address,0xff,'w')
        # The erase address lies in the second half of a 128 KiB block.
        for address in (0x45ebfffe,0x45ec0000,0x45edfffe,0x45ee0000):program(address)
        erase(0x45ed8000)
        for address in (0x45ec0000,0x45edfffe):self.assertEqual(self.bus.get(address,'w'),0xffff)
        for address in (0x45ebfffe,0x45ee0000):self.assertEqual(self.bus.get(address,'w'),0)
        # Each of the four parameter blocks must erase independently.
        starts=[0x45fe0000+i*0x8000 for i in range(4)]
        for selected in starts:
            for address in starts:
                program(address);program(address+0x7ffe)
            erase(selected+0x4000)
            for address in starts:
                expected=0xffff if address==selected else 0
                self.assertEqual(self.bus.get(address,'w'),expected)
                self.assertEqual(self.bus.get(address+0x7ffe,'w'),expected)

    def test_nor_buffered_single_word_count_zero(self):
        # Intel L18 table8 specifies N-1. Zero is a valid one-word request.
        dest=0x45fe0040
        self.bus.put(dest,0xe8,'w')
        self.assertEqual(self.bus.get(dest,'w') & 0x80,0x80)
        self.bus.put(dest,0,'w')
        self.bus.put(dest,0x1234,'w')
        self.bus.put(dest,0xd0,'w')
        self.assertEqual(self.bus.get(dest,'w') & 0x90,0x80)
        self.bus.put(dest,0xff,'w')
        self.assertEqual(self.bus.get(dest,'w'),0x1234)
        self.assertEqual(self.bus.get(dest+2,'w'),0xffff)

    def test_reset_preserves_nonvolatile_flash_contents(self):
        original=hashlib.sha256(self.q.flash_path.read_bytes()).digest()
        dest=0x45fe0060
        self.bus.put(dest,0x40,'w');self.bus.put(dest,0x1234,'w')
        self.bus.put(dest,0xff,'w')
        self.program(0x123,4,b'\x5a\x7f')
        self.assertEqual(self.bus.get(dest,'w'),0x1234)
        self.bus.put(dest,0x90,'w')
        self.assertNotEqual(self.bus.get(dest,'w'),0x1234)
        self.q.command('system_reset')
        self.assertEqual(self.bus.get(dest,'w'),0x1234)
        self.assertEqual(self.read_page(0x123,4,2),b'\x5a\x7f')
        self.assertEqual(hashlib.sha256(self.q.flash_path.read_bytes()).digest(),original)

    def test_nor_original_split_buffer_write_keeps_single_word_tail(self):
        # Original MAIN writes eight bytes at45f42eba. Its driver splits the
        # transfer at64 bytes, leaving one buffered word at45f42ec0.
        # Replay that real command pattern in an erased GDFS spare block.
        dest=0x45fe003a
        words=[0x0100,0,0,0]
        for address,part in [(dest,words[:3]),(dest+6,words[3:])]:
            self.bus.put(address,0xe8,'w')
            self.bus.put(address,len(part)-1,'w')
            for index,value in enumerate(part):
                self.bus.put(address+2*index,value,'w')
            self.bus.put(address & ~63,0xd0,'w')
            self.assertEqual(self.bus.get(address,'w') & 0x90,0x80)
            self.bus.put(address & ~63,0xff,'w')
        self.assertEqual([self.bus.get(dest+2*i,'w') for i in range(4)],words)
        self.assertEqual(self.bus.get(dest-2,'w'),0xffff)
        self.assertEqual(self.bus.get(dest+8,'w'),0xffff)

    def test_i2c_register_transfer_status_and_irq(self):
        # qtest executes no CPU instructions; cont enables virtual timers.
        self.q.command('cont')
        base=0xf9040000
        def control(value, expected):
            self.bus.put(base+2,value,'w')
            self.bus.command('clock_step 20000')
            self.assertEqual(self.bus.get(base+6,'w'),expected)
        self.bus.put(0xfb020104,0xbfffffff,'l')
        self.bus.put(0xfb020304,0xfffffbff,'l')
        control(0xa4,8)
        self.assertEqual(self.bus.get(0xfb020300,'l'),1<<10)
        self.assertEqual(self.bus.get(0xfb020100,'l'),1<<30)
        self.bus.put(base,0x94,'w');control(0x80,0x18)
        self.bus.put(base,0x60,'w');control(0x80,0x28)
        self.bus.put(base,0x5a,'w');control(0x80,0x28)
        control(0x90,0xf8)
        self.assertEqual(self.bus.get(0xfb020300,'l'),0)
        control(0xa4,8)
        self.bus.put(base,0x94,'w');control(0x80,0x18)
        self.bus.put(base,0x60,'w');control(0x80,0x28)
        control(0xa4,0x10)
        self.bus.put(base,0x95,'w');control(0x80,0x40)
        control(0x80,0x58)
        self.assertEqual(self.bus.get(base,'w'),0x5a)
        control(0x90,0xf8)
        control(0xa4,8)
        self.bus.put(base,0x96,'w');control(0x80,0x20)  # Unknown slave NACK
        control(0x90,0xf8)
