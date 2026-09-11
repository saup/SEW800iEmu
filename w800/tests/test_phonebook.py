"""Check the narrow unavailable-response adapter's ownership contract."""
import struct
import unittest
from w800 import guest_ui
from w800.phonebook_services import UnavailableReplyAdapter, UNAVAILABLE_REPLY, SEND_REPLY


class DebugMemory:
    def __init__(self):
        regs = [0x12340000 + i for i in range(17)]
        regs[4], regs[13], regs[15] = 0x180f2, 0x4c220000, UNAVAILABLE_REPLY
        self.packet_bytes = struct.pack('<17I', *regs)
        self.memory = {}
        self.writes = []
        self.steps = []
        self.put(0x4c220008, struct.pack('<I', 0x4c200008))
        self.put(0x4c200000, struct.pack('<I', 0x180f2))
        self.put(0x4c0bae24, struct.pack('<I', 0x180f2))
        self.reply = bytes.fromhex('131b000061001a0000075f50')
        self.put(0x4c200008, self.reply)

    def put(self, address, data):
        self.memory.update({address + i: value for i, value in enumerate(data)})

    def read(self, address, size):
        return bytes(self.memory[address + i] for i in range(size))

    def registers(self):
        return struct.unpack('<17I', self.packet_bytes)[:16]

    def packet(self, command):
        if command == 'g':
            return self.packet_bytes.hex()
        if command.startswith('G'):
            self.packet_bytes = bytes.fromhex(command[1:])
            self.writes.append(self.packet_bytes)
            return 'OK'
        raise AssertionError('Adapter attempted an unexpected debugger command')

    def breakpoint(self, address, **kwargs):
        pass

    def step_over_breakpoint(self, address, thumb=False):
        self.steps.append((address, thumb))


class Session:
    def __init__(self):
        self.d = DebugMemory()
        self.provenance = {}

    def word(self, address):
        return struct.unpack('<I', self.d.read(address, 4))[0]


class UnavailableReplyTests(unittest.TestCase):
    def setUp(self):
        self.previous_checkpoint = guest_ui.CHECKPOINTS.get(UNAVAILABLE_REPLY)
        self.session = Session()
        self.adapter = UnavailableReplyAdapter(self.session)

    def tearDown(self):
        if self.previous_checkpoint is None:
            guest_ui.CHECKPOINTS.pop(UNAVAILABLE_REPLY, None)
        else:
            guest_ui.CHECKPOINTS[UNAVAILABLE_REPLY] = self.previous_checkpoint

    def test_original_sender_consumes_same_error_reply_and_registers_restore(self):
        d = self.session.d
        saved = d.packet_bytes
        self.adapter.step_over_breakpoint(UNAVAILABLE_REPLY, True)
        regs = d.registers()
        self.assertEqual((regs[0], regs[1], regs[14], regs[15]),
                         (0x4c220008, 0x180f2, UNAVAILABLE_REPLY | 1, SEND_REPLY))
        self.assertEqual(d.read(0x4c200008, 12), d.reply)
        # Model only the documented pointer consumption by the original send.
        d.put(0x4c220008, bytes(4))
        self.adapter.step_over_breakpoint(UNAVAILABLE_REPLY, True)
        self.assertEqual(d.packet_bytes, saved)
        self.assertEqual(d.steps, [(UNAVAILABLE_REPLY, True)])
        self.assertTrue(self.adapter.rows[0]['sent'])

    def test_success_or_unrelated_error_cannot_be_fabricated(self):
        self.session.d.put(0x4c20000e, b'\0')
        with self.assertRaisesRegex(RuntimeError, 'Unrecognized'):
            self.adapter.step_over_breakpoint(UNAVAILABLE_REPLY, True)
        self.assertEqual(self.session.d.writes, [])

    def test_different_sender_cannot_receive_reply(self):
        self.session.d.put(0x4c200000, struct.pack('<I', 0x180ff))
        with self.assertRaisesRegex(RuntimeError, 'Unrecognized'):
            self.adapter.step_over_breakpoint(UNAVAILABLE_REPLY, True)
        self.assertEqual(self.session.d.writes, [])

    def test_unconsumed_pointer_is_not_silently_reused(self):
        self.adapter.step_over_breakpoint(UNAVAILABLE_REPLY, True)
        with self.assertRaisesRegex(RuntimeError, 'did not consume'):
            self.adapter.step_over_breakpoint(UNAVAILABLE_REPLY, True)
        self.assertFalse(self.adapter.rows[0]['sent'])


if __name__ == '__main__':
    unittest.main()
