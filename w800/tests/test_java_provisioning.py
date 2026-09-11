"""Original native game installation, registry, Java execution and physical keys."""
import hashlib
import json
import os
from pathlib import Path
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from w800 import guest_ui
from w800.backend import ROOT

app = QApplication.instance() or QApplication([])
FIXTURES = json.loads((Path(__file__).parent / 'fixtures/java-native-games.json').read_text())


@unittest.skipUnless((ROOT / 'build/qemu-system-arm').exists(), 'Local QEMU build required')
class NativeGamesTests(unittest.TestCase):
    def press(self, key):
        self.keys.append(key)
        self.session.advance(120, (key, True))
        self.session.advance(450, (key, False))

    def expect(self, label):
        row = FIXTURES['regions'][label]
        x, y, width, height = row['rect']
        for _ in range(12):
            frame = self.session.frame()
            if row['mask'] == 'dark':
                foreground = lambda rgb: max(rgb) < 128
            elif row['mask'] == 'light':
                foreground = lambda rgb: min(rgb) > 235
            else:
                foreground = lambda rgb: max(rgb) < 60 or min(rgb) > 235
            mask = bytes(int(foreground(frame.pixelColor(xx, yy).getRgb()[:3]))
                         for yy in range(y, y + height)
                         for xx in range(x, x + width))
            if hashlib.sha256(mask).hexdigest() == row['sha256']:
                frame.save(str(ROOT / f'reports/{self.artifact_prefix}-{label}.png'))
                return
            self.session.advance(250)
        frame.save(str(ROOT / f'reports/{self.artifact_prefix}-failure-{label}.png'))
        self.fail(f'Original game LCD did not match {label}')

    def test_native_installer_games_registry_and_quadra_execution(self):
        self.verify_theme(default_theme=True)

    def test_built_in_red_theme_native_games(self):
        self.verify_theme(default_theme=False)

    def verify_theme(self, default_theme):
        self.keys = []
        self.artifact_prefix = 'java-provisioning-test' if default_theme else 'java-provisioning-test-red'
        point = 0x44d6e10a
        previous = guest_ui.CHECKPOINTS.get(point)
        guest_ui.CHECKPOINTS[point] = 'Java application execution'
        try:
            with guest_ui.OriginalUISession(default_theme=default_theme) as session:
                self.session = session
                try:
                    self.press('select')  # Real original Phone mode selection.
                    self.assertEqual({row['game'] for row in session.provenance['native_game_installations']},
                                     {'PuzzleSlider', 'QuadraPop'})
                    self.assertTrue(all(row['native_completion_observed'] and
                                        row['mmi_argument_stack_restored']
                                        for row in session.provenance['native_game_installations']))
                    for key in ('up', 'right', 'select', 'select'):
                        self.press(key)
                    self.expect('games_puzzle')
                    self.expect('games_quadra')
                    self.press('down')
                    self.press('select')
                    self.expect('quadra_new_game')
                    entries = [event for event in session.events
                               if event['checkpoint'] == 'Java application execution']
                    self.assertTrue(entries)
                    self.assertEqual(entries[-1]['args'][0], 1)
                    self.press('select')
                    self.expect('quadra_score')
                    self.press('6')
                    self.press('5')
                    session.frame().save(str(ROOT / f'reports/{self.artifact_prefix}-quadra-input.png'))
                    self.expect('quadra_score')
                    self.assertFalse(session.q.pressed_keys)
                    self.assertTrue(session.report()['source_unchanged'])
                finally:
                    report = {'physical_key_sequence': self.keys,
                              'session': session.report(),
                              'default_theme': default_theme,
                              'scope': 'Production default startup only; physical Phone selection, native Games installation and actual Java game'}
                    (ROOT / f'reports/{self.artifact_prefix}.json').write_text(json.dumps(report, indent=2) + '\n')
        finally:
            if previous is None:
                guest_ui.CHECKPOINTS.pop(point, None)
            else:
                guest_ui.CHECKPOINTS[point] = previous


if __name__ == '__main__':
    unittest.main()
