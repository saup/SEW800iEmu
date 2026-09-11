"""Record original Organizer functions using only physical keypad events.

One JSON action per stdin line: keys, wait_ms, label; optional hold_ms/up_ms.
Every capture records the complete action history and native diagnostics.
No host audio, network, guest data writes, or replacement application UI.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication

from w800.backend import ROOT
from w800.guest_ui import OriginalUISession
from w800 import guest_ui
from w800.guest_files import FilesystemSession


def trace_task_dates(session):
    """Observe original task date conversion and save/read ownership."""
    checkpoints = {
        0x44bb0a54: 'CheckToDo entry',
        0x44bb0a8e: 'Current time acquired',
        0x44bb0aca: 'CheckToDo after UTC conversion',
        0x44bb0ada: 'CheckToDo save returned',
        0x44bb1d64: 'Task information view',
        0x44bb2096: 'Format completion time',
        0x44bb1714: 'Convert local date to UTC',
        0x44bb1732: 'Timezone getter returned',
        0x44bb174c: 'Daylight getter returned',
        0x44bb1786: 'UTC conversion completed',
        0x44bb16dc: 'Convert UTC date to local',
        0x44bb16fc: 'Timezone getter for read returned',
        0x44e34338: 'Apply native UTC/local offset',
        0x44e343f4: 'Native offset applied',
        0x450c0804: 'Task save requester',
    }
    original = session.d.registers
    rows = session.provenance.setdefault('task_date_trace', [])
    def registers():
        values = original()
        if values[15] in checkpoints:
            if len(rows) >= 1000:
                raise RuntimeError('Task date observation bound exceeded')
            row = {'point': checkpoints[values[15]],
                   'registers': [hex(value) for value in values], 'objects': {}}
            for pointer in tuple(values[:8]) + (values[13],):
                if 0x4c000000 <= pointer <= 0x4c7fff80:
                    row['objects'][hex(pointer)] = session.d.read(pointer, 128).hex()
            rows.append(row)
        return values
    session.d.registers = registers
    for pc, description in checkpoints.items():
        guest_ui.CHECKPOINTS[pc] = 'Observed original ' + description
        session.d.breakpoint(pc, thumb=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path,
                        default=ROOT / 'reports/organizer-functions')
    parser.add_argument('--trace-task-dates', action='store_true')
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    actions = []
    session = OriginalUISession(start_screen='menu', default_theme=True)
    previous = []
    started = time.monotonic()

    def wait(milliseconds):
        if not 0 <= milliseconds <= 60000:
            raise ValueError('Each wait must be between zero and 60 seconds')
        while milliseconds:
            window = min(milliseconds, 1000)
            session.advance(max(10, window))
            milliseconds -= window

    def capture(label):
        nonlocal previous
        if not label or Path(label).name != label:
            raise ValueError('Label must be one filename component')
        frame = session.frame()
        frame.save(str(args.directory / (label + '.png')))
        messages = list(session.messages)
        common = 0
        for count in range(min(len(previous), len(messages)), 0, -1):
            if previous[-count:] == messages[:count]:
                common = count
                break
        body = frame.copy(0, 20, 176, 175)
        result = {
            'label': label, 'elapsed_wall_seconds': time.monotonic() - started,
            'physical_actions': actions,
            'physical_key_events': session.q.key_events_sent,
            'held_keys': sorted(session.q.pressed_keys),
            'body_sha256': hashlib.sha256(body.constBits().tobytes()).hexdigest(),
            'native_rtc': {
                name: struct.unpack('<H', session.d.read(0xf9050000 + offset, 2))[0]
                for name, offset in (('second', 4), ('minute', 6), ('hour', 8),
                                     ('day', 12), ('month', 14), ('year', 16), ('century', 18))},
            'new_messages': messages[common:], 'session': session.report(),
        }
        (args.directory / (label + '.json')).write_text(json.dumps(result, indent=2) + '\n')
        previous = messages
        print(json.dumps({key: result[key] for key in
                          ('label', 'elapsed_wall_seconds', 'body_sha256', 'held_keys', 'new_messages')}), flush=True)

    try:
        session.start()
        session.advance(1000)  # Finish the production deferred application startup.
        if args.trace_task_dates:
            trace_task_dates(session)
        capture('00-main-menu')
        for number, line in enumerate(sys.stdin, 1):
            action = json.loads(line)
            if action.get('close'):
                break
            if number > 200 or len(action.get('keys', [])) > 100:
                raise ValueError('Probe action bound exceeded')
            actions.append(action)
            for key in action.get('keys', []):
                session.advance(120, (key, True))
                wait(action.get('hold_ms', 120) - 120)
                session.advance(action.get('up_ms', 450), (key, False))
            wait(action.get('wait_ms', 0))
            for phone_directory in action.get('directories', []):
                try:
                    entries = FilesystemSession.list_directory(session, phone_directory)
                except OSError as error:
                    entries = {'path': phone_directory, 'inspection_error': str(error)}
                output = args.directory / (phone_directory.strip('/').replace('/', '-') + '-files.json')
                output.write_text(json.dumps(entries, indent=2) + '\n')
            for phone_file in action.get('read_files', []):
                content = FilesystemSession.read_file(session, phone_file, limit=1048576)
                output = args.directory / (phone_file.strip('/').replace('/', '-') + '.bin')
                output.write_bytes(content)
            capture(action.get('label', f'{number:02d}-frame'))
    except Exception as error:
        (args.directory / 'error.json').write_text(json.dumps({
            'error': repr(error), 'actions': actions,
            'session': session.report() if session.q else {},
        }, indent=2) + '\n')
        raise
    finally:
        session.close()


if __name__ == '__main__':
    main()
