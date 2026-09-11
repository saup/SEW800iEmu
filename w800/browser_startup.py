"""Run the original WAP initializer on its owning task in hosted mode.

The virtual phone has no SIM startup event. Instead of manufacturing that
notification or changing readiness bytes, invoke the original state-2 handler
before a WAP callback. Its asynchronous completion owns the ready transition.
"""
import struct

CALLBACK = 0x44b9d998
INITIALIZE = 0x44b9ccec
STATE = 0x4c391261


class BrowserStartup:
    def __init__(self, session):
        self.session = session
        self.saved = None
        self.stack = None
        self.completed = False
        self.started = False
        self.ready_reported = False
        if session.d.read(CALLBACK, 4) != bytes.fromhex('90470006'):
            raise RuntimeError('Unsupported WAP callback dispatch')
        if session.d.read(INITIALIZE, 2) != bytes.fromhex('00b5'):
            raise RuntimeError('Unsupported WAP initialization handler')
        session.d.breakpoint(CALLBACK, thumb=True)
        self.report()

    def report(self):
        if self.ready_reported:
            return
        state = self.session.d.read(STATE, 2).hex()
        self.ready_reported = self.completed and state == '0401'
        self.session.provenance['browser_startup'] = {
            'provider': 'original WAP initialization on WAP task',
            'started': self.started, 'initializer_returned': self.completed,
            'state': state,
            'readiness_bytes_written': False,
        }

    def observe(self, regs):
        if regs[15] != CALLBACK:
            return False
        s = self.session
        if self.saved is not None:
            if regs[13] != self.stack:
                raise RuntimeError('WAP initialization returned on a different stack')
            if s.d.packet('G' + self.saved.hex()) != 'OK':
                raise RuntimeError('Unable to restore WAP callback')
            self.saved = None
            self.completed = True
            s.d.breakpoint(CALLBACK, enabled=False, thumb=True)
            self.report()
            return True
        if s.d.read(STATE, 2) != b'\x02\x00':
            s.d.step_over_breakpoint(CALLBACK, thumb=True)
            return True
        self.saved = bytes.fromhex(s.d.packet('g'))
        self.stack = regs[13]
        packet = bytearray(self.saved)
        struct.pack_into('<I', packet, 14 * 4, CALLBACK | 1)
        struct.pack_into('<I', packet, 15 * 4, INITIALIZE)
        if s.d.packet('G' + packet.hex()) != 'OK':
            raise RuntimeError('Unable to start original WAP initialization')
        self.started = True
        self.report()
        return True
