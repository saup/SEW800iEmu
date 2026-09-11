"""The enlarged view preserves original pixels, aspect ratio and physical keys."""
import os
import time
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from w800.backend import ROOT
from w800.ui_gui import UIWindow

app = QApplication.instance() or QApplication([])


class DisplayGuiTests(unittest.TestCase):
    def test_native_frame_zoom_input_pause_and_close(self):
        window = UIWindow(audio_output='none')
        window.show()

        def wait(condition):
            deadline = time.monotonic() + 70
            while not condition():
                app.processEvents()
                if window.failure:
                    self.fail(window.failure)
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.01)
            app.processEvents()

        try:
            wait(lambda: window.ready)
            worker = window.worker
            window.show_display()
            view = window.display_window
            screen = view.screen
            self.assertEqual(screen.frame, window.phone.frame)
            original = screen.frame.copy()
            for width, height in ((800, 450), (350, 900), (800, 1000)):
                view.resize(width, height)
                app.processEvents()
                rect = screen.screen_rect()
                self.assertAlmostEqual(rect.width() / rect.height(), 176 / 220)
                self.assertLessEqual(rect.width(), screen.width())
                self.assertLessEqual(rect.height(), screen.height())
            view.scale.setCurrentIndex(2)
            view.apply_scale()
            app.processEvents()
            self.assertAlmostEqual(screen.screen_rect().width(), 528, delta=2)
            self.assertEqual(screen.frame, window.phone.frame)
            # Original startup selection opens the native main menu.
            QTest.keyClick(screen, Qt.Key.Key_Return)
            wait(lambda: screen.frame != original)
            self.assertEqual(screen.frame, window.phone.frame)
            self.assertIs(window.worker, worker)
            screen.grab().save(str(ROOT / 'reports/enlarged-display.png'))
            # Closing a view with a held key must deliver its release.
            events = []
            screen.key_changed.connect(lambda key, down: events.append((key, down)))
            QTest.keyPress(screen, Qt.Key.Key_Down)
            view.close()
            self.assertIn(('down', False), events)
            self.assertFalse(screen.pressed)
            window.toggle_pause()
            wait(lambda: window.paused)
            window.show_display()
            self.assertFalse(screen.isEnabled())
            frozen = screen.frame.copy()
            QTest.qWait(100)
            self.assertEqual(frozen, screen.frame)
        finally:
            window.close()
            wait(lambda: window.worker is None)
            window.close()
        self.assertFalse(window.display_window.isVisible())
        self.assertTrue(window.last_report['source_unchanged'])


if __name__ == '__main__':
    unittest.main()
