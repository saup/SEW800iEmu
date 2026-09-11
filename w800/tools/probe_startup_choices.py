"""Original Music only/Start phone transitions through physical phone keys."""
import argparse
import json
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from w800.backend import ROOT
from w800.guest_ui import OriginalUISession
from w800.tools.probe_menu_services import capture, press, trace_calls
from w800 import guest_ui
from w800.radio_rejection import OriginalRadioRejection, REQUEST_SEND


def attach_rejection(session):
    existing = getattr(session, 'radio_rejection', None)
    if existing is not None:
        return existing
    adapter = OriginalRadioRejection(session)
    original_registers = session.d.registers
    original_advance = session.advance
    original_close = session.close
    previous_checkpoint = guest_ui.CHECKPOINTS.get(REQUEST_SEND)
    guest_ui.CHECKPOINTS[REQUEST_SEND] = 'Original rejected radio request destination'
    session.d.breakpoint(REQUEST_SEND, thumb=True)
    def registers():
        values = original_registers()
        adapter.route(values)
        return values
    def advance(*args, **kwargs):
        result = original_advance(*args, **kwargs)
        adapter.poll()
        return result
    def close():
        try:
            original_close()
        finally:
            if previous_checkpoint is None:
                guest_ui.CHECKPOINTS.pop(REQUEST_SEND, None)
            else:
                guest_ui.CHECKPOINTS[REQUEST_SEND] = previous_checkpoint
    session.d.registers = registers
    session.advance = advance
    session.close = close
    return adapter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dispatch-rejection', action='store_true')
    args = parser.parse_args()
    directory = ROOT / ('reports/menu-services-startup-choices-rejection' if args.dispatch_rejection
                        else 'reports/menu-services-startup-choices')
    directory.mkdir(parents=True, exist_ok=True)
    with OriginalUISession() as session:
        if args.dispatch_rejection:
            attach_rejection(session)
        trace_calls(session, (0x44e91ad8, 0x44e91ae4, 0x45052c18,
                              0x44d225e0, 0x44e91b08, 0x45052c38,
                              0x45052c44, 0x4498de74, 0x4496e504), 'window_input')
        previous = ()
        def snap(label):
            nonlocal previous
            session.provenance['startup_mode_word'] = hex(session.word(0x4c289034))
            previous = capture(session, directory, label, previous)
        snap('00-startup')
        press(session, 'select')
        snap('01-main-menu')
        for key in ('right', 'down', 'down', 'select'):
            press(session, key)
        snap('02-settings')
        press(session, 'back')
        press(session, 'back')
        snap('03-return-startup')
        press(session, 'down')
        snap('04-music-selected')
        press(session, 'select')
        snap('05-music-only')
        press(session, 'back')
        snap('06-back-from-music')
        press(session, 'up')
        snap('07-up-to-start-phone')
        press(session, 'select')
        snap('08-select-start-phone')
        # Separate additional observation, not part of the original test.
        session.advance(1000)
        press(session, 'up')
        press(session, 'select')
        snap('09-additional-up-select')
        (directory / 'frozen-ram.bin').write_bytes(session.d.read(0x4c000000, 0x800000))
        print(json.dumps({'mode_word': hex(session.word(0x4c289034))}), flush=True)


if __name__ == '__main__':
    main()
