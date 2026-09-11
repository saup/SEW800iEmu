"""Opt-in real host camera through original CameraBook/CAMIF/DMA; private VM."""
import argparse
import json
import os
from pathlib import Path
import struct
import time
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from w800.backend import ROOT
from w800.guest_ui import OriginalUISession
from w800.tools.probe_menu_services import capture, press


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', default='camera-host-native')
    parser.add_argument('--seconds', type=int, default=8)
    args = parser.parse_args()
    directory = ROOT / 'reports' / args.directory
    directory.mkdir(parents=True, exist_ok=True)
    session = OriginalUISession(default_theme=True, host_camera=True)
    report = {'samples': []}
    process = None
    try:
        session.start()
        process = session.q.process
        session.q.command('human-monitor-command', {'command-line': f'logfile {directory / "hardware.log"}'})
        session.q.command('human-monitor-command', {'command-line': 'log unimp,guest_errors'})
        report['status_before_open'] = session.q.command('qom-get', {'path': '/machine', 'property': 'camera-host-status'})
        for key in ('select', 'left', 'select'): press(session, key)
        previous = capture(session, directory, '00-camera-open')
        start = time.monotonic()
        for index in range(args.seconds):
            session.advance(1000)
            sample = {'elapsed_wall': time.monotonic() - start}
            for name in ('camera-host-status', 'camera-frames', 'camera-paused'):
                sample[name] = session.q.command('qom-get', {'path': '/machine', 'property': name})
            sample['receiver'] = [hex(x) for x in struct.unpack('<12I', session.d.read(0xf7000200,48))]
            sample['dma'] = [hex(x) for x in struct.unpack('<8I', session.d.read(0xf2000120,32))]
            report['samples'].append(sample)
            print(json.dumps(sample), flush=True)
            previous = capture(session, directory, f'{index+1:02d}-camera', previous)
        session.q.command('qom-set', {'path': '/machine', 'property': 'camera-paused', 'value': True})
        time.sleep(.3)
        report['paused_host_status'] = session.q.command('qom-get', {'path': '/machine', 'property': 'camera-host-status'})
        session.q.command('qom-set', {'path': '/machine', 'property': 'camera-paused', 'value': False})
        session.advance(1000)
        session.advance(500)
        previous = capture(session, directory, '20-resumed', previous)
        press(session, 'back')
        session.advance(1000)
        report['status_after_back'] = session.q.command('qom-get', {'path': '/machine', 'property': 'camera-host-status'})
        capture(session, directory, '21-back', previous)
        report['original'] = session.report()
    except Exception as error:
        report['error'] = repr(error)
        if session.q: report['original'] = session.report()
        raise
    finally:
        if session.q: report['diagnostics'] = session.q.diagnostics()
        session.close()
        report['owned_vm_closed'] = process is None or process.poll() is not None
        (directory / 'result.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__': main()
