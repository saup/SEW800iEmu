"""Measure one complete original Greeting MIDI preview with the real host sink.

Normal40ms guest advances and LCD captures continue throughout playback. The
only added breakpoint is the native natural-completion callback, never a MIDI
packet producer. All media bytes are read through original local file APIs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import time
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from w800 import guest_ui
from w800.backend import ROOT
from w800.tests.test_menu_services import press
from w800.tools.trace_audio_hardware import ROUTE
from w800.tools.probe_midi_pacing import midi_events

NATURAL_COMPLETION = 0x44883658
MARKERS = ('W800_DSP_PCM_ACTIVITY', 'W800_DSP_PCM source=',
           'W800_DSP_SYNTH_EOF', 'W800_DSP_SYNTH_TAIL_DRAINED',
           'W800_DSP_SYNTH_COMPLETE', 'W800_DSP_PCM_END',
           'W800_COREAUDIO_FORMAT', 'W800_COREAUDIO_DEBUG')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'reports/midi-natural-coreaudio')
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    directory = args.output
    directory.mkdir(parents=True, exist_ok=True)
    session = guest_ui.OriginalUISession(default_theme=True, audio_output='coreaudio',
        progress=lambda message: print(message, flush=True))
    report, observations, windows, native = {}, [], [], []
    start = time.monotonic_ns()
    log = None
    cursor = 0
    last_log_observation = time.monotonic_ns()
    previous = guest_ui.CHECKPOINTS.get(NATURAL_COMPLETION)
    guest_ui.CHECKPOINTS[NATURAL_COMPLETION] = 'Original MIDI natural completion'

    def sample_log():
        nonlocal cursor, last_log_observation
        data = session.q.diagnostics()
        now = time.monotonic_ns()
        for line in data[cursor:].splitlines():
            if any(marker in line for marker in MARKERS):
                observations.append({'previous_observation_wall_ns': last_log_observation,
                                     'observed_wall_ns': now, 'line': line})
        cursor = len(data)
        last_log_observation = now
        return data

    def state():
        return {'wall_ns': time.monotonic_ns(),
                'virtual_ns': session.q.command('qom-get', {
                    'path': '/machine', 'property': 'virtual-time-ns'}),
                'native_ticks': session.word(0x4200f264)}

    try:
        session.start()
        log = (session.q.directory / 'stderr.log').open()
        path = session.files.stage(0, '/tpa/user/audio/Greeting.mid'.encode('utf-16le')+b'\0\0')
        fd = session.invoke(0x45105f48, path, 1, 0x1b6)
        if fd & 0x80000000:
            raise OSError(f'Original MIDI open failed: {fd:#x}')
        content = bytearray()
        try:
            for _ in range(3):
                ptr = session.files.buffer + 1024
                count = session.invoke(0x451063d4, fd, ptr, 8192)
                if not 0 <= count <= 8192:
                    raise OSError(f'Original MIDI read failed: {count:#x}')
                if not count:
                    break
                content.extend(session.d.read(ptr, count))
        finally:
            session.invoke(0x45106254, fd, 0)
        source = midi_events(bytes(content))
        source['sha256'] = hashlib.sha256(content).hexdigest()
        source['size'] = len(content)
        (directory / 'Greeting.mid').write_bytes(content)
        (directory / 'source-events.json').write_text(json.dumps(source, indent=2)+'\n')
        report['source'] = {key: value for key, value in source.items() if key != 'events'}
        report['source']['event_count'] = len(source['events'])
        report['source']['last_event_seconds'] = max(row['seconds'] for row in source['events'])
        report['source']['last_note_off_seconds'] = max(row['seconds'] for row in source['events']
            if row['status'] & 0xf0 == 0x80 or (row['status'] & 0xf0 == 0x90 and not row['data2']))
        original_registers = session.d.registers
        def registers():
            regs = original_registers()
            if regs[15] == NATURAL_COMPLETION:
                native.append({'wall_ns': time.monotonic_ns(), 'registers': regs[:4]})
            return regs
        session.d.registers = registers
        for key in ROUTE[:-1]:
            press(session, key)
        session.frame().save(str(directory / 'before-play.png'))
        sample_log()
        report['before_play'] = state()
        session.advance(100, (ROUTE[-1], True))
        session.frame()
        sample_log()
        report['after_play_down'] = state()
        session.advance(100, (ROUTE[-1], False))
        session.frame()
        sample_log()
        deadline = time.monotonic() + 90
        completion_seen = None
        last_progress = time.monotonic()
        while time.monotonic() < deadline:
            before = time.monotonic_ns()
            session.advance(40)
            frame = session.frame()
            after = time.monotonic_ns()
            data = sample_log()
            windows.append({'before_wall_ns': before, 'after_wall_ns': after})
            if time.monotonic() - last_progress >= 5:
                last_progress = time.monotonic()
                elapsed = (after-report['before_play']['wall_ns'])/1e9
                frame.save(str(directory / f'playing-{int(elapsed):02d}s.png'))
                print(json.dumps({'play_wall_seconds': elapsed, 'windows': len(windows),
                    'native_completions': len(native)}), flush=True)
            if 'W800_DSP_SYNTH_COMPLETE ' in data and completion_seen is None:
                completion_seen = time.monotonic()
                report['completion_observed'] = state()
                frame.save(str(directory / 'natural-completion.png'))
            if completion_seen is not None and native and time.monotonic()-completion_seen >= .5:
                break
        else:
            raise TimeoutError('Original MIDI did not complete within90 wall seconds')
        report['after_completion'] = state()
        report['natural_completion_callbacks'] = native
        # Leave the native preview after its first natural completion.
        press(session, 'back')
        for _ in range(10):
            session.advance(40)
            session.frame()
        sample_log()
        session.frame().save(str(directory / 'after-native-back.png'))
        if session.q.pressed_keys:
            raise AssertionError('A physical key remains held')
        report['result'] = 'PASS'
    except Exception as error:
        report['result'] = 'FAIL'
        report['error'] = repr(error)
    finally:
        report['session'] = session.report()
        report['observations'] = observations
        report['windows'] = windows
        report['host_run_seconds'] = (time.monotonic_ns()-start)/1e9
        if session.q:
            report['diagnostics_before_close'] = session.q.diagnostics()
        session.close()
        if log:
            log.seek(0)
            (directory / 'complete-diagnostics.log').write_text(log.read())
            log.close()
        if previous is None:
            guest_ui.CHECKPOINTS.pop(NATURAL_COMPLETION, None)
        else:
            guest_ui.CHECKPOINTS[NATURAL_COMPLETION] = previous
        (directory / 'result.json').write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps({'result':report['result'],'error':report.get('error'),
                          'native_completions':len(native),'windows':len(windows)}),flush=True)
    if report['result'] != 'PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
