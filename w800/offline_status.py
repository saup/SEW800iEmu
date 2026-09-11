"""Unavailable status endpoint for the explicitly hosted, offline UI session.

Only the read-only SL queries 5a6c, 59a7 and 5b9b are routed here. The original message allocator,
sender lookup, send and free functions retain queue and ownership semantics.
The PH task, radio activation/deactivation, ETX register and validation paths
are untouched. This is a host service adapter, not normal handset boot.
"""
from collections import deque
import struct

from .offline_radio import OfflineRadio


PLMN_STATUS_SEND = 0x4496f068
PLMN_STATUS_REQUEST = 0x5b9b
PLMN_STATUS_RESPONSE = 0x5bbe
STATUS_SEND = 0x44964f34
AUDIO_STATUS_SEND = 0x44948d96
AUDIO_STATUS_REQUEST = 0x59a7
AUDIO_STATUS_RESPONSE = 0x59f0
AUDIO_STATUS_REASON = 0x2e  # Captured original inactive SL reply, not a codec capability.
STATUS_REQUEST = 0x5a6c
STATUS_RESPONSE = 0x5a85
STATUS_UNAVAILABLE = 7  # Original SL inactive response; a nonzero API failure.
REASON_INACTIVE = 1     # 44a11274..76 supplies this reason for SL state zero.


