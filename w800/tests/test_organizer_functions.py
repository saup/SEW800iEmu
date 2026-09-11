"""Exercise actual original Organizer state changes with physical keys.

The reviewed LCD fixtures contain only text masks from original captures.
Task completion-state checks do not assert timestamp fidelity: the immediate
UTC display versus reopened local display is documented separately.
"""
import hashlib
import json
import os
from pathlib import Path
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication

from w800.backend import ROOT
from w800.guest_ui import OriginalUISession

app = QApplication.instance() or QApplication([])
FIXTURES = json.loads((Path(__file__).parent / 'fixtures/organizer-text.json').read_text())


def text_mask(frame, rect):
    x, y, width, height = rect
    return bytes(int(max(frame.pixelColor(xx, yy).red(),
                         frame.pixelColor(xx, yy).green(),
                         frame.pixelColor(xx, yy).blue()) < 128)
                 for yy in range(y, y + height) for xx in range(x, x + width))


@unittest.skipUnless((ROOT / 'build/qemu-system-arm').exists(), 'Local QEMU build required')
class OrganizerFunctionsTests(unittest.TestCase):
    def press(self, *keys):
        self.actions.append({'keys': keys})
        for key in keys:
            self.session.advance(120, (key, True))
            self.session.advance(450, (key, False))

    def wait(self, milliseconds):
        self.actions.append({'wait_ms': milliseconds})
        while milliseconds:
            window = min(milliseconds, 1000)
            self.session.advance(window)
            milliseconds -= window

    def hold_clear(self):
        # The original input handler decides the long-press threshold. Keep
        # the contact held for 2.5 seconds and check its actual UI result.
        self.actions.append({'key': 'clear', 'hold_ms': 2500})
        self.session.advance(120, ('clear', True))
        self.wait(2380)
        self.session.advance(450, ('clear', False))

    def capture(self, label):
        self.wait(700)
        frame = self.session.frame()
        frame.save(str(self.output / (label + '.png')))
        return frame

    def pixels(self, rect=(0, 20, 176, 175)):
        body = self.session.frame().copy(*rect)
        return body.constBits().tobytes()

    def expect_text(self, name):
        fixture = FIXTURES[name]
        self.capture(name)
        actual = hashlib.sha256(text_mask(self.session.frame(), fixture['rect'])).hexdigest()
        self.assertEqual(actual, fixture['sha256'], name)

    def test_original_calculator_stopwatch_calendar_tasks_and_notes(self):
        self.output = ROOT / 'reports/organizer-functions-test'
        self.output.mkdir(parents=True, exist_ok=True)
        self.actions = []
        passed = False
        with OriginalUISession(start_screen='menu', default_theme=True) as session:
            self.session = session
            try:
                self.press('down', 'down', 'select', *(['down'] * 9), 'select')
                self.press('2', 'select', '3', 'soft_left')
                self.expect_text('calculator_five')
                self.press('back', 'select', '1', '2', 'left', 'left', 'select', '4', 'soft_left')
                self.expect_text('calculator_forty_eight')
                self.press('back', 'select', '9', 'left', 'left', 'left', 'select', '4', 'soft_left')
                self.expect_text('calculator_two_point_two_five')
                self.hold_clear()
                self.expect_text('calculator_zero')

                self.press('back', 'up', 'up', 'select')
                self.capture('stopwatch_zero')
                zero = self.pixels()
                self.press('soft_left')
                self.wait(2000)
                self.capture('stopwatch_running')
                self.assertNotEqual(self.pixels(), zero)
                self.press('soft_right')
                self.wait(2000)
                self.expect_text('stopwatch_two_laps')
                self.press('soft_left')
                self.capture('stopwatch_stopped')
                stopped = self.pixels()
                self.wait(3000)
                self.assertEqual(self.pixels(), stopped)
                self.press('soft_left')
                self.wait(2000)
                self.capture('stopwatch_restarted')
                self.assertNotEqual(self.pixels(), stopped)
                self.press('soft_left', 'soft_right')
                self.capture('stopwatch_reset')
                self.assertEqual(self.pixels(), zero)

                self.press('back', *(['up'] * 5), 'select', 'right', 'down', 'select', 'select')
                self.press('select', '1', '0', '0', '0', 'select', 'select')
                self.press('8', '3', '7', '8', 'soft_left', 'soft_left', 'select')
                self.wait(1500)
                self.press('down', 'select')
                self.capture('calendar_saved')
                saved_appointment = self.pixels()
                self.assertIn('Goto Cale_SaveEvent_Page', session.messages)
                self.press('back', 'back', 'back', 'select', 'right', 'down', 'select', 'down', 'select')
                self.capture('calendar_reopened')
                self.assertEqual(self.pixels(), saved_appointment)
                self.press('back', 'back')
                month = self.pixels((40, 22, 90, 22))
                self.press(*(['right'] * 31))
                self.capture('calendar_next_month')
                self.assertNotEqual(self.pixels((40, 22, 90, 22)), month)

                self.press('back', 'down', 'select', 'select', 'select')
                self.press('8', '3', '7', '8', 'soft_left', 'soft_right')
                self.wait(1000)
                self.press('down', 'select')
                self.expect_text('task_not_done')
                self.press('soft_right', 'select')
                self.expect_text('task_done')
                self.press('back', 'back', 'select', 'down', 'select')
                self.expect_text('task_done')

                self.press('back', 'back', 'down', 'select')
                self.capture('notes_empty')
                empty_notes = self.pixels()
                self.press('select', '2', '3', 'soft_left')
                self.wait(1000)
                self.press('down', 'select')
                self.expect_text('note_Be')
                self.press('soft_right', 'select')
                self.hold_clear()
                self.press('8', '3', '7', '8', 'soft_left')
                self.wait(1000)
                self.press('back', 'select', 'down', 'select')
                self.expect_text('note_Test')
                self.press('soft_right', 'down', 'down', 'down', 'select')
                self.capture('note_delete_confirmation')
                self.press('soft_left')
                self.wait(1000)
                self.press('back', 'select')
                self.capture('notes_deleted_reopened')
                self.assertEqual(self.pixels(), empty_notes)
                self.assertTrue(session.ready)
                self.assertFalse(session.q.pressed_keys)
                self.assertTrue(session.report()['source_unchanged'])
                self.assertFalse(session.report()['guest_instruction_patches'])
                self.assertFalse(session.report()['validation_result_changes'])
                passed = True
            finally:
                (self.output / 'result.json').write_text(json.dumps({
                    'status': 'PASS' if passed else 'FAIL',
                    'actions': self.actions, 'session': session.report(),
                    'held_keys': sorted(session.q.pressed_keys),
                    'scope': 'Original app functions and persistence across book reopen within one private VM; task timestamp discrepancy excluded',
                }, indent=2) + '\n')


if __name__ == '__main__':
    unittest.main()
