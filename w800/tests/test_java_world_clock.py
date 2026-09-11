"""Original World Clock installation, city navigation, exit and relaunch."""
import hashlib
import json
import os
from pathlib import Path
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from w800 import guest_ui
from w800.backend import ROOT

FIXTURES = json.loads((Path(__file__).parent / 'fixtures/java-world-clock.json').read_text())


@unittest.skipUnless((ROOT / 'build/qemu-system-arm').exists(), 'Local QEMU build required')
class WorldClockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def press(self, key):
        self.keys.append(key)
        self.session.advance(120, (key, True))
        self.session.advance(450, (key, False))

    def expect(self, label, suffix=''):
        row = FIXTURES['regions'][label]
        x, y, width, height = row['rect']
        foreground = ((lambda rgb: max(rgb) < 128) if row['mask'] == 'dark'
                      else (lambda rgb: min(rgb) > 235))
        for _ in range(20):
            frame = self.session.frame()
            mask = bytes(int(foreground(frame.pixelColor(xx, yy).getRgb()[:3]))
                         for yy in range(y, y + height)
                         for xx in range(x, x + width))
            if hashlib.sha256(mask).hexdigest() == row['sha256']:
                frame.save(str(self.output / (label + suffix + '.png')))
                return frame
            self.session.advance(250)
        frame.save(str(self.output / ('failure-' + label + suffix + '.png')))
        self.fail('Original World Clock LCD did not match ' + label)

    def test_native_applications_world_clock_city_exit_and_relaunch(self):
        self.output = ROOT / 'reports/worldclock-test'
        self.output.mkdir(parents=True, exist_ok=True)
        self.keys = []
        point = 0x44d6e10a
        previous = guest_ui.CHECKPOINTS.get(point)
        guest_ui.CHECKPOINTS[point] = 'Java application execution'
        report = {}
        try:
            with guest_ui.OriginalUISession(default_theme=True) as session:
                self.session = session
                try:
                    self.press('select')
                    self.assertEqual({row['game'] for row in
                        session.provenance['native_game_installations']},
                        {'QuadraPop', 'PuzzleSlider'})
                    installed = session.provenance['native_application_installations']
                    self.assertEqual(len(installed), 1)
                    self.assertEqual(installed[0]['game'], 'WorldClock3D')
                    self.assertEqual(installed[0]['destination'], 'Applications')
                    self.assertTrue(installed[0]['native_completion_observed'])
                    for key in ('down', 'down', 'select', 'down', 'select'):
                        self.press(key)
                    self.expect('applications_title')
                    self.expect('world_clock_entry')
                    self.press('select')
                    self.expect('english')
                    self.press('select')
                    original = self.expect('utc')
                    for key in ('right', 'soft_left', 'down'):
                        self.press(key)
                    self.expect('lahore_list')
                    self.press('soft_left')
                    changed = self.expect('lahore_globe')
                    original_map = original.copy(2, 25, 172, 133)
                    changed_map = changed.copy(2, 25, 172, 133)
                    self.assertNotEqual(original_map.constBits().tobytes(),
                                        changed_map.constBits().tobytes())
                    for key in ('soft_right', 'down', 'down', 'down', 'down'):
                        self.press(key)
                    self.expect('exit')
                    self.press('select')
                    self.expect('applications_title', '-after-exit')
                    self.expect('world_clock_entry', '-after-exit')
                    self.press('select')
                    self.expect('utc', '-relaunch')
                    executions = [event for event in session.events
                                  if event['checkpoint'] == 'Java application execution']
                    self.assertEqual(len(executions), 2)
                    self.assertFalse(session.q.pressed_keys)
                    self.assertTrue(session.report()['source_unchanged'])
                    self.assertEqual({row['game'] for row in
                        session.provenance['native_game_installations']},
                        {'QuadraPop', 'PuzzleSlider'})
                    report['result'] = 'PASS'
                finally:
                    report.update(keys=self.keys, session=session.report(),
                        scope='Native World Clock2.0.7 in Applications; original city/globe UI, exit and relaunch')
                    (self.output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
        finally:
            if previous is None:
                guest_ui.CHECKPOINTS.pop(point, None)
            else:
                guest_ui.CHECKPOINTS[point] = previous


if __name__ == '__main__':
    unittest.main()
