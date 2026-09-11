"""Read-only wall/virtual/native pacing and display sampling in native DemoTour."""
import collections
import hashlib
import json
import os
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from w800.guest_ui import OriginalUISession
from w800.backend import ROOT
from w800.tests.test_menu_services import press


def main():
    directory = ROOT / 'reports/video-pacing'
    directory.mkdir(parents=True, exist_ok=True)
    session = OriginalUISession(default_theme=False,
        audio_capture_path=directory / 'audio.wav',
        progress=lambda text: print(text, flush=True))
    rows, batches = [], []
    counts, costs, points = collections.Counter(), collections.Counter(), collections.Counter()

    def state():
        return dict(virtual_ns=session.q.command('qom-get', {
            'path': '/machine', 'property': 'virtual-time-ns'}),
            native_ticks=session.word(0x4200f264),
            lcd_completions=session.frames_completed)

    def batch(name, interval, repeats, capture):
        initial = state()
        start = time.monotonic()
        previous_hash = None
        changes = 0
        for index in range(repeats):
            before = state()
            counts.clear(); costs.clear(); points.clear()
            wall = time.monotonic()
            session.advance(interval)
            advance_wall = time.monotonic() - wall
            frame_wall, frame_hash = 0, None
            if capture:
                wall = time.monotonic()
                frame = session.frame()
                frame_wall = time.monotonic() - wall
                frame_hash = hashlib.sha256(bytes(frame.constBits())).hexdigest()
                if previous_hash is not None and frame_hash != previous_hash:
                    changes += 1
                previous_hash = frame_hash
                if index in (0, repeats - 1):
                    frame.save(str(directory / f'{name}-{index:03}.png'))
            measured = dict(counts=dict(counts), costs=dict(costs), points=dict(points))
            after = state()
            rows.append(dict(batch=name, index=index, interval_ms=interval,
                advance_wall=advance_wall, frame_wall=frame_wall, frame_hash=frame_hash,
                before=before, after=after, **measured))
        final = state()
        result = dict(name=name, interval_ms=interval, repeats=repeats,
                      captures=capture, initial=initial, final=final,
                      wall_seconds=time.monotonic()-start, sampled_frame_changes=changes)
        batches.append(result)
        print(json.dumps(result), flush=True)

    try:
        session.start()
        original_packet = session.d.packet
        original_registers = session.d.registers
        original_qmp = session.q.command

        def packet(command):
            label = 'rsp:' + command[0]
            start = time.monotonic()
            try:
                return original_packet(command)
            finally:
                counts[label] += 1
                costs[label] += time.monotonic() - start

        def registers():
            result = original_registers()
            points[hex(result[15])] += 1
            return result

        def qmp(command, arguments=None):
            label = 'qmp:' + command
            start = time.monotonic()
            try:
                return original_qmp(command, arguments)
            finally:
                counts[label] += 1
                costs[label] += time.monotonic()-start

        session.d.packet = packet
        session.d.registers = registers
        session.q.command = qmp
        press(session, 'select')
        batch('menu-100', 100, 20, True)
        batch('menu-1000', 1000, 3, False)
        for key in ('down', 'left', 'select', 'down', 'select', 'down', 'select'):
            press(session, key)
        session.advance(1000)
        press(session, 'soft_left')
        batch('video-100', 100, 50, True)
        batch('video-20', 20, 150, True)
        batch('video-1000', 1000, 4, False)
        batch('video-50', 50, 60, True)
        press(session, 'back')
        report=session.report()
        report['diagnostics']=session.q.diagnostics()
    except Exception as error:
        report=session.report()
        report['error']=repr(error)
    finally:
        session.close()
    report.update(pacing_batches=batches,pacing_rows=rows)
    (directory/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(dict(error=report.get('error'),batches=len(batches))),flush=True)


if __name__=='__main__':
    main()
