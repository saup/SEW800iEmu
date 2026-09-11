"""Measure the live performance switch while original Java gameplay runs."""
import json
import os
import time
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from w800.backend import ROOT
from w800.guest_ui import OriginalUISession
from w800.ui_gui import UIWindow
from w800.tests import test_java_provisioning as native_games
from w800.tests.test_coreaudio_debug_grace import stats
from w800.tests.test_menu_services import press
from w800.tools.trace_audio_hardware import ROUTE

app = QApplication.instance() or QApplication([])


class GameBoostTests(unittest.TestCase):
    press = native_games.NativeGamesTests.press
    expect = native_games.NativeGamesTests.expect

    def test_live_switch_original_game_input_and_observation_cost(self):
        self.keys = []
        self.artifact_prefix = 'game-boost'
        report = {'measurements': []}
        with OriginalUISession(default_theme=True, audio_output='none') as session:
            self.session = session
            process = session.q.process
            try:
                for key in ('select', 'up', 'right', 'select', 'select', 'down', 'select'):
                    self.press(key)
                self.expect('quadra_new_game')
                self.press('select')
                self.expect('quadra_score')
                for label, enabled, window in (('standard', False, 40),
                                                ('boost_16', True, 16),
                                                ('standard_repeat', False, 40),
                                                ('boost_25', True, 25),
                                                ('standard_final', False, 40),
                                                ('fewer_stops_only', True, 40),
                                                ('boost_running_display', True, 50)):
                    session.set_game_boost(enabled)
                    checkpoints = session.observed_checkpoints
                    completions = session.frames_completed
                    virtual = session.q.command('qom-get', {'path': '/machine', 'property': 'virtual-time-ns'})
                    frames, changes, previous = 0, 0, None
                    def observe_frame():
                        nonlocal frames, changes, previous
                        frame = session.frame()
                        pixels = frame.constBits().tobytes()
                        changes += previous is not None and pixels != previous
                        previous = pixels
                        frames += 1
                        return frame
                    started = time.monotonic()
                    while time.monotonic() - started < 3:
                        session.advance(window, frame_callback=observe_frame
                                        if label == 'boost_running_display' else None)
                        frame = observe_frame()
                    wall = time.monotonic() - started
                    virtual = (session.q.command('qom-get', {'path': '/machine', 'property': 'virtual-time-ns'}) - virtual) / 1e9
                    sample = dict(mode=label, wall_seconds=wall, virtual_seconds=virtual,
                                  frames=frames, changed_frames=changes,
                                  sampled_fps=frames / wall, changed_fps=changes / wall,
                                  checkpoints=session.observed_checkpoints - checkpoints,
                                  lcd_diagnostic_stops=session.frames_completed - completions)
                    report['measurements'].append(sample)
                    frame.save(str(ROOT / f'reports/game-boost-{label}.png'))
                    self.assertGreater(changes, 0, 'Original Java game stopped rendering')
                    self.assertGreater(virtual, 0)
                    if enabled:
                        self.assertEqual(sample['lcd_diagnostic_stops'], 0)
                    else:
                        self.assertGreater(sample['lcd_diagnostic_stops'], 0)
                    self.expect('quadra_score')
                self.press('6')
                self.press('5')
                self.expect('quadra_score')
                session.set_game_boost(False)
                self.assertEqual(session.idle_window_ms, 40)
                completions = session.frames_completed
                self.press('back')
                self.assertGreater(session.frames_completed, completions)
                self.press('soft_right')
                self.press('up')  # Quit retains QuadraPop selection; use the unselected label fixture.
                self.expect('games_quadra')
                self.assertIs(session.q.process, process)
                self.assertFalse(session.q.pressed_keys)
                self.assertEqual(bytes.fromhex(session.d.packet('g')), session.files.saved)
                self.assertTrue(session.report()['source_unchanged'])
                report['passed'] = True
            finally:
                report['session'] = session.report()
                (ROOT / 'reports/game-boost.json').write_text(json.dumps(report, indent=2) + '\n')
        self.assertIsNotNone(process.poll())


class GameBoostGuiTests(unittest.TestCase):
    def test_toggle_while_paused_keeps_existing_worker_and_screen(self):
        window = UIWindow(audio_output='none')
        window.show()

        def wait(condition):
            deadline = time.monotonic() + 60
            while not condition():
                app.processEvents()
                if window.failure:
                    self.fail(window.failure)
                if time.monotonic() > deadline:
                    self.fail('Timed out waiting for the original UI worker')
                time.sleep(.01)
            app.processEvents()

        try:
            wait(lambda: window.ready)
            worker = window.worker
            window.toggle_pause()
            wait(lambda: window.paused)
            paused = window.phone.frame.copy()
            window.game_boost.setChecked(True)
            QTest.qWait(200)
            self.assertEqual(window.phone.frame, paused)
            self.assertIs(window.worker, worker)
            window.toggle_pause()
            wait(lambda: not window.paused)
            window.grab().save(str(ROOT / 'reports/game-boost-gui.png'))
        finally:
            window.close()
            wait(lambda: window.worker is None)
        self.assertTrue(window.last_report['game_boost']['enabled'])
        self.assertEqual(window.last_report['game_boost']['clock_multiplier'], 1)
        self.assertTrue(window.last_report['source_unchanged'])


class GameBoostAudioTests(unittest.TestCase):
    def test_original_midi_host_audio_and_pause_with_running_display(self):
        observations = []
        report = {}
        with OriginalUISession(audio_output='coreaudio', game_boost=True) as session:
            try:
                def observe():
                    observations.append(session.q.command('query-status')['status'])
                    session.frame()
                for key in ROUTE:
                    press(session, key)
                for _ in range(24):
                    session.advance(session.idle_window_ms, frame_callback=observe)
                time.sleep(.25)
                paused = stats(session.q.diagnostics())[-1]
                self.assertEqual(paused['reason'], 'debug-timeout')
                self.assertGreater(paused['delivered_frames'], 0)
                self.assertGreater(paused['hal_stops'], 0)
                time.sleep(.25)
                self.assertEqual(stats(session.q.diagnostics())[-1], paused)
                for _ in range(24):
                    session.advance(session.idle_window_ms, frame_callback=observe)
                time.sleep(.25)
                resumed = stats(session.q.diagnostics())[-1]
                self.assertGreater(resumed['delivered_frames'], paused['delivered_frames'])
                self.assertGreater(resumed['hal_starts'], paused['hal_starts'])
                self.assertIn('running', observations)
                press(session, 'back')
                session.advance(500)
                time.sleep(.25)
                stopped = stats(session.q.diagnostics())[-1]
                self.assertIn(stopped['reason'], ('stop', 'close'))
                self.assertFalse(session.q.pressed_keys)
                self.assertTrue(session.report()['source_unchanged'])
                report.update(passed=True, paused=paused, resumed=resumed, stopped=stopped)
            finally:
                report.update(session=session.report(), running_display_samples=observations.count('running'))
                (ROOT / 'reports/game-boost-audio.json').write_text(json.dumps(report, indent=2) + '\n')
