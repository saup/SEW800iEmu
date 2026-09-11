"""Observe the original operator shortcut and WAP startup in a private VM."""
import json
import argparse
import os
import struct

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from w800.backend import ROOT
from w800 import guest_ui
from w800.guest_ui import OriginalUISession
from w800.tools.debug import Debugger
from w800.tools.trace_messages import cstring
from w800.tests.test_menu_services import press


POINTS = {
    0x44b9c5b8: 'WAP initialize', 0x44b9cf1c: 'WAP interfaces ready',
    0x44b9cf42: 'WAP received event', 0x44b9d098: 'WAP dispatch',
    0x44b9ce4c: 'WAP state transition',
    0x44b9d998: 'WAP event callback',
    0x450bec44: 'Browser request', 0x450bec98: 'Browser request return',
    0x44be0e64: 'OperatorWebPage selection',
    0x44be1808: 'Operator homepage configuration',
    0x44be17b2: 'Operator title property returned',
    0x44b7537e: 'Native browser readiness guard',
    0x44b950fc: 'Native browser error display',
    0x44b9d9a8: 'WAP native request release',
    0x44b9d9ac: 'WAP native request release return',
}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report-directory',default='operator-current')
    args=parser.parse_args()
    directory=ROOT/'reports'/args.report_directory
    directory.mkdir(parents=True,exist_ok=True)
    rows = []
    phase='startup'
    original = Debugger.registers
    previous_points = guest_ui.CHECKPOINTS.copy()

    def registers(debugger):
        values = original(debugger)
        if values[15] in POINTS:
            row = {'point': POINTS[values[15]], 'pc': hex(values[15]),
                   'phase': phase,
                   'registers': [hex(x) for x in values]}
            row['wap_state']=debugger.read(0x4c391261,16).hex()
            if values[15] in (0x44b9d9a8, 0x44b9d9ac):
                slot = values[13] + 8
                pointer = struct.unpack('<I', debugger.read(slot, 4))[0]
                row['native_message_slot'] = hex(slot)
                row['native_message_pointer'] = hex(pointer)
                if 0x4c000000 <= pointer <= 0x4c7ffff0:
                    row['native_message'] = debugger.read(pointer, 16).hex()
            if values[15] == 0x44be17b2:
                title = debugger.read(values[13] + 12, 32)
                row['title_property_utf16le'] = title.hex()
                row['title_property'] = title.decode('utf-16le').split('\0')[0]
            if values[15] == 0x44b9cf42:
                pointer = struct.unpack('<I', debugger.read(values[13], 4))[0]
                if 0x4c000000 <= pointer <= 0x4c7fffc0:
                    row['event'] = debugger.read(pointer, 64).hex()
            if values[15] == 0x450bec44 and 0x4c000000 <= values[3] <= 0x4c7fff00:
                row['url'] = debugger.read(values[3], 256).split(b'\0')[0].decode('utf-8', 'replace')
            rows.append(row)
        elif values[15] in guest_ui.LOGGERS:
            message = cstring(debugger, values[0])
            if message.startswith('WAP:'):
                rows.append({'point': 'WAP log', 'format': message,
                             'registers': [hex(x) for x in values]})
        return values

    session = OriginalUISession(start_screen='startup', default_theme=True,
        progress=lambda message: print(message,flush=True))
    try:
        Debugger.registers = registers
        guest_ui.CHECKPOINTS.update(POINTS)
        session.start()
        phase='phone'
        press(session,'select')
        phase='shortcut-selection'
        for key in ('up', 'left'):
            session.advance(120, (key, True))
            session.advance(450, (key, False))
        session.frame().save(str(directory/'01-label.png'))
        phase='shortcut-action'
        session.advance(120, ('select', True))
        session.advance(450, ('select', False))
        session.frame().save(str(directory/'02-action.png'))
        phase='back'
        press(session,'back')
        session.frame().save(str(directory/'03-back.png'))
        phase='navigation'
        press(session,'down')
        session.frame().save(str(directory/'04-navigation.png'))
        press(session,'up')
        phase='repeated-shortcut-action'
        press(session,'select')
        session.frame().save(str(directory/'05-repeated-error.png'))
        press(session,'back')
        session.frame().save(str(directory/'06-repeated-back.png'))
        report = session.report()
        report['wap_state'] = session.d.read(0x4c391261, 16).hex()
        report['diagnostics']=session.q.diagnostics()
    except Exception as error:
        report = session.report()
        report['error'] = repr(error)
    finally:
        session.close()
        Debugger.registers = original
        guest_ui.CHECKPOINTS.clear()
        guest_ui.CHECKPOINTS.update(previous_points)
    report['operator_trace'] = rows
    (directory/'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'trace_events': len(rows), 'error': report.get('error')}, indent=2))


if __name__ == '__main__':
    main()
