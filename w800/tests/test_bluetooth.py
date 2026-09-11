"""Exercise the offline controller through its mapped UART registers."""
import struct
import unittest
from w800.backend import QemuBackend, ROOT
from w800.tools.debug import Debugger


@unittest.skipUnless((ROOT / 'build/qemu-system-arm').exists(), 'Local QEMU build required')
class BluetoothTests(unittest.TestCase):
    def setUp(self):
        self.q = QemuBackend(exploratory=True, debug=True)
        self.q.start()
        self.d = Debugger(self.q)

    def tearDown(self):
        self.d.close()
        self.q.close()

    def write16(self, offset, value):
        data = struct.pack('<H', value)
        self.assertEqual(self.d.packet(f'M{0xfc030000 + offset:x},2:' + data.hex()), 'OK')

    def read16(self, offset):
        return int.from_bytes(self.d.read(0xfc030000 + offset, 2), 'little')

    def command(self, data):
        for value in data:
            self.write16(2, value)

    def receive(self):
        return bytes(self.read16(0) for _ in range(self.read16(0x2e)))

    def test_reset_h4_framing_and_irq47(self):
        self.assertEqual(self.read16(0x0e) & 0x10, 0x10)
        self.assertEqual(self.read16(0x2c), 256)
        self.assertEqual(int.from_bytes(self.d.read(0xf9000014, 2), 'little') & 0x10, 0x10)
        self.write16(0x12, 0x41)
        self.command(bytes.fromhex('01030c'))
        self.assertEqual(self.read16(0x2e), 0)
        self.command(b'\x00')
        self.assertEqual(self.read16(0x14), 0x41)
        self.assertEqual(int.from_bytes(self.d.read(0xfb020200, 4), 'little') & (1 << 19), 1 << 19)
        self.assertEqual(self.receive().hex(), '040e0401030c00')
        self.assertEqual(self.read16(0x14), 0)

    def test_vendor_command_is_explicitly_unsupported(self):
        self.command(bytes.fromhex('010ffc00'))
        self.assertEqual(self.receive().hex(), '040e04010ffc01')
        self.command(bytes.fromhex('01091000'))
        self.assertEqual(self.receive().hex(), '040e0a01091000010000000002')

    def test_transmit_interrupt_mask(self):
        self.write16(0x12, 2)
        self.assertEqual(self.read16(0x14), 2)
        self.write16(0x12, 0)
        self.assertEqual(self.read16(0x14), 0)

    def test_local_name_persists_and_invalid_write_is_rejected(self):
        name = b'Offline W800'.ljust(248, b'\x00')
        self.command(bytes.fromhex('01130cf8') + name)
        self.assertEqual(self.receive().hex(), '040e0401130c00')
        self.command(bytes.fromhex('01130c01ff'))
        self.assertEqual(self.receive().hex(), '040e0401130c12')
        self.command(bytes.fromhex('01140c00'))
        self.assertEqual(self.receive(), bytes.fromhex('040efc01140c00') + name)
        self.command(bytes.fromhex('01030c00'))
        self.receive()
        self.command(bytes.fromhex('01140c00'))
        self.assertEqual(self.receive(), bytes.fromhex('040efc01140c00') + bytes(248))

    def test_controller_timeout_write_and_readback(self):
        self.command(bytes.fromhex('01160c02401f'))
        self.assertEqual(self.receive().hex(), '040e0401160c00')
        self.command(bytes.fromhex('01150c00'))
        self.assertEqual(self.receive().hex(), '040e0601150c00401f')
        self.command(bytes.fromhex('010a0c0100'))
        self.assertEqual(self.receive().hex(), '040e04010a0c00')
        self.command(bytes.fromhex('01090c00'))
        self.assertEqual(self.receive().hex(), '040e0501090c0000')
