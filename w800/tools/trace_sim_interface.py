"""Trace original SIM service diagnostics and SIMIF driver calls in private QEMU.

This observer only reads registers and memory at original firmware breakpoints.
It does not insert a card, alter validation results, or send network traffic.
"""
import argparse
import json
from pathlib import Path
from PySide6.QtGui import QGuiApplication
from .. import guest_ui
from .debug import Debugger
from .trace_messages import cstring

POINTS = {0x449f3310: 'SIM error diagnostic',
          0x449f3328: 'SIM detailed diagnostic',
          0x449f32f0: 'SIM state diagnostic'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--point', action='append', default=[],
                        help='Additional original Thumb checkpoint address')
    parser.add_argument('--seconds', type=int, default=0)
    parser.add_argument('--output', default='w800/reports/sim-interface.json')
    args = parser.parse_args()
    for value in args.point:
        POINTS[int(value, 0)] = 'SIM interface checkpoint'
    app = QGuiApplication.instance() or QGuiApplication([])
    samples = []

    class Observer(Debugger):
        def registers(self):
            regs = super().registers()
            if regs[15] in POINTS and len(samples) < 1000:
                item = {'pc': hex(regs[15]), 'name': POINTS[regs[15]],
                        'registers': [hex(n) for n in regs],
                        'stack': self.read(regs[13], 128).hex()}
                if regs[15] in (0x449f3310, 0x449f3328, 0x449f32f0):
                    item['text'] = [cstring(self, p) for p in regs[:3]]
                samples.append(item)
            return regs

    guest_ui.Debugger = Observer
    guest_ui.CHECKPOINTS.update(POINTS)
    session = guest_ui.OriginalUISession(progress=lambda s: print(s, flush=True))
    output = {}
    try:
        session.start()
        for _ in range(args.seconds):
            session.advance(1000)
    except Exception as error:
        output['error'] = f'{type(error).__name__}: {error}'
        print(output['error'], flush=True)
    finally:
        output['samples'] = samples
        output['report'] = session.report()
        if session.q:
            output['qemu_diagnostics'] = session.q.diagnostics()
        Path(args.output).write_text(json.dumps(output, indent=2) + '\n')
        session.close()
    print(args.output, flush=True)


if __name__ == '__main__':
    main()