class OfflineStatus:
    """Service a bounded set of original status messages between UI windows.

    Integration calls ``route(regs)`` at STATUS_SEND, then ``poll()`` after
    each completed scheduler window. No guest call may be nested within route.
    A failed ownership or layout check requires disposal of the current VM.
    """
    def __init__(self, session):
        self.session = session
        if session.d.read(STATUS_SEND, 4) != bytes.fromhex('dff030fc'):
            raise RuntimeError('Unsupported original SL status send instruction')
        if session.word(0x44964f58) != STATUS_REQUEST:
            raise RuntimeError('Unsupported original SL status request')
        if (session.d.read(AUDIO_STATUS_SEND, 4) != bytes.fromhex('fbf0fffc') or
                session.word(0x44948dbc) != AUDIO_STATUS_REQUEST):
            raise RuntimeError('Unsupported original SL audio-state request')
        if (session.d.read(PLMN_STATUS_SEND, 4) != bytes.fromhex('d5f096fb') or
                session.word(0x4496f08c) != PLMN_STATUS_REQUEST):
            raise RuntimeError('Unsupported original PLMN status request')
        self.endpoint_pid = session.invoke(0x44a42e24)
        if not 0x10000 <= self.endpoint_pid <= 0x1ffff:
            raise RuntimeError('Offline status endpoint has no native task')
        self.filter = session.files.stage(16200, struct.pack('<4I', 3, STATUS_REQUEST, AUDIO_STATUS_REQUEST, PLMN_STATUS_REQUEST))
        self.slot = session.files.stage(16216, b'\0' * 4)
        self.radio = OfflineRadio()
        self.radio.start()
        self.pending = deque()
        self.responses = deque(maxlen=32)
        self.routed = self.completed = 0
        self.polling = False
        session.provenance['offline_status'] = self.snapshot()

    def snapshot(self):
        return {
            'provider': 'hosted offline unavailable-status endpoint',
            'endpoint_pid': hex(self.endpoint_pid),
            'request': hex(STATUS_REQUEST), 'response': hex(STATUS_RESPONSE),
            'status': STATUS_UNAVAILABLE,
            'audio_state_query': {'request': hex(AUDIO_STATUS_REQUEST),
                'response': hex(AUDIO_STATUS_RESPONSE), 'status': STATUS_UNAVAILABLE,
                'reason': AUDIO_STATUS_REASON, 'records_valid': False},
            'plmn_query': {'request': hex(PLMN_STATUS_REQUEST),
                'response': hex(PLMN_STATUS_RESPONSE), 'status': STATUS_UNAVAILABLE,
                'reason': 6, 'records_valid': False},
            'activation_adapter_connected': False,
            'deactivation_adapter_connected': False,
            'radio_powered': self.radio.status.radio_powered,
            'routed': self.routed, 'completed': self.completed,
            'pending': len(self.pending), 'responses': list(self.responses),
        }

    def _report(self):
        self.session.provenance['offline_status'] = self.snapshot()

    def route(self, regs):
        if regs[15] not in (STATUS_SEND, AUDIO_STATUS_SEND, PLMN_STATUS_SEND):
            return False
        session = self.session
        if regs[1] != session.word(0x4c04c67c):
            raise RuntimeError('Status request was not addressed to original SL')
        if not 0x4c000000 <= regs[0] <= 0x4c7ffffc:
            raise RuntimeError('Status request ownership pointer is outside RAM')
        pointer = session.word(regs[0])
        if not 0x4c000000 <= pointer <= 0x4c7ffff8:
            raise RuntimeError('Status request is outside RAM')
        request = session.d.read(pointer, 8)
        signal, correlation = struct.unpack('<2I', request)
        expected_signal = {STATUS_SEND: STATUS_REQUEST, AUDIO_STATUS_SEND: AUDIO_STATUS_REQUEST,
                           PLMN_STATUS_SEND: PLMN_STATUS_REQUEST}[regs[15]]
        if signal != expected_signal:
            raise RuntimeError('Offline status endpoint refuses unrelated SL traffic')
        if len(self.pending) >= 8:
            raise RuntimeError('Offline status request queue exceeded its bound')
        tcb = session.word(0x4c041c68)
        sender = session.word(tcb + 0x38)
        if sender == self.endpoint_pid:
            raise RuntimeError('Offline endpoint cannot query itself synchronously')
        # Change only this message's destination. The original send executes
        # next and transfers ownership; its request payload is unchanged.
        packet = bytearray(bytes.fromhex(session.d.packet('g')))
        struct.pack_into('<I', packet, 4, self.endpoint_pid)
        if session.d.packet('G' + packet.hex()) != 'OK':
            raise RuntimeError('Unable to route offline status request')
        self.pending.append((pointer, sender, correlation, signal))
        self.routed += 1
        self._report()
        return True

    def _store(self, address, data):
        if self.session.d.packet(f'M{address:x},{len(data):x}:' + data.hex()) != 'OK':
            raise RuntimeError('Unable to stage offline service message')

    def poll(self):
        if self.polling or not self.pending:
            return
        session = self.session
        if not session.ready:
            raise RuntimeError('Offline service requires a restored startup caller')
        self.polling = True
        try:
            # The exact receive filter cannot consume the scheduler sentinel
            # or any other native task traffic. One millisecond is bounded.
            for _ in range(len(self.pending)):
                pointer = session.invoke(0x44a47ae8, 1, self.filter)
                expected, sender, correlation, signal = self.pending[0]
                if pointer != expected:
                    raise RuntimeError('Offline status queue ownership did not match')
                if session.d.read(pointer, 8) != struct.pack('<2I', signal, correlation):
                    raise RuntimeError('Offline status request changed while queued')
                self._store(self.slot, struct.pack('<I', pointer))
                actual_sender = session.invoke(0x44a44790, self.slot)
                if actual_sender != sender:
                    raise RuntimeError('Offline status message sender did not match')
                session.invoke(0x44a47af8, self.slot)
                if session.word(self.slot):
                    raise RuntimeError('Original status request was not released')
                audio_state = signal == AUDIO_STATUS_REQUEST
                plmn_state = signal == PLMN_STATUS_REQUEST
                response_signal = (PLMN_STATUS_RESPONSE if plmn_state else
                                   AUDIO_STATUS_RESPONSE if audio_state else STATUS_RESPONSE)
                response_bytes = 24 if audio_state or plmn_state else 12
                response = session.invoke(0x44a46128, response_bytes, response_signal)
                if not 0x4c000000 <= response <= 0x4c800000 - response_bytes:
                    raise RuntimeError('Original allocator did not supply a status response')
                # The original decoder returns byte 8. MMI's wrapper clears
                # its outputs and returns 80100000 for any nonzero result.
                if audio_state or plmn_state:
                    # Original inactive SL59f0 supplies7/reason46. Its two
                    # six-byte records are invalid on failure; do not expose
                    # allocator leftovers or fabricate microphone capability.
                    payload = bytearray(24)
                    struct.pack_into('<2I', payload, 0, response_signal, correlation)
                    payload[8] = STATUS_UNAVAILABLE
                    payload[21] = 6 if plmn_state else AUDIO_STATUS_REASON
                    # Native PLMN decoder 4496f098 copies 8 bytes at +10,
                    # three status bytes at +18, and error reason at +21.
                    # Reason 6 maps to the generic unavailable HRESULT;
                    # no operator identity or registration is asserted.
                    payload = bytes(payload)
                else:
                    payload = struct.pack('<2I4B', response_signal, correlation,
                                          STATUS_UNAVAILABLE, 0, 0, REASON_INACTIVE)
                self._store(response, payload)
                self._store(self.slot, struct.pack('<I', response))
                session.invoke(0x44a44798, self.slot, sender)
                if session.word(self.slot):
                    raise RuntimeError('Original status response ownership did not transfer')
                self.pending.popleft()
                self.completed += 1
                self.responses.append({'sender': hex(sender), 'correlation': correlation,
                                       'bytes': payload.hex()})
                self._report()
        except Exception:
            # An incomplete ownership transfer cannot be resumed safely by
            # another UI call; the disposable VM must be discarded.
            session.ready = False
            raise
        finally:
            self.polling = False
