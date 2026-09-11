"""Compare original media/OS clocks with QEMU time using bounded observation."""
import json
import os
import struct
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from w800 import guest_ui
from w800.backend import ROOT
from w800.guest_ui import OriginalUISession
from w800.tests.test_menu_services import press


TIMER_POINTS = {
    0x448e0a2c: ('OS timer ISR entry', True),
    0x448e0a74: ('ISR compare reload', True),
    0x448e0a76: ('ISR compare reloaded', True),
    0x44b3c70c: ('Idle compare extend', False),
    0x44b3c794: ('Idle compare shorten', False),
    0x44b3c738: ('Idle counter after wake', False),
}
DROP_POINT = 0x4488dce0


def main():
    directory = ROOT / 'reports/media-clock'
    directory.mkdir(parents=True, exist_ok=True)
    previous = guest_ui.CHECKPOINTS.copy()
    session = OriginalUISession(default_theme=True,
                                audio_capture_path=directory / 'audio.wav')
    rows, details, drops = [], [], []
    phase = 'startup'

    def clock():
        return session.q.command('qom-get', {'path': '/machine',
                                           'property': 'virtual-time-ns'})

    def state():
        return dict(virtual_ns=clock(), tick=session.word(0x4200f264),
                    counter=session.word(0xf901001c), compare=session.word(0xf9010004),
                    idle_words=list(struct.unpack('<5I', session.d.read(0x4c07d934, 20))))

    def window(milliseconds):
        before = state()
        wall = time.monotonic()
        result = session.advance(milliseconds)
        rows.append(dict(phase=phase, requested_ms=milliseconds, result=result,
                         wall_seconds=time.monotonic() - wall, before=before, after=state()))

    def detail_window():
        for pc, (name, thumb) in TIMER_POINTS.items():
            guest_ui.CHECKPOINTS[pc] = name
            session.d.breakpoint(pc, thumb=thumb)
        try:
            window(100)
        finally:
            for pc, (_, thumb) in TIMER_POINTS.items():
                session.d.breakpoint(pc, False, thumb=thumb)
                guest_ui.CHECKPOINTS.pop(pc)

    try:
        session.start()
        original_registers = session.d.registers
        original_step = session.d.step_over_breakpoint

        def step(pc, thumb=False):
            return original_step(pc, TIMER_POINTS[pc][1] if pc in TIMER_POINTS else thumb)

        def registers():
            values = original_registers()
            pc = values[15]
            if pc in TIMER_POINTS:
                details.append(dict(phase=phase, point=TIMER_POINTS[pc][0],
                                    registers=[hex(x) for x in values], **state()))
            elif pc == DROP_POINT:
                delta = struct.unpack('<i', struct.pack('<I', values[0]))[0]
                drops.append(dict(phase=phase, delta_ms=delta, dropped=delta < 20,
                                  native_now_ms=values[1], virtual_ns=clock(),
                                  metadata=session.d.read(values[13] + 4, 8).hex(),
                                  sample_bytes=struct.unpack('<H', session.d.read(values[13], 2))[0]))
            return values

        session.d.step_over_breakpoint = step
        session.d.registers = registers
        phase = 'menu'
        for _ in range(4):
            window(1000)
        phase = 'menu-detail'
        detail_window()
        for key in ('select', 'down', 'left', 'select', 'down', 'select', 'down', 'select'):
            press(session, key)
        session.advance(500)
        phase = 'play'
        guest_ui.CHECKPOINTS[DROP_POINT] = 'AAC lateness decision'
        session.d.breakpoint(DROP_POINT, thumb=True)
        press(session, 'soft_left')
        for second in range(12):
            phase = f'play-{second:02d}'
            window(1000)
        phase = 'play-detail'
        detail_window()
        phase = 'back'
        press(session, 'back')
        for _ in range(2):
            window(1000)
        report = session.report()
        report['diagnostics'] = session.q.diagnostics()
    except Exception as error:
        report = session.report()
        report['error'] = repr(error)
    finally:
        session.close()
        guest_ui.CHECKPOINTS.clear()
        guest_ui.CHECKPOINTS.update(previous)
    report.update(windows=rows, timer_details=details, lateness=drops)
    (directory / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(dict(windows=len(rows), timer_details=len(details),
                         lateness=len(drops), error=report.get('error'))))


if __name__ == '__main__':
    main()
