"""Trace original video preview and LCD activity using physical phone keys."""
import json
import hashlib
import os
import struct

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from w800 import guest_ui
from w800.backend import ROOT
from w800.guest_ui import OriginalUISession
from w800.tools.debug import Debugger

POINTS = {
    0x450ea7d0: 'Video launcher page', 0x450ea7f8: 'Video launch request',
    0x450ea7fc: 'Video launch return', 0x451265e4: 'Preview launcher',
    0x44f4aba8: 'Viewer main page', 0x44f4abbc: 'Viewer show return',
    0x44f4abc8: 'Viewer callback', 0x44f4abd8: 'Viewer callback context',
    0x44f4abe2: 'Viewer event type', 0x44f4ac60: 'Viewer event subtype',
    0x449ff6f4: 'DSP kernel upload',
    0x4488d660: 'AAC decoder control',
    0x4488d6e4: 'Decoder configuration arguments',
    0x4488d970: 'Decoder configuration packet',
    0x447367a8: 'DSP logical send',
}
SHOW_POINTS = {
    0x44d276e4: 'GUI show', 0x44d276ec: 'GUI window kind',
    0x44d276fe: 'GUI second kind', 0x44d27710: 'GUI prepared',
    0x44d53050: 'GUI window show', 0x44d29620: 'GUI second show',
    0x44d278f8: 'GUI book visibility', 0x44d2791c: 'GUI book condition',
    0x44d27920: 'GUI condition result', 0x44d27934: 'GUI book show request',
    0x44e74f28: 'Book show', 0x44e74f38: 'Book show callback',
    0x44e74f42: 'Book current visibility', 0x44d270c8: 'Window visibility dispatch',
    0x44d270cc: 'Window visibility result',
}


def main():
    original = Debugger.registers
    previous = guest_ui.CHECKPOINTS.copy()
    rows = []
    hardware = []
    session = OriginalUISession(default_theme=True)

    def registers(debugger):
        values = original(debugger)
        if values[15] in (0x448ca366, 0x44a47af0):
            tcb = session.word(0x4c041c68)
            pid = struct.unpack('<H', debugger.read(tcb + 2, 2))[0]
            if pid == (session.word(0x4c0bae24) & 0x7fff):
                row = {'point': 'MMI receive', 'registers': [hex(x) for x in values],
                       'stack': debugger.read(values[13], 512).hex()}
                if values[0] and 0x44000000 <= values[0] <= 0x4c7fffc0:
                    row['r0_data'] = debugger.read(values[0], 64).hex()
                rows.append(row)
        if values[15] == 0x44f4aba8:
            guest_ui.CHECKPOINTS.update(SHOW_POINTS)
            for address in SHOW_POINTS:
                debugger.breakpoint(address, thumb=True)
        if values[15] in POINTS:
            if values[15] == 0x447367a8 and values[0] not in (18, 20):
                return values
            row = {'point': POINTS[values[15]], 'registers': [hex(x) for x in values]}
            if values[15] == 0x447367a8 and 0x4c000000 <= values[1] <= 0x4c7fffc0:
                row['packet'] = debugger.read(values[1], 64).hex()
            if values[15] == 0x4488d970:
                row['packet'] = debugger.read(values[1], 64).hex()
            if values[15] == 0x4488d6e4:
                row['stack'] = debugger.read(values[13], 64).hex()
            if values[15] == 0x449ff6f4 and 0 < values[1] <= 131072:
                data = debugger.read(values[2], values[1] * 2)
                output = ROOT / 'reports/video-dsp'
                output.mkdir(exist_ok=True)
                name = f'{len(rows):04d}-{values[0]:06x}.bin'
                (output / name).write_bytes(data)
                row.update({'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                            'path': str(output / name)})
            if values[15] == 0x44f4aba8:
                row['book'] = debugger.read(values[1], 128).hex()
            if values[15] == 0x44f4abd8 and 0x4c000000 <= values[0] <= 0x4c7fff80:
                row['context'] = debugger.read(values[0], 128).hex()
            rows.append(row)
        elif values[15] in SHOW_POINTS:
            row = {'point': SHOW_POINTS[values[15]], 'registers': [hex(x) for x in values]}
            for index in (0, 4):
                if 0x4c000000 <= values[index] <= 0x4c7fff80:
                    row[f'r{index}_data'] = debugger.read(values[index], 128).hex()
            rows.append(row)
        return values

    def press(key):
        session.advance(120, (key, True))
        session.advance(450, (key, False))
        if 'W800_LCD_UNSUPPORTED' in log_path.read_text(errors='replace'):
            hardware.append({'after_key': key,
                             'pdi': session.d.read(0xf7000100, 256).hex(),
                             'dma': session.d.read(0xf2000100, 256).hex()})

    try:
        Debugger.registers = registers
        guest_ui.CHECKPOINTS.update(POINTS)
        session.start()
        log_path = session.q.directory / 'video-hardware.log'
        session.q.command('human-monitor-command', {'command-line': f'logfile {log_path}'})
        session.q.command('human-monitor-command', {'command-line': 'log unimp,guest_errors'})
        for key in ('select', 'down', 'left', 'select', 'down', 'select', 'down', 'select'):
            press(key)
        for _ in range(3):
            session.advance(1000)
        session.frame().save(str(ROOT / 'reports/video-preview-open.png'))
        guest_ui.CHECKPOINTS[0x448ca366] = 'Observed MMI receive'
        session.d.breakpoint(0x448ca366, thumb=True)
        guest_ui.CHECKPOINTS[0x44a47af0] = 'Observed MMI wait'
        session.d.breakpoint(0x44a47af0, thumb=True)
        press('soft_left')
        session.frame().save(str(ROOT / 'reports/video-play-0.png'))
        for second in range(1, 4):
            session.advance(1000)
            session.frame().save(str(ROOT / f'reports/video-play-{second}.png'))
        press('back')
        session.frame().save(str(ROOT / 'reports/video-preview-back.png'))
        report = session.report()
        report['hardware_diagnostics_tail'] = session.q.diagnostics().splitlines()[-1000:]
        report['hardware_log'] = log_path.read_text(errors='replace')
    except Exception as error:
        report = session.report()
        report['error'] = repr(error)
    finally:
        session.close()
        Debugger.registers = original
        guest_ui.CHECKPOINTS.clear()
        guest_ui.CHECKPOINTS.update(previous)
    report['video_trace'] = rows
    report['hardware_snapshots'] = hardware
    (ROOT / 'reports/video-preview.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'trace': rows, 'error': report.get('error')}, indent=2))


if __name__ == '__main__':
    main()
