"""Name-only customization of the original offline standby display.

The native ui_network.c SPName setter owns a TextID and redraws the original
standby text slot. This adapter does not implement network registration,
change SIM/radio state, or answer any cellular request.
"""

STANDBY_BOOK = 0x44e9202c
STARTUP_BOOK = 0x44e916f0
CREATE_TEXT = 0x44edd944
COPY_TEXT = 0x44eddb68
SET_OPERATOR_TEXT = 0x44e93c14
SHOW_STANDBY = 0x44e9203c
HIDE_BOOK = 0x44e6dcb8
SHOW_BOOK = 0x44e6dccc
EMPTY_TEXT = 0x6fffffff
MAX_NAME_LENGTH = 32


def show_original_standby(session):
    """Expose the original standby book with its native input focus."""
    book = session.call_on_mmi(STANDBY_BOOK)
    if not 0x4c000000 <= book < 0x4c7ffe00:
        raise RuntimeError('Original standby book is unavailable')
    startup = session.call_on_mmi(STARTUP_BOOK)
    if startup:
        session.call_on_mmi(HIDE_BOOK, startup)
    session.call_on_mmi(0x44e92140, book, 1, 0)
    session.call_on_mmi(SHOW_STANDBY)


def validate_name(name):
    if name is None:
        return None
    if not isinstance(name, str):
        raise ValueError('Virtual network name must be text')
    if any(not char.isprintable() or ord(char) > 0xffff for char in name):
        raise ValueError('Use printable characters supported by the phone font')
    name = name.strip()
    if len(name) > MAX_NAME_LENGTH:
        raise ValueError(f'Virtual network name must be at most {MAX_NAME_LENGTH} characters')
    return name or None


class VirtualNetworkName:
    def __init__(self, session):
        self.session = session
        self.name = None
        self.book = self.startup_book = None
        self.original_text = None
        self.original_mode = 0

    def set(self, name):
        name = validate_name(name)
        session = self.session
        if not session.ready:
            raise RuntimeError('Virtual network name requires a ready original UI')
        if name == self.name:
            return self.snapshot()
        if self.book is None:
            # This literal is emitted by the original caller of the setter.
            if session.d.read(0x4420f9cf, 10) != b'%sSPName: ':
                raise RuntimeError('Original operator-name ABI did not match')
            book = session.call_on_mmi(STANDBY_BOOK)
            if not 0x4c000000 <= book < 0x4c7ffe00:
                raise RuntimeError('Original standby book is unavailable')
            self.book = book
            text = session.word(book + 0x64)
            self.original_text = session.call_on_mmi(COPY_TEXT, text)
            gui = session.word(book + 0x18)
            widget = session.call_on_mmi(0x44e90f80, gui)
            self.original_mode = session.d.read(widget + 0xb4, 1)[0]
            self.startup_book = session.call_on_mmi(STARTUP_BOOK)
        if name is None:
            # Transfer the retained original reference back to its native
            # owner. The setter releases the previous custom text itself.
            text = self.original_text
            mode = self.original_mode
        else:
            pointer = session.files.stage(15000, name.encode('utf-16le') + b'\0\0')
            text = session.call_on_mmi(CREATE_TEXT, pointer, 0, len(name))
            if text == EMPTY_TEXT:
                raise RuntimeError('Original TextID allocation failed')
            mode = 0
        session.call_on_mmi(SET_OPERATOR_TEXT, text, mode)
        if session.word(self.book + 0x64) != text:
            raise RuntimeError('Original standby did not retain its operator text')
        if name is not None:
            # Suspend only the startup-choice GUI through its original book
            # API. Its state and callbacks remain available for Clear.
            if self.name is None and self.startup_book:
                session.call_on_mmi(HIDE_BOOK, self.startup_book)
            # Native background manager selects ordinary standby input and
            # presentation with (active=1, secondary/call layout=0).
            session.call_on_mmi(0x44e92140, self.book, 1, 0)
            session.call_on_mmi(SHOW_STANDBY)
        else:
            if getattr(session, '_phone_mode_started', False) and session.start_phone_standby:
                show_original_standby(session)
            elif self.startup_book:
                session.call_on_mmi(SHOW_BOOK, self.startup_book)
            self.book = self.startup_book = self.original_text = None
        self.name = name
        snapshot = self.snapshot()
        session.provenance['virtual_network_name'] = snapshot
        return snapshot

    def snapshot(self):
        return {'name': self.name, 'scope': 'offline standby display only',
                'native_setter': hex(SET_OPERATOR_TEXT),
                'radio_registration_changed': False, 'sim_state_changed': False}
