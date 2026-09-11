"""Original keypad text entry and a phone-memory contact round trip in QEMU.

Golden text masks come from the manually reviewed original LCD, not a host
text renderer. Only keys reach the guest; the test never writes a contact or
input mode into guest memory. This verifies storage within one VM session.
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
FIXTURES = json.loads((Path(__file__).parent / 'fixtures/contacts-keypad-text.json').read_text())


def text_mask(frame, rect):
    x, y, width, height = rect
    return bytes(int(max(frame.pixelColor(xx, yy).red(),
                         frame.pixelColor(xx, yy).green(),
                         frame.pixelColor(xx, yy).blue()) < 128)
                 for yy in range(y, y + height) for xx in range(x, x + width))


@unittest.skipUnless((ROOT / 'build/qemu-system-arm').exists(), 'Local QEMU build required')
class ContactsKeypadTests(unittest.TestCase):
    def press(self, key, count=1, release_ms=450):
        self.sequence.append({'key': key, 'count': count, 'down_ms': 120,
                              'up_ms': release_ms})
        for _ in range(count):
            self.session.advance(120, (key, True))
            self.session.advance(release_ms, (key, False))

    def wait(self, milliseconds):
        self.sequence.append({'wait_ms': milliseconds})
        while milliseconds:
            window = min(milliseconds, 1000)
            self.session.advance(window)
            milliseconds -= window

    def matches(self, name):
        expected = FIXTURES['regions'][name]
        return hashlib.sha256(text_mask(self.session.frame(), expected['rect'])).hexdigest() == expected['sha256']

    def expect_text(self, name):
        # UI workers and LCD DMA may finish after the physical key release.
        for _ in range(12):
            if self.matches(name):
                self.session.frame().save(str(ROOT / f'reports/contacts-keypad-test-{name}.png'))
                return
            self.wait(250)
        self.session.frame().save(str(ROOT / f'reports/contacts-keypad-test-failure-{name}.png'))
        self.fail(f'Original LCD text did not match {name}')

    def dictionary(self, enabled):
        # The editor preserves the writing language and previous menu focus.
        # Use its physical hold-* shortcut and observe the native indicator,
        # avoiding an assumed menu index that could select Standard abc.
        target = 't9_on' if enabled else 't9_off'
        for _ in range(4):
            if self.matches(target):
                return
            self.hold_star()
            self.wait(1000)
        self.expect_text(target)

    def hold_star(self):
        self.sequence.append({'key': '*', 'hold_ms': 1200})
        self.session.advance(100, ('*', True))
        self.wait(1100)
        self.session.advance(450, ('*', False))

    def check_t9_and_clear_in_draft(self):
        for key in ('up', 'select', 'select'):
            self.press(key)
        self.dictionary(True)
        self.expect_text('t9_on')
        # Short * cycles capitalization. Keep the dictionary enabled while
        # selecting lowercase to make the expected suggestion unambiguous.
        for _ in range(4):
            if self.matches('input_lowercase'):
                break
            self.press('*')
        self.expect_text('input_lowercase')
        for key in '8378':
            self.press(key, release_ms=250)
        self.expect_text('t9_test')
        self.press('clear')
        self.expect_text('t9_ter')
        self.dictionary(False)
        self.expect_text('t9_off')
        self.dictionary(True)
        self.expect_text('t9_on')
        self.dictionary(False)
        self.expect_text('t9_off')
        self.press('clear', 3)
        for key in '8378':
            self.press(key)
            self.wait(1000)
        self.press('clear')
        self.expect_text('multitap_tdp')
        self.press('back')
        self.press('back')  # Discard the unsaved second contact.

    def test_create_save_close_reopen_with_physical_keys(self):
        self.sequence = []
        with OriginalUISession(start_screen='menu') as session:
            self.session = session
            try:
                for key in ('down', 'select', 'down', 'select', 'select'):
                    self.press(key)
                self.dictionary(False)
                for key, count in (('8', 1), ('3', 2), ('7', 4), ('8', 1)):
                    self.press(key, count, release_ms=150)
                    self.wait(1000)
                self.expect_text('name_Test')
                self.press('soft_left')
                self.wait(1000)
                # Accepting the name advances the original form focus to
                # New number automatically.
                self.press('select')
                self.wait(1000)
                for key in '5550100':
                    self.press(key, release_ms=250)
                self.expect_text('number_5550100')
                self.press('select')
                self.wait(1000)
                self.press('select')  # Mobile number type.
                self.wait(1000)
                self.press('soft_right')  # Native Save softkey.
                self.wait(3000)
                self.assertIn('Goto PB_UI_ContactForm_InformSaved_Page', session.messages)
                self.press('down')  # Move from New contact to the saved Test.
                self.wait(1000)
                self.expect_text('list_Test')
                self.expect_text('list_5550100')
                self.press('back')
                self.wait(1000)
                self.press('select')
                self.wait(1000)
                self.expect_text('list_Test')
                self.expect_text('list_5550100')
                self.check_t9_and_clear_in_draft()
                self.assertTrue(session.report()['source_unchanged'])
                self.assertFalse(session.q.pressed_keys)
                self.assertTrue(session.provenance['phonebook_unavailable_replies'])
                self.assertTrue(all(row['original_error'] == '0x1a' and row['sent']
                                    for row in session.provenance['phonebook_unavailable_replies']))
            finally:
                report = {'physical_key_sequence': self.sequence,
                          'session': session.report(),
                          'scope': 'Phone-memory contact survives closing and reopening its book in one VM'}
                (ROOT / 'reports/contacts-keypad-test.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    unittest.main()
