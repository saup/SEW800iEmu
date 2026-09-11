"""Capture the original SL audio-state reply before MMI startup."""
import json
from pathlib import Path
import struct
from w800.guest_files import FilesystemSession


def main():
    report = {'replies': []}
    session = FilesystemSession()
    path = Path('w800/reports/microphone-sl-inactive.json')
    try:
        session.start()
        report['qemu_sha256'] = session.q.binary_sha256
        report['sl_pid'] = hex(struct.unpack('<I', session.d.read(0x4c04c67c, 4))[0])
        native_run = session.d.run
        def run():
            response = native_run()
            registers = session.d.registers()
            while registers[15] == 0x44948dc8:
                report['replies'].append({'registers': [hex(v) for v in registers],
                    'payload': session.d.read(registers[0], 24).hex()})
                session.d.step_over_breakpoint(0x44948dc8, thumb=True)
                response = native_run()
                registers = session.d.registers()
            return response
        session.d.run = run
        session.d.breakpoint(0x44948dc8, thumb=True)
        request = session.files.stage(0, bytes(8))
        first = session.files.stage(32, bytes(6))
        second = session.files.stage(64, bytes(6))
        reason = session.files.stage(96, b'\xee')
        report['result'] = session.files.call(0x44948d70, request, first, second, reason)
        report['record1'] = session.d.read(first, 6).hex()
        report['record2'] = session.d.read(second, 6).hex()
        report['reason'] = session.d.read(reason, 1).hex()
    finally:
        report['diagnostics'] = session.q.diagnostics() if session.q else ''
        session.close()
        path.write_text(json.dumps(report, indent=2) + '\n')
    print(report)


if __name__ == '__main__':
    main()
