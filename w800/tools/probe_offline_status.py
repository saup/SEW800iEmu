"""Physical navigation with the bounded offline status endpoint in local QEMU."""
import argparse
import json
import os
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from w800 import guest_ui
from w800.backend import ROOT
from w800.guest_ui import OriginalUISession
from w800.offline_status import OfflineStatus, STATUS_SEND
from w800.tools.probe_menu_services import capture, press


def attach_probe(session):
    """Temporary probe wiring; production uses explicit dispatch/poll hooks."""
    installed = getattr(session, 'offline_status', None)
    if installed is not None:
        return installed
    endpoint = OfflineStatus(session)
    original_registers = session.d.registers
    original_advance = session.advance
    original_close = session.close
    previous_checkpoint = guest_ui.CHECKPOINTS.get(STATUS_SEND)
    guest_ui.CHECKPOINTS[STATUS_SEND] = 'Offline read-only status destination'
    session.d.breakpoint(STATUS_SEND, thumb=True)

    def registers():
        regs = original_registers()
        endpoint.route(regs)
        return regs

    def advance(*args, **kwargs):
        result = original_advance(*args, **kwargs)
        endpoint.poll()
        return result

    def close():
        if previous_checkpoint is None:
            guest_ui.CHECKPOINTS.pop(STATUS_SEND, None)
        else:
            guest_ui.CHECKPOINTS[STATUS_SEND] = previous_checkpoint
        original_close()

    session.d.registers = registers
    session.advance = advance
    session.close = close
    return endpoint


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--subset', action='store_true')
    parser.add_argument('--output', type=Path, default=ROOT / 'reports/menu-services-offline-status')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    session = OriginalUISession(start_screen='startup', default_theme=True,
                                full_services=not args.subset)
    try:
        session.start()
        endpoint = attach_probe(session)
        initial_etx = session.word(0xf9030004)
        press(session, 'select')
        previous = capture(session, args.output, '00-main-menu')
        press(session, 'right')
        previous = capture(session, args.output, '01-early-right', previous)
        for _ in range(35):
            session.advance(1000)
        previous = capture(session, args.output, '02-idle-35-seconds', previous)
        press(session, 'left')
        previous = capture(session, args.output, '03-late-left', previous)
        press(session, 'left')
        previous = capture(session, args.output, '04-late-left-again', previous)
        press(session, 'down')
        press(session, 'left')
        press(session, 'select')
        capture(session, args.output, '05-open-file-manager', previous)
        result = {'adapter': endpoint.snapshot(), 'etx_initial': initial_etx,
                  'etx_final': session.word(0xf9030004),
                  'ph_state': hex(session.word(0x4c04a5c8)),
                  'source_unchanged': session.report()['source_unchanged']}
        (args.output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result), flush=True)
    except Exception as error:
        report = {'error': str(error), 'report': session.report()}
        if session.d is not None:
            report['registers'] = [hex(value) for value in session.d.registers()]
        (args.output / 'error.json').write_text(json.dumps(report, indent=2) + '\n')
        raise
    finally:
        session.close()


if __name__ == '__main__':
    main()
