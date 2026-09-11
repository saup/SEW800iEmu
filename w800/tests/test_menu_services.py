"""Interaction regressions for original offline firmware applications.

These exercise actual LCD output and physical keys in separate disposable QEMU
sessions. A task/book creation message alone is not treated as support.
"""
import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from w800.backend import ROOT
from w800.guest_ui import OriginalUISession


def body(session):
    # Keep the copied QImage alive while reading its borrowed memory view.
    frame = session.frame().copy(0, 20, 176, 175)
    return frame.constBits().tobytes()


def press(session, *keys):
    for key in keys:
        session.advance(120, (key, True))
        session.advance(450, (key, False))


def settled_body(session):
    """Observe completion of the native list's final row animation."""
    previous = body(session)
    unchanged = 0
    for _ in range(20):
        session.advance(100)
        current = body(session)
        unchanged = unchanged + 1 if current == previous else 0
        if unchanged >= 2:
            return current
        previous = current
    raise AssertionError('Original menu body did not settle within two seconds')


@unittest.skipUnless((ROOT / 'build/qemu-system-arm').exists(), 'Local QEMU build required')
class OriginalMenuServiceTests(unittest.TestCase):
    def assert_context(self, session):
        self.assertTrue(session.ready)
        self.assertFalse(session.q.pressed_keys)
        self.assertEqual(bytes.fromhex(session.d.packet('g')), session.files.saved)
        self.assertTrue(session.report()['source_unchanged'])

    def test_file_manager_picture_browsing_and_back(self):
        with OriginalUISession(start_screen='menu', default_theme=True) as session:
            press(session, 'down', 'left')
            selected_grid = body(session)
            press(session, 'select')
            categories = settled_body(session)
            self.assertIn('Goto DataBrowserCategoryList_ViewStatus_Page', session.messages)
            self.assertNotEqual(categories, selected_grid)
            press(session, 'select')
            thumbnails = body(session)
            self.assertIn('Goto DataBrowser_Main_Page', session.messages)
            self.assertNotEqual(thumbnails, categories)
            press(session, 'down')
            self.assertNotEqual(body(session), thumbnails)
            press(session, 'back')
            # The native final-row animation outlasts a key-release window.
            # Wait for its actual completion, then compare every body pixel.
            self.assertEqual(settled_body(session), categories)
            press(session, 'back')
            self.assertEqual(body(session), selected_grid)
            self.assert_context(session)

    def test_notes_text_entry_save_and_return(self):
        with OriginalUISession(start_screen='menu', default_theme=True) as session:
            press(session, 'down', 'down', 'select', *(['down'] * 4), 'select')
            empty_notes = body(session)
            self.assertIn('Goto Notes_ViewNotes_DataListWA_Page', session.messages)
            press(session, 'select')
            empty_editor = body(session)
            self.assertIn('Goto Notes_EditNote_StringInput_Page', session.messages)
            press(session, '2', '3')
            self.assertNotEqual(body(session), empty_editor)
            press(session, 'soft_left')
            saved_notes = body(session)
            self.assertIn('Goto Notes_EditNote_SaveAndClose_Page', session.messages)
            self.assertNotEqual(saved_notes, empty_notes)
            # New note is selected again; Down opens the note just saved.
            press(session, 'down', 'select')
            self.assertNotEqual(body(session), saved_notes)
            press(session, 'back')
            self.assert_context(session)


if __name__ == '__main__':
    unittest.main()
