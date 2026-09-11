"""Observe native SoundRecorder setup without injecting capture data."""
import argparse
from collections import Counter, deque
import hashlib
import json
from pathlib import Path
import struct
import traceback

from w800 import guest_ui
from w800.tools.debug import Debugger
from w800.tools.trace_audio_hardware import POINTS
from w800.tools.probe_menu_services import press


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('w800/reports/microphone-setup'))
    parser.add_argument('--trace-waits', action='store_true')
    parser.add_argument('--capture-kernels', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    points = dict(POINTS)
    points.update({
        0x44c6fa20: 'Recorder audio-resource dispatch',
        0x44c6fa22: 'Recorder audio-resource request return',
        0x44c6fa3c: 'Recorder audio-resource confirmation',
        0x44c6fa8c: 'Recorder file setup',
        0x44c6fb4c: 'Recorder file/resource interface call',
        0x44c6fb5c: 'Recorder file start call',
        0x44c6fbd0: 'Recorder control',
        0x449cb3ac: 'Original encoder resource control',
        0x449cb3e6: 'Original encoder resource wait return',
        0x449cb424: 'Original encoder configuration arguments',
        0x449cb310: 'Original encoder reply callback',
    })
    if args.trace_waits:
        points.update({0x44a47ae8: 'Native timed receive',
                       0x44a47af0: 'Native receive',
                       0x448ca366: 'MMI delivered message'})
    session = guest_ui.OriginalUISession(default_theme=False,
        progress=lambda message: print(message, flush=True))
    previous = Debugger.step_over_breakpoint
    counts, trace, waits = Counter(), [], deque(maxlen=240)
    wait_sites = {}
    phase = 'startup'

    def observe(debugger, address, thumb=False):
        if address in points:
            regs = debugger.registers()
            name = points[address]
            counts[name] += 1
            row = dict(event=name, phase=phase, pc=hex(address),
                       registers=[hex(r) for r in regs])
            tcb = session.word(0x4c041c68)
            row['pid'] = hex(struct.unpack('<H', debugger.read(tcb + 2, 2))[0])
            if address in (0x44a47ae8, 0x44a47af0, 0x448ca366) and row['pid'] in ('0x38', '0x88'):
                return previous(debugger, address, thumb)
            if 0x4c000000 <= regs[13] <= 0x4c7fff00:
                row['stack'] = debugger.read(regs[13], 256).hex()
            if address == 0x447367a8:
                words = struct.unpack_from('<H', debugger.read(regs[1] - 8, 8), 4)[0]
                row['payload'] = debugger.read(regs[1], min(words * 2, 2048)).hex()
            elif address == 0x449ff6f4 and args.capture_kernels and 0 < regs[1] <= 131072:
                data = debugger.read(regs[2], regs[1] * 2)
                directory = args.output / 'dsp'
                directory.mkdir(exist_ok=True)
                path = directory / f'{counts[name]:04d}-{regs[0]:06x}.bin'
                path.write_bytes(data)
                row.update(word_address=regs[0], words=regs[1],
                           arm_source=hex(regs[2]), path=str(path),
                           sha256=hashlib.sha256(data).hexdigest())
            if address in (0x44c6fa20, 0x44c6fa22, 0x44c6fa3c, 0x44c6fb4c,
                           0x44c6fb5c, 0x44a47ae8, 0x44a47af0):
                row['objects'] = {hex(value): debugger.read(value, 128).hex()
                    for value in regs[:8] if 0x44000000 <= value <= 0x4c7fff80}
            if address in (0x44a47ae8, 0x44a47af0, 0x448ca366):
                waits.append(row)
                if address != 0x448ca366:
                    wait_sites[(row['pid'], regs[14])] = row
            elif len(trace) < 3000:
                trace.append(row)
        return previous(debugger, address, thumb)

    def save(label):
        session.frame().save(str(args.output / (label + '.png')))
        report = session.report()
        report.update(audio_trace=trace, waits=list(waits), wait_sites=list(wait_sites.values()), counts=dict(counts),
                      diagnostics=session.q.diagnostics())
        (args.output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
        print(label, dict(counts), flush=True)

    Debugger.step_over_breakpoint = observe
    try:
        session.start()
        press(session, 'select')
        for address, name in points.items():
            guest_ui.CHECKPOINTS[address] = name
            session.d.breakpoint(address, thumb=True)
        phase = 'record'
        for key in ['up', 'right', 'select'] + ['down'] * 5 + ['select']:
            press(session, key)
        for _ in range(4):
            session.advance(1000)
        save('record-wait')
        phase = 'cancel'
        press(session, 'back')
        session.advance(1000)
        save('cancel')
    except BaseException:
        (args.output / 'error.txt').write_text(traceback.format_exc())
        raise
    finally:
        session.close()
        Debugger.step_over_breakpoint = previous


if __name__ == '__main__':
    main()
