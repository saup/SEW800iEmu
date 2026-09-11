"""Read original AAC filter selection and stream headers through physical Play."""
import json
import os
import struct

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from w800 import guest_ui
from w800.backend import ROOT
from w800.guest_ui import OriginalUISession
from w800.tests.test_menu_services import press

POINTS = {
    0x4488d834: 'resampler selection', 0x4488d838: 'resampler selected',
    0x4488daa6: 'coefficient copy', 0x447367a8: 'DSP logical send',
    0x4488dc84: 'compressed stream writer', 0x4488e0f8: 'sample reader dispatch',
    0x4488ddcc: 'stream4 write', 0x4488d6e4: 'decoder configuration',
}


def main():
    directory = ROOT / 'reports/aac-service-probe'
    directory.mkdir(parents=True, exist_ok=True)
    rows = []
    previous = guest_ui.CHECKPOINTS.copy()
    session = OriginalUISession(default_theme=True)
    try:
        session.start()
        original_registers = session.d.registers
        def registers():
            values = original_registers()
            pc = values[15]
            if pc not in POINTS or (pc == 0x447367a8 and values[0] not in (18, 20)):
                return values
            row = {'point': POINTS[pc], 'registers': [hex(v) for v in values]}
            if pc == 0x447367a8:
                opcode = struct.unpack('<H', session.d.read(values[1], 2))[0]
                size = {0x0e00: 48, 0x0e02: 104, 0x0e04: 4}.get(opcode, 48)
                if opcode == 0x0804:
                    size = min(4096, 4 + 2 * struct.unpack('<H', session.d.read(values[1] + 2, 2))[0])
                row['packet'] = session.d.read(values[1], size).hex()
            elif pc == 0x4488d838:
                row['selection'] = session.d.read(values[13] + 0x3c, 0x2e).hex()
            elif pc == 0x4488ddcc:
                row['stream_words'] = session.d.read(values[1], min(4096, values[2] * 2)).hex()
            elif pc == 0x4488e0f8:
                row['sample_reader'] = hex(session.word(values[0] + 0x20c))
            elif pc == 0x4488d6e4:
                row['caller_context'] = session.d.read(values[6], 0x220).hex()
                row['sample_reader_candidate'] = hex(session.word(values[6] + 0x20c))
                route = session.word(values[13])
                row['route_record'] = session.d.read(route, 6).hex()
            rows.append(row)
            return values
        session.d.registers = registers
        guest_ui.CHECKPOINTS.update(POINTS)
        for pc in POINTS:
            session.d.breakpoint(pc, thumb=True)
        for key in ('select', 'down', 'left', 'select', 'down', 'select', 'down', 'select'):
            press(session, key)
        press(session, 'soft_left')
        for _ in range(3):
            session.advance(1000)
        session.frame().save(str(directory / 'after-play.png'))
        report = session.report()
        report['diagnostics'] = session.q.diagnostics().splitlines()[-1000:]
    except Exception as error:
        report = session.report()
        report['error'] = repr(error)
    finally:
        session.close()
        guest_ui.CHECKPOINTS.clear()
        guest_ui.CHECKPOINTS.update(previous)
    report['aac_trace'] = rows
    (directory / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'events': len(rows), 'error': report.get('error')}))


if __name__ == '__main__':
    main()
