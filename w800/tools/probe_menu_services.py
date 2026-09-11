"""Record real firmware menus through physical QEMU keys.

Run with ``python -m w800.tools.probe_menu_services`` and enter one JSON
object per line, e.g. {"keys": ["right"], "label": "selected-icon"}.
Every observation includes the original LCD, new task messages, and registers.
No host network is used; each session owns private QEMU Unix sockets.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from w800.backend import ROOT
from w800.guest_ui import OriginalUISession
from w800 import guest_ui


TIMER_CALLS = (0x44a3ebf0, 0x44a3ec28, 0x44a3ec64, 0x44a3ed14)


def trace_calls(session, addresses, bucket):
    """Read original call arguments/callers without changing them."""
    original_registers = session.d.registers
    rows = session.provenance.setdefault(bucket, [])
    def registers():
        values = original_registers()
        pc = values[15]
        if pc in addresses:
            if bucket == 'mmi_delivery':
                tcb = session.word(0x4c041c68)
                native_pid = struct.unpack('<H', session.d.read(tcb + 2, 2))[0]
                if native_pid not in (0xdc, 0xf2):
                    return values
            row = {'pc': hex(pc), 'caller': hex(values[14]),
                   'arguments': [hex(value) for value in values[:4]],
                   'registers': [hex(value) for value in values],
                   'physical_key_events': session.q.key_events_sent,
                   'stack_pointer': hex(values[13])}
            if 0x4c000000 <= values[13] <= 0x4c7fff00:
                row['stack'] = [hex(value) for value in struct.unpack(
                    '<64I', session.d.read(values[13], 256))]
            if bucket in ('window_input', 'window_dispatch', 'mmi_delivery'):
                row['objects'] = {}
                for pointer in values[:2]:
                    if 0x4c000000 <= pointer <= 0x4c7fffc0:
                        row['objects'][hex(pointer)] = session.d.read(pointer, 64).hex()
            if bucket == 'window_dispatch':
                tcb = session.word(0x4c041c68)
                row['native_tcb'] = hex(tcb)
                row['native_pid'] = hex(struct.unpack('<H', session.d.read(tcb + 2, 2))[0])
            if bucket == 'camera_platform_calls':
                row['objects'] = {hex(pointer): session.d.read(pointer, 256).hex()
                                  for pointer in values[:8]
                                  if 0x4c000000 <= pointer <= 0x4c7fff00}
            if bucket == 'mmi_delivery':
                row['native_pid'] = hex(native_pid)
            rows.append(row)
        return values
    session.d.registers = registers
    for pc in addresses:
        guest_ui.CHECKPOINTS[pc] = 'Observed original ' + bucket
        session.d.breakpoint(pc, thumb=True)


def press(session, key):
    session.advance(120, (key, True))
    session.advance(450, (key, False))


def capture(session, directory, label, previous_messages=()):
    frame = session.frame()
    frame.save(str(directory / (label + '.png')))
    body = frame.copy(0, 20, 176, 175)
    messages = list(session.messages)
    # Find the longest surviving prefix overlap when the bounded log rotated.
    common = 0
    for count in range(min(len(previous_messages), len(messages)), 0, -1):
        if list(previous_messages[-count:]) == messages[:count]:
            common = count
            break
    report = {
        'label': label,
        'lcd_sha256': hashlib.sha256(frame.constBits().tobytes()).hexdigest(),
        'body_sha256': hashlib.sha256(body.constBits().tobytes()).hexdigest(),
        'caller_restored': session.ready,
        'held_keys': sorted(session.q.pressed_keys),
        'physical_key_events': session.q.key_events_sent,
        'registers': [hex(value) for value in session.d.registers()],
        # Calendar reads avoid the RTC interrupt-status register, whose
        # eventual hardware read/ack semantics must not affect observation.
        'rtc_calendar': {name: struct.unpack('<H', session.d.read(0xf9050000 + offset, 2))[0]
            for name, offset in (('second', 4), ('minute', 6), ('hour', 8),
                                 ('day', 12), ('month', 14), ('year', 16), ('century', 18))},
        'rtc_alarm_registers': {hex(offset): struct.unpack('<H',
            session.d.read(0xf9050000 + offset, 2))[0]
            for offset in (0x16, 0x18, 0x1e, 0x2a, 0x2c, 0x2e,
                           0x30, 0x32, 0x34, 0x36, 0x38, 0x3a, 0x58, 0x5a, 0x5c)},
        'new_messages': messages[common:],
        'report': session.report(),
    }
    (directory / (label + '.json')).write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: value for key, value in report.items() if key != 'report'}), flush=True)
    return messages


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'reports/menu-services')
    parser.add_argument('--theme', action='store_true')
    parser.add_argument('--audio-wav', type=Path)
    parser.add_argument('--startup', action='store_true')
    parser.add_argument('--subset', action='store_true', help='Compare earlier component subset startup')
    parser.add_argument('--trace-timers', action='store_true')
    parser.add_argument('--trace-rtc', action='store_true')
    parser.add_argument('--trace-camera', action='store_true',
                        help='Observe original camera platform status and callers')
    parser.add_argument('--trace-waits', action='store_true')
    parser.add_argument('--trace-operator', action='store_true')
    parser.add_argument('--trace-input', action='store_true')
    parser.add_argument('--trace-window-input', action='store_true')
    parser.add_argument('--trace-window-dispatch', action='store_true')
    parser.add_argument('--trace-key-handler', action='store_true')
    parser.add_argument('--trace-mmi-delivery', action='store_true')
    parser.add_argument('--hardware-log', action='store_true')
    parser.add_argument('--trace-status-query', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    session = OriginalUISession(start_screen='startup' if args.startup else 'menu',
                                default_theme=args.theme, full_services=not args.subset,
                                audio_capture_path=args.audio_wav)
    try:
        session.start()
        if args.hardware_log:
            hardware_log = (args.output / 'hardware.log').resolve()
            session.q.command('human-monitor-command', {
                'command-line': f'logfile {hardware_log}'})
            session.q.command('human-monitor-command', {
                'command-line': 'log unimp,guest_errors'})
        if args.trace_timers:
            trace_calls(session, TIMER_CALLS, 'timer_requests')
        if args.trace_rtc:
            trace_calls(session, (0x449115d4, 0x44911734, 0x449118d8,
                                  0x44910cdc), 'rtc_calls')
        if args.trace_camera:
            trace_calls(session, (0x44c91210, 0x44f78df2, 0x44f78df4,
                                  0x44f78e0a, 0x44f78e0c, 0x4494562c,
                                  0x4480d0b4, 0x4480d0ce, 0x4480d0e2,
                                  0x4480ea74, 0x4480eae6, 0x4480eaf6,
                                  0x4480eb08), 'camera_platform_calls')
        if args.trace_waits:
            trace_calls(session, (0x44c902bc, 0x448ca366), 'receive_calls')
        if args.trace_operator:
            trace_calls(session, (0x44be0e64, 0x44be0e74, 0x44be0edc,
                                  0x44be1808, 0x44be183c, 0x44be1852,
                                  0x44be186a, 0x44be189a, 0x44d5c278), 'operator_calls')
        if args.trace_input:
            trace_calls(session, (0x44a4f7e0, 0x44a4fe86, 0x44a4fee4,
                                  0x44e6e592, 0x44e6e598, 0x44e6e5b4,
                                  0x44e6e5c2, 0x44e6e5e6, 0x44e6e604,
                                  0x44e6e60c, 0x44e6ec90), 'input_calls')
        if args.trace_window_input:
            trace_calls(session, (0x44986efc, 0x44794878, 0x44794894,
                                  0x44794950, 0x44794074, 0x44794500), 'window_input')
        if args.trace_window_dispatch:
            trace_calls(session, (0x448c9ace, 0x448c9ae6, 0x448c9b16,
                                  0x448c9b20, 0x448c9b40), 'window_dispatch')
        if args.trace_key_handler:
            trace_calls(session, (0x44793ca4, 0x44793d54, 0x44d55a3e,
                                  0x44d55a4c, 0x44d55a56, 0x44d55a92,
                                  0x44f0eb8a, 0x44f0ebbc), 'window_input')
        if args.trace_mmi_delivery:
            trace_calls(session, (0x44749746, 0x448ca366, 0x4474af96), 'mmi_delivery')
        if args.trace_status_query:
            trace_calls(session, (0x44e92dfa, 0x44964f14, 0x44964f34,
                                  0x44964f44, 0x44964f48, 0x44a11a80,
                                  0x44a11a8a, 0x44a11a8e, 0x4489465c,
                                  0x448946a6, 0x44894776), 'window_input')
        previous = capture(session, args.output, '00-main-menu')
        for index, line in enumerate(sys.stdin, 1):
            command = json.loads(line)
            if command.get('close'):
                break
            for key in command.get('keys', []):
                press(session, key)
            for _ in range(command.get('seconds', 0)):
                session.advance(1000)
            label = command.get('label', f'{index:02d}-frame')
            if not label or Path(label).name != label:
                raise ValueError('Observation label must be one filename component')
            previous = capture(session, args.output, label, previous)
    except Exception as error:
        report = session.report() if session.q else {}
        report['error'] = repr(error)
        if session.d:
            try:
                report['registers'] = [hex(value) for value in session.d.registers()]
            except Exception:
                pass
        (args.output / 'error.json').write_text(json.dumps(report, indent=2) + '\n')
        raise
    finally:
        session.close()


if __name__ == '__main__':
    main()
