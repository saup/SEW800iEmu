"""Check native MIDI CoreAudio pause/resume and host-device teardown."""
import hashlib
import json
import os
import re
import sys
import time
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from w800.backend import ROOT
from w800.guest_files import ANCHOR
from w800.guest_ui import OriginalUISession
from w800.tests.test_menu_services import press
from w800.tools.trace_audio_hardware import ROUTE


def stats(text):
    return [{key: value if key == 'reason' else int(value)
             for key, value in re.findall(r'(\w+)=([^\s]+)', line)}
            for line in text.splitlines() if 'W800_COREAUDIO_DEBUG ' in line]


@unittest.skipUnless(sys.platform == 'darwin', 'CoreAudio is macOS-only')
class CoreAudioDebugGraceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_original_midi_pause_resume_and_native_stop(self):
        session = OriginalUISession(default_theme=False, audio_output='coreaudio')
        report = {'qemu_sha256': hashlib.sha256(
            (ROOT / 'build/qemu-system-arm').read_bytes()).hexdigest()}
        directory = ROOT / 'reports/coreaudio-pause-test'
        directory.mkdir(parents=True, exist_ok=True)
        diagnostics_fd = None
        try:
            session.start()
            report['qemu_sha256'] = session.q.binary_sha256
            report['qemu_binary'] = session.q.binary_path
            diagnostics_fd = (session.q.directory / 'stderr.log').open()
            for key in ROUTE:
                press(session, key)
            for _ in range(30):
                session.advance(40)
                session.frame()
            self.assertEqual(session.d.registers()[15], ANCHOR)
            self.assertEqual(session.q.command('query-status')['status'], 'debug')
            self.assertFalse(session.q.pressed_keys)
            before = len(stats(session.q.diagnostics()))
            pause_ns = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
            time.sleep(.250)  # Real UI Pause leaves the guest at ANCHOR.
            first = stats(session.q.diagnostics())
            self.assertGreater(len(first), before, 'DEBUG grace did not expire')
            paused = first[-1]
            self.assertEqual(paused['reason'], 'debug-timeout')
            self.assertGreater(paused['hal_stops'], 0)
            report['pause'] = paused
            report['pause_observed_delay_ms'] = (paused['realtime_ns'] - pause_ns) / 1e6
            # The 20 ms timer is not an upper bound on host scheduling or the
            # synchronous HAL call. The tested 250 ms pause must have stopped.
            self.assertLess(report['pause_observed_delay_ms'], 250)
            time.sleep(.250)
            self.assertEqual(stats(session.q.diagnostics()), first,
                             'Paused VM unexpectedly re-enabled the output')
            for _ in range(30):
                session.advance(40)
                session.frame()
            time.sleep(.250)
            resumed = stats(session.q.diagnostics())[-1]
            self.assertEqual(resumed['reason'], 'debug-timeout')
            self.assertGreater(resumed['hal_starts'], paused['hal_starts'])
            self.assertGreater(resumed['delivered_frames'], paused['delivered_frames'])
            report['resumed_then_paused'] = resumed
            press(session, 'back')  # Original preview Stop/Back lifecycle.
            for _ in range(20):
                session.advance(40)
            time.sleep(.250)
            diagnostics = session.q.diagnostics()
            self.assertTrue(any(int(count) > 0 for count in re.findall(
                r'W800_DSP_PCM_ACTIVITY running=0 writer=1 frames=(\d+)', diagnostics)))
            stopped = stats(diagnostics)[-1]
            self.assertIn(stopped['reason'], ('stop', 'close'))
            report['native_stop'] = stopped
            self.assertFalse(session.q.pressed_keys)
            report['firmware'] = session.report()
            self.assertTrue(report['firmware']['source_unchanged'])
            formats = [dict(re.findall(r'(\w+)=([^\s]+)', line))
                       for line in diagnostics.splitlines()
                       if 'W800_COREAUDIO_FORMAT ' in line]
            self.assertTrue(formats)
            self.assertTrue(all(row['requested_hz'] == row['actual_hz'] for row in formats))
            report['formats'] = formats
            time.sleep(.250)
            session.close()
            diagnostics_fd.seek(0)
            complete = diagnostics_fd.read()
            closed = stats(complete)[-1]
            self.assertEqual(closed['reason'], 'close')
            self.assertEqual(closed['delivered_frames'], stopped['delivered_frames'])
            self.assertEqual(closed['callbacks'], stopped['callbacks'])
            report['close'] = closed
            report['result'] = 'PASS'
        except BaseException as error:
            report['result'] = 'FAIL'
            report['error'] = repr(error)
            raise
        finally:
            session.close()
            if diagnostics_fd:
                diagnostics_fd.seek(0)
                complete = diagnostics_fd.read()
                diagnostics_fd.close()
                (directory / 'diagnostics.log').write_text(complete)
            (directory / 'result.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    unittest.main()
