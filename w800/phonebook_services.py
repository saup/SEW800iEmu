"""Original phonebook startup and absent-card reply completion.

The compatibility adapter only forwards the unavailable response the original
PB handler has already built. It never supplies a SIM application, changes a
validation result, or reports fixed dialling as successfully queried.
"""
import struct

ACTIVATE = 0x450bb9c8
START_SORTING = 0x450bb9e4
CONTEXT = 0x4424552c
UNAVAILABLE_REPLY = 0x44dc7cbe
SEND_REPLY = 0x44c90200


def initialize(session):
    """Issue the original asynchronous activation and loading requests on MMI."""
    if getattr(session, '_phonebook_initialized', False):
        return observe(session)
    context = session.word(CONTEXT)
    if context != 0x44245518:
        raise RuntimeError('Unsupported original phonebook client context')
    results = []
    for function in (ACTIVATE, START_SORTING):
        result = session.call_on_mmi(function, context)
        results.append({'entry': hex(function), 'result': hex(result)})
        if result != 0:
            raise RuntimeError(f'Original phonebook startup failed: {result:#x}')
        session.advance(1000)
    session.provenance['phonebook_initialization'] = results
    for _ in range(10):
        if observe(session)['access_levels'][0] == 7:
            break
        session.advance(1000)
    else:
        raise TimeoutError('Original phone-memory phonebook did not finish loading')
    session._phonebook_initialized = True
    return observe(session)


def observe(session):
    return {'initialization': session.d.read(0x4c06068c, 10).hex(),
            'access_levels': list(session.d.read(0x4c390885, 3))}


def install_unavailable_adapter(session):
    """Install the absent-card response completion once per private session."""
    adapter = getattr(session, '_phonebook_unavailable_adapter', None)
    if adapter is None:
        adapter = UnavailableReplyAdapter(session)
        session._phonebook_unavailable_adapter = adapter
    return adapter


class UnavailableReplyAdapter:
    """Complete only the proven absent-card fixed-dialling response path.

    Install after debugger creation and before Contacts is opened. The added
    QEMU breakpoint does not change instruction bytes. The original send API
    runs on the existing PB task and owns/consumes the existing reply pointer.
    All task registers are restored when that send call returns.
    """
    def __init__(self, session):
        from . import guest_ui
        self.session = session
        session._phonebook_unavailable_adapter = self
        self.original_step = session.d.step_over_breakpoint
        self.pending = None
        self.rows = session.provenance.setdefault('phonebook_unavailable_replies', [])
        guest_ui.CHECKPOINTS[UNAVAILABLE_REPLY] = 'Original absent-SIM phonebook response'
        session.d.breakpoint(UNAVAILABLE_REPLY, thumb=True)
        session.d.step_over_breakpoint = self.step_over_breakpoint

    def step_over_breakpoint(self, address, thumb=False):
        if address != UNAVAILABLE_REPLY:
            return self.original_step(address, thumb)
        d = self.session.d
        regs = d.registers()
        if self.pending is not None:
            pending = self.pending
            if regs[13] != pending['stack']:
                raise RuntimeError('Phonebook response returned on a different task stack')
            if self.session.word(pending['slot']) != 0:
                raise RuntimeError('Original phonebook sender did not consume its response')
            if d.packet('G' + pending['registers'].hex()) != 'OK':
                raise RuntimeError('Unable to restore original phonebook caller')
            pending['row']['sent'] = True
            self.pending = None
            return self.original_step(address, thumb)
        stack = regs[13]
        if not 0x4c000000 <= stack <= 0x4c7fff00:
            raise RuntimeError('Phonebook reply stack is outside RAM')
        slot = stack + 8
        pointer = self.session.word(slot)
        if not 0x4c000008 <= pointer <= 0x4c7ffff4:
            raise RuntimeError('Phonebook unavailable response is outside RAM')
        reply = d.read(pointer, 12)
        sender = self.session.word(pointer - 8)
        if (struct.unpack_from('<I', reply)[0] != 0x1b13 or
                reply[5:9] != bytes((0, 0x1a, 0, 0)) or
                regs[4] != sender or sender != self.session.word(0x4c0bae24)):
            raise RuntimeError('Unrecognized phonebook unavailable response')
        row = {'response': hex(pointer), 'sender': hex(sender),
               'original_bytes': reply.hex(), 'original_error': '0x1a', 'sent': False}
        self.rows.append(row)
        saved = bytes.fromhex(d.packet('g'))
        self.pending = {'registers': saved, 'stack': stack, 'slot': slot, 'row': row}
        call = bytearray(saved)
        for index, value in ((0, slot), (1, sender), (14, address | 1), (15, SEND_REPLY)):
            struct.pack_into('<I', call, index * 4, value)
        if d.packet('G' + call.hex()) != 'OK':
            raise RuntimeError('Unable to call original phonebook response sender')
