"""Measure native movie clocks and host output without per-packet breakpoints."""
import argparse
import hashlib
import json
import os
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from w800.backend import ROOT
from w800.guest_ui import OriginalUISession
from w800.tests.test_menu_services import press


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', default='video-realtime-clocks')
    parser.add_argument('--seconds', type=int, default=45)
    parser.add_argument('--wav', action='store_true')
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    directory = ROOT / 'reports' / args.directory
    directory.mkdir(parents=True, exist_ok=True)
    session = OriginalUISession(default_theme=True, audio_output='coreaudio',
        audio_capture_path=directory / 'audio.wav' if args.wav else None,
        progress=lambda value: print(value, flush=True))
    rows = []
    log = None
    report = {}
    images = set()
    try:
        session.start()
        log = (session.q.directory / 'stderr.log').open()
        for key in ('select', 'down', 'left', 'select', 'down', 'select', 'down', 'select'):
            press(session, key)
        session.advance(1000)
        session.frame().save(str(directory / 'preview.png'))
        start = time.monotonic()
        for index in range(args.seconds * 25):
            if index == 0:
                session.advance(100, ('soft_left', True))
                session.advance(100, ('soft_left', False))
            else:
                session.advance(40)
            frame = session.frame()
            images.add(hashlib.sha256(frame.constBits().tobytes()).hexdigest())
            if index % 125 == 0:
                row = dict(index=index, wall=time.monotonic() - start,
                    virtual_ns=session.q.command('qom-get', {
                        'path': '/machine', 'property': 'virtual-time-ns'}),
                    tick=session.word(0x4200f264),
                    media_clock_ms=session.invoke(0x44a3ee98),
                    images=len(images))
                rows.append(row)
                print(json.dumps(row), flush=True)
                frame.save(str(directory / f'frame-{index:04}.png'))
            if 'AAC_RESOURCE operation=4 capacity=0' in session.q.diagnostics():
                report['natural_end'] = True
                break
        session.frame().save(str(directory / 'after.png'))
        report['wall_seconds'] = time.monotonic() - start
        report['before_back_diagnostics'] = session.q.diagnostics()
        press(session, 'back')
        session.advance(500)
        session.frame().save(str(directory / 'back.png'))
        report['session'] = session.report()
    except Exception as error:
        report['error'] = repr(error)
    finally:
        session.close()
        if log:
            (directory / 'diagnostics.log').write_text(log.read())
            log.close()
    report['rows'] = rows
    report['images'] = len(images)
    (directory / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k not in (
        'session', 'before_back_diagnostics', 'rows')}), flush=True)


if __name__ == '__main__':
    main()
