"""Dispatch an already-rejected radio request through its original handler.

The hosted UI may query radio activation while SystemControl is parked in an
unrelated deactivation. Only the original, already-established rejection case
is served here. The original code reads its own context and constructs/sends
the negative response; no status, legality flag or ETX result is supplied.
"""
from collections import deque
import struct


REQUEST_SEND = 0x4496e4ea
REQUEST = 0x63e4
HANDLER = 0x4498de74


class OriginalRadioRejection:
    def __init__(self, session):
        self.session = session
        if (session.d.read(REQUEST_SEND, 4) != bytes.fromhex('d6f055f9') or
                session.word(0x4496e520) != REQUEST):
            raise RuntimeError('Unsupported original radio activation request')
        self.context = session.word(0x4498dc08)
        if self.context != session.word(0x4498dbf8) or self.context != 0x4c38eed4:
            raise RuntimeError('Unsupported original radio-control context')
        self.endpoint_pid = session.invoke(0x44a42e24)
        self.filter = session.files.stage(16232, struct.pack('<2I', 1, REQUEST))
        self.slot = session.files.stage(16240, b'\0' * 4)
        self.pending = deque()
        self.rows = deque(maxlen=32)
        self.polling = False
        self.completed = 0
        self._report()

    def rejection_established(self):
        # 4498de8a..dea4: available platform, but original COPS legality
        # result rejects activation. Do not dispatch any other branch here.
        return self.session.d.read(self.context + 3, 2) == b'\x01\x01'

    def _report(self):
        self.session.provenance['original_radio_rejections'] = {
            'handler': hex(HANDLER), 'context': hex(self.context),
            'completed': self.completed, 'pending': len(self.pending),
            'validation_result_changes': False, 'rows': list(self.rows),
        }

    def _store(self, address, data):
        if self.session.d.packet(f'M{address:x},{len(data):x}:' + data.hex()) != 'OK':
            raise RuntimeError('Unable to stage native radio request ownership')

    def route(self, regs):
        if regs[15] != REQUEST_SEND or not self.rejection_established():
            return False
        session = self.session
        if regs[1] != session.word(0x4c04a8fc):
            raise RuntimeError('Radio request was not addressed to SystemControl')
        if not 0x4c000000 <= regs[0] <= 0x4c7ffffc:
            raise RuntimeError('Radio request ownership pointer is outside RAM')
        pointer = session.word(regs[0])
        if not 0x4c000000 <= pointer <= 0x4c7ffff8:
            raise RuntimeError('Radio request is outside RAM')
        request, correlation = struct.unpack('<2I', session.d.read(pointer, 8))
        if request != REQUEST or len(self.pending) >= 8:
            raise RuntimeError('Unsupported or excessive radio request')
        sender = session.word(session.word(0x4c041c68) + 0x38)
        if sender == self.endpoint_pid:
            raise RuntimeError('Radio endpoint cannot synchronously query itself')
        packet = bytearray(bytes.fromhex(session.d.packet('g')))
        struct.pack_into('<I', packet, 4, self.endpoint_pid)
        if session.d.packet('G' + packet.hex()) != 'OK':
            raise RuntimeError('Unable to route original radio rejection')
        self.pending.append((pointer, sender, correlation))
        self._report()
        return True

    def poll(self):
        if self.polling or not self.pending:
            return
        session = self.session
        if not session.ready:
            raise RuntimeError('Original radio rejection requires a restored caller')
        self.polling = True
        try:
            for _ in range(len(self.pending)):
                pointer = session.invoke(0x44a47ae8, 1, self.filter)
                expected, sender, correlation = self.pending[0]
                if pointer != expected or session.d.read(pointer, 8) != struct.pack('<2I', REQUEST, correlation):
                    raise RuntimeError('Native radio request ownership did not match')
                self._store(self.slot, struct.pack('<I', pointer))
                if session.invoke(0x44a44790, self.slot) != sender:
                    raise RuntimeError('Native radio request sender did not match')
                if not self.rejection_established():
                    raise RuntimeError('Original radio rejection condition changed')
                context_before = session.d.read(self.context, 6)
                # This original handler allocates 63ed, copies the request's
                # correlation, reads the actual legality result and sends its
                # own error response to the request's original native sender.
                session.invoke(HANDLER, self.context, pointer)
                if session.d.read(self.context, 6) != context_before:
                    raise RuntimeError('Original radio rejection changed its context')
                # The original caller, not HANDLER, owns the request cleanup.
                session.invoke(0x44a47af8, self.slot)
                if session.word(self.slot):
                    raise RuntimeError('Original radio request was not released')
                self.pending.popleft()
                self.completed += 1
                self.rows.append({'sender': hex(sender), 'correlation': correlation,
                                  'original_context': context_before.hex()})
                self._report()
        except Exception:
            session.ready = False
            raise
        finally:
            self.polling = False
