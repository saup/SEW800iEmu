"""Native error feedback for a browser request rejected by offline startup.

The R1L002 WAP task consumes URL_LOAD while its startup state is 2, before
creating any UI. The hosted session cannot complete SIM authentication. Observe
that existing rejection and request its original WAP error dialog on MMI after
the scheduler window. The task still owns and frees its request, and its state,
SIM notifications, radio services and readiness decision are not changed.
"""
from collections import deque
import struct


URL_LOAD_GUARD = 0x44b7537e
URL_LOAD_REQUEST = 0x2592
WAP_STATE = 0x4c391261
ERROR_DISPLAY = 0x44b950fc
DEFAULT_ERROR_TEXT = 0x1410


class BrowserUnavailable:
    """Observe the original guard, then show native feedback between windows."""

    def __init__(self, session):
        self.session = session
        # Original CMP state,4 / BCC return precedes the second ready guard.
        if session.d.read(URL_LOAD_GUARD, 4) != bytes.fromhex('042829d3'):
            raise RuntimeError('Unsupported original WAP readiness guard')
        if session.d.read(0x44b75374, 2) != bytes.fromhex('f1b5'):
            raise RuntimeError('Unsupported original WAP request stack layout')
        if session.d.read(ERROR_DISPLAY, 6) != bytes.fromhex('30b5041c0d1c'):
            raise RuntimeError('Unsupported original WAP error display')
        self.pending = deque()
        self.responses = deque(maxlen=32)
        self.observed = self.displayed = 0
        self.polling = False
        self._report()

    def _report(self):
        self.session.provenance['browser_unavailable'] = {
            'provider': 'native WAP error feedback for hosted offline UI',
            'observed': self.observed, 'displayed': self.displayed,
            'pending': len(self.pending), 'responses': list(self.responses),
            'readiness_changes': False, 'request_ownership_changes': False,
        }

    def observe(self, regs):
        """Read the rejected request; the caller executes its original guard."""
        if regs[15] != URL_LOAD_GUARD:
            return False
        state = self.session.d.read(WAP_STATE, 2)
        if regs[0] != 2 or state != b'\x02\x00':
            return False
        stack = regs[13]
        if not 0x4c000000 <= stack <= 0x4c7ffffc:
            raise RuntimeError('Original WAP stack lies outside RAM')
        # PUSH {r0,r4-r7,lr} retains the request argument at the stack top.
        pointer = struct.unpack('<I', self.session.d.read(stack, 4))[0]
        if not 0x4c000000 <= pointer <= 0x4c7ffff0:
            raise RuntimeError('Original WAP request lies outside RAM')
        request = self.session.d.read(pointer, 16)
        if struct.unpack_from('<I', request)[0] != URL_LOAD_REQUEST:
            raise RuntimeError('Offline WAP feedback refuses unrelated traffic')
        if len(self.pending) >= 8:
            raise RuntimeError('Offline WAP feedback queue exceeded its bound')
        # Retain only an observation. Do not keep or free a guest pointer after
        # the native dispatcher returns; the native receive loop owns it.
        self.pending.append({'request': request.hex(), 'state': state.hex()})
        self.observed += 1
        self._report()
        return True

    def poll(self):
        session = self.session
        if (self.polling or not self.pending or not session.ready or
                session.pending_menu is not None or session.q.pressed_keys):
            return
        self.polling = True
        try:
            # One original error page per observed request. Nested advance()
            # within call_on_mmi cannot display the same request a second time.
            row = self.pending[0]
            session.call_on_mmi(ERROR_DISPLAY, DEFAULT_ERROR_TEXT, 0)
            self.pending.popleft()
            self.displayed += 1
            self.responses.append({**row, 'text_id': hex(DEFAULT_ERROR_TEXT)})
            self._report()
        finally:
            self.polling = False
