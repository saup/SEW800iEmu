"""Observe the original DemoTour natural ending in a private offline QEMU."""
import argparse
import hashlib
import json
import os
import struct
import time
import wave

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from w800 import guest_ui
from w800.backend import ROOT
from w800.guest_ui import OriginalUISession
from w800.tests.test_menu_services import press
from w800.tools.analyze_aac_stream import native_packet


POINTS = {
    0x447367a8: 'DSP logical send',
    0x4488d5d8: 'Original AAC indication callback',
    0x4488de38: 'Original stream4 EOF writer',
    0x4488d660: 'Original AAC control',
    0x4488cb64: 'Original AAC status consumer',
    0x4488c910: 'Original audio client notification',
    0x44f4abc8: 'Original viewer callback',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report-directory', default='video-natural-eof')
    parser.add_argument('--default-theme', action=argparse.BooleanOptionalAction,
                        default=True)
    args = parser.parse_args()
    directory = ROOT / 'reports' / args.report_directory
    directory.mkdir(parents=True, exist_ok=True)
    capture = directory / 'audio.wav'
    previous = guest_ui.CHECKPOINTS.copy()
    session = OriginalUISession(default_theme=args.default_theme,
                                audio_capture_path=capture,
                                progress=lambda message: print(message, flush=True))
    events, snapshots = [], []
    phase = 'startup'
    start = time.monotonic()

    def snapshot(name):
        frame = session.frame()
        frame.save(str(directory / (name + '.png')))
        snapshots.append(dict(name=name, wall_seconds=time.monotonic() - start,
                              virtual_ns=session.q.command('qom-get', {
                                  'path': '/machine', 'property': 'virtual-time-ns'}),
                              sha256=hashlib.sha256(bytes(frame.constBits())).hexdigest(),
                              lcd_completions=session.frames_completed))

    try:
        session.start()
        original_registers = session.d.registers

        def registers():
            values = original_registers()
            pc = values[15]
            if pc not in POINTS:
                return values
            if pc == 0x447367a8 and values[0] not in (18, 20):
                return values
            row = dict(point=POINTS[pc], phase=phase,
                       wall_seconds=time.monotonic() - start,
                       registers=[hex(value) for value in values])
            if pc in (0x447367a8, 0x4488d5d8):
                opcode = struct.unpack('<H', session.d.read(values[1], 2))[0]
                size = {0x0e00: 48, 0x0e02: 104, 0x0e04: 4,
                        0x0e01: 4, 0x0e03: 2, 0x0e05: 4,
                        0x0e06: 4}.get(opcode, 24)
                if opcode == 0x0804:
                    size = 4 + 2 * struct.unpack('<H', session.d.read(values[1] + 2, 2))[0]
                    packet = session.d.read(values[1], size)
                    row['packet'] = packet.hex()
                    try:
                        row['frame'], _ = native_packet(packet)
                    except ValueError as error:
                        row['parse_error'] = str(error)
                else:
                    row['packet'] = session.d.read(values[1], size).hex()
                    row['capture_bytes'] = size
            elif pc == 0x4488cb64:
                row['status_packet'] = session.d.read(session.word(values[0] + 4), 4).hex()
            events.append(row)
            return values

        session.d.registers = registers
        guest_ui.CHECKPOINTS.update(POINTS)
        for pc in POINTS:
            session.d.breakpoint(pc, thumb=True)
        for key in ('select', 'down', 'left', 'select', 'down', 'select', 'down', 'select'):
            press(session, key)
        for _ in range(3):
            session.advance(500)
        snapshot('00-preview')
        phase = 'play'
        press(session, 'soft_left')
        snapshot('01-play')
        for second in range(1, 56):
            phase = f'play-{second:02d}'
            session.advance(1000)
            if second % 5 == 0 or second in (38, 39, 40, 41, 42):
                snapshot(phase)
                print(json.dumps(dict(phase=phase, events=len(events),
                                      lcd=session.frames_completed)), flush=True)
        phase = 'back-after-natural-end'
        press(session, 'back')
        session.advance(500)
        snapshot(phase)
        phase = 'back-to-videos'
        press(session, 'back')
        session.advance(500)
        snapshot(phase)
        phase = 'navigation-after-end'
        press(session, 'up')
        snapshot(phase)
        report = session.report()
        report['diagnostics'] = session.q.diagnostics()
    except Exception as error:
        report = session.report()
        report['error'] = repr(error)
        if session.q:
            report['diagnostics'] = session.q.diagnostics()
    finally:
        session.close()
        guest_ui.CHECKPOINTS.clear()
        guest_ui.CHECKPOINTS.update(previous)
    if capture.exists():
        with wave.open(str(capture)) as audio:
            report['audio_capture'] = dict(frames=audio.getnframes(),
                                          rate=audio.getframerate(),
                                          channels=audio.getnchannels())
    report.update(eof_trace=events, snapshots=snapshots,
                  default_theme=args.default_theme)
    (directory / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(dict(events=len(events), error=report.get('error'),
                          capture=report.get('audio_capture'))), flush=True)


if __name__ == '__main__':
    main()
