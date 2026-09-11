"""Ownership and guard checks for the original negative-response dispatcher."""
import struct
import unittest

from w800.radio_rejection import OriginalRadioRejection, REQUEST_SEND, HANDLER


class NativeQueueFixture:
    """Small native-API double; it does not simulate radio validation."""
    def __init__(self):
        self.memory = bytearray(0x800000)
        self.d = self.files = self
        self.ready = True
        self.provenance = {}
        self.calls = []
        self.register_writes = []
        self.slot = 0x4c210000
        self.request = 0x4c200000
        self.regs = [0] * 17
        self.regs[0], self.regs[1], self.regs[15] = self.slot, 0x10035, REQUEST_SEND
        self.put(0x4c38eed4, bytes.fromhex('000000010101'))
        for address, value in ((0x4c04a8fc, 0x10035), (0x4c041c68, 0x4c190000),
                               (0x4c190038, 0x100f2), (self.slot, self.request)):
            self.put(address, struct.pack('<I', value))
        self.put(self.request, struct.pack('<2I', 0x63e4, 0x12345678))

    def put(self, address, data):
        offset = address - 0x4c000000
        self.memory[offset:offset + len(data)] = data

    def read(self, address, size):
        if address == REQUEST_SEND:
            return bytes.fromhex('d6f055f9')
        return bytes(self.memory[address - 0x4c000000:address - 0x4c000000 + size])

    def word(self, address):
        literals = {0x4496e520: 0x63e4, 0x4498dc08: 0x4c38eed4, 0x4498dbf8: 0x4c38eed4}
        return literals[address] if address in literals else struct.unpack('<I', self.read(address, 4))[0]

    def stage(self, offset, data):
        address = 0x4c100000 + offset
        self.put(address, data)
        return address

    def packet(self, command):
        if command == 'g':
            return struct.pack('<17I', *self.regs).hex()
        if command.startswith('G'):
            self.regs = list(struct.unpack('<17I', bytes.fromhex(command[1:])))
            self.register_writes.append(self.regs[:])
        elif command.startswith('M'):
            location, payload = command[1:].split(':')
            address, size = (int(v, 16) for v in location.split(','))
            data = bytes.fromhex(payload)
            assert len(data) == size
            self.put(address, data)
        else:
            raise AssertionError(command)
        return 'OK'

    def invoke(self, function, *args):
        self.calls.append((function, args))
        if function == 0x44a42e24:
            return 0x100da
        if function == 0x44a47ae8:
            return self.request
        if function == 0x44a44790:
            return 0x100f2
        if function == HANDLER:
            # Firmware execution needs the separate QEMU interaction probe.
            return 0
        if function == 0x44a47af8:
            assert self.word(args[0]) == self.request
            self.put(args[0], b'\0' * 4)
            return 0
        raise AssertionError(hex(function))


class RadioRejectionOwnershipTests(unittest.TestCase):
    def test_original_handler_precedes_single_request_free(self):
        fixture = NativeQueueFixture()
        adapter = OriginalRadioRejection(fixture)
        original_regs = fixture.regs[:]
        payload = fixture.read(fixture.request, 8)
        context = fixture.read(adapter.context, 6)
        self.assertTrue(adapter.route(fixture.regs))
        changed = fixture.register_writes[-1]
        self.assertEqual(changed[1], 0x100da)
        self.assertEqual(changed[:1] + changed[2:], original_regs[:1] + original_regs[2:])
        adapter.poll()
        adapter.poll()
        handlers = [c for c in fixture.calls if c[0] == HANDLER]
        frees = [c for c in fixture.calls if c[0] == 0x44a47af8]
        self.assertEqual(handlers, [(HANDLER, (adapter.context, fixture.request))])
        self.assertEqual(len(frees), 1)
        self.assertLess(fixture.calls.index(handlers[0]), fixture.calls.index(frees[0]))
        self.assertEqual(fixture.read(fixture.request, 8), payload)
        self.assertEqual(fixture.read(adapter.context, 6), context)
        self.assertEqual(adapter.rows[0]['correlation'], 0x12345678)
        self.assertEqual(adapter.completed, 1)
        self.assertTrue(fixture.ready)

    def test_other_context_states_keep_original_destination(self):
        for guard in (b'\x00\x00', b'\x00\x01', b'\x01\x00', b'\x01\x02'):
            with self.subTest(guard=guard.hex()):
                fixture = NativeQueueFixture()
                adapter = OriginalRadioRejection(fixture)
                fixture.put(adapter.context + 3, guard)
                self.assertFalse(adapter.route(fixture.regs))
                adapter.poll()
                self.assertFalse(fixture.register_writes)
                self.assertFalse(adapter.pending)
                self.assertFalse(any(c[0] == HANDLER for c in fixture.calls))

    def test_changed_guard_invalidates_without_calling_handler(self):
        fixture = NativeQueueFixture()
        adapter = OriginalRadioRejection(fixture)
        adapter.route(fixture.regs)
        fixture.put(adapter.context + 4, b'\0')
        with self.assertRaisesRegex(RuntimeError, 'rejection condition changed'):
            adapter.poll()
        self.assertFalse(fixture.ready)
        self.assertFalse(any(c[0] in (HANDLER, 0x44a47af8) for c in fixture.calls))


if __name__ == '__main__':
    unittest.main()
