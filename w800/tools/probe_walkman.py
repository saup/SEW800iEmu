"""Bounded physical-key Walkman observer using only a private native VM.

JSON lines on stdin select keys and scheduling windows. Captures come from
the original LCD and emulated device PCM; no host media files are decoded.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import struct
import sys
import traceback
import wave

from w800 import guest_ui
from w800.backend import KEY_QCODES, ROOT
from w800.tools.debug import Debugger
from w800.tools.trace_audio_hardware import POINTS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', default=str(ROOT / 'reports/walkman'))
    parser.add_argument('--early-audio-control', action='store_true',
                        help='Test the original Audio Control provider before MMI initialization')
    args = parser.parse_args()
    if args.early_audio_control and not any(c == 27 for c, _ in guest_ui.COMPONENTS):
        mmi = next(i for i, (c, _) in enumerate(guest_ui.COMPONENTS) if c == 2)
        guest_ui.COMPONENTS = guest_ui.COMPONENTS[:mmi] + ((27, 'Audio Control'),) + guest_ui.COMPONENTS[mmi:]
    directory = Path(args.directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    capture = directory / 'device.wav'
    session = guest_ui.OriginalUISession(default_theme=False,
        audio_capture_path=capture, progress=lambda value: print(value, flush=True))
    POINTS.update({
        0x44d2b4ec: 'Original short sound interface initialization',
        0x44d2b514: 'Original short sound interface initialization result',
        0x44d2b5d4: 'Original short sound request',
        0x44d2b5e2: 'Original short sound interface',
        0x44d2b618: 'Original short sound dispatch',
        0x44d2b774: 'Original short sound return',
    })
    previous = Debugger.step_over_breakpoint
    trace, actions, counts = [], [], Counter()
    phase = 'startup'
    action_command = {}

    def observe(debugger, address, thumb=False):
        if address in POINTS:
            regs = debugger.registers()
            event = POINTS[address]
            counts[event] += 1
            essential = 'control' in event or 'configuration' in event or 'stop' in event
            # Preserve actual terminal records even after the bounded ordinary
            # trace fills during long playback; they establish native EOF.
            if address == 0x448d5714 and regs[2] >= 6:
                essential |= debugger.read(regs[1], 2) == b'\x02\x00'
            if len(trace) < 3000 or essential:
                row = dict(event=event, phase=phase, registers=[hex(r) for r in regs])
                if address in (0x44d2b514, 0x44d2b5e2, 0x44d2b618):
                    row['short_sound_interface'] = hex(session.word(0x4c28967c))
                if address == 0x447367a8:
                    words = struct.unpack_from('<H', debugger.read(regs[1] - 8, 8), 4)[0]
                    row['payload'] = debugger.read(regs[1], min(words * 2, 2048)).hex()
                elif address == 0x448d5714:
                    row['payload'] = debugger.read(regs[1], min(regs[2] * 2, 2048)).hex()
                if essential:
                    row['virtual_ns'] = session.q.command('qom-get', {
                        'path': '/machine', 'property': 'virtual-time-ns'})
                trace.append(row)
        return previous(debugger, address, thumb)

    def save(label):
        frame = directory / f'{len(actions):02d}-{label}.png'
        session.frame().save(str(frame))
        row = dict(label=label, frame=str(frame), messages=list(session.messages)[-30:],
                   command=action_command, event_counts=dict(counts),
                   capture_bytes=capture.stat().st_size if capture.exists() else 0,
                   virtual_ns=session.q.command('qom-get', {
                       'path': '/machine', 'property': 'virtual-time-ns'}))
        actions.append(row)
        report = session.report()
        report.update(actions=actions, audio_trace=trace, event_counts=dict(counts),
                      diagnostics=session.q.diagnostics(),
                      early_audio_control=args.early_audio_control)
        (directory / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(row), flush=True)

    guest_ui.CHECKPOINTS.update(POINTS)
    Debugger.step_over_breakpoint = observe
    try:
        session.start()
        save('startup')
        for index, line in enumerate(sys.stdin):
            if index >= 50:
                raise ValueError('Probe action limit reached')
            command = json.loads(line)
            if command.get('finish'):
                break
            action_command = command
            phase = str(command.get('label', f'action-{index}'))
            if not phase.replace('-', '').replace('_', '').isalnum():
                raise ValueError('Invalid capture label')
            for key in command.get('keys', []):
                if key not in KEY_QCODES:
                    raise ValueError('Unknown physical key')
                session.advance(120, (key, True))
                session.advance(450, (key, False))
            seconds = command.get('seconds', 0)
            if not isinstance(seconds, int) or not 0 <= seconds <= 60:
                raise ValueError('Invalid observation duration')
            for _ in range(seconds):
                session.advance(1000)
            save(phase)
    except BaseException:
        report = session.report()
        report.update(error=traceback.format_exc(), actions=actions, audio_trace=trace,
                      diagnostics=session.q.diagnostics() if session.q else '')
        (directory / 'error.json').write_text(json.dumps(report, indent=2) + '\n')
        raise
    finally:
        session.close()
        Debugger.step_over_breakpoint = previous
        if capture.exists():
            with wave.open(str(capture), 'rb') as audio:
                data = audio.readframes(audio.getnframes())
                result = dict(rate=audio.getframerate(), channels=audio.getnchannels(),
                              frames=audio.getnframes(), nonzero_bytes=sum(v != 0 for v in data))
                (directory / 'pcm.json').write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
