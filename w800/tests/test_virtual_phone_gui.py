"""Virtual-name worker serialization and sharing with the enlarged display."""
import os
import time
import unittest
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from w800.backend import ROOT
from w800.ui_gui import UIWindow

app = QApplication.instance() or QApplication([])


class VirtualPhoneGuiTests(unittest.TestCase):
    def test_apply_clear_pause_and_stale_response(self):
        window = UIWindow(audio_output='none')
        window.show()

        def wait(condition):
            deadline = time.monotonic() + 90
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
            panel = window.virtual_panel
            panel.name.setText('Creative Zen')
            panel.apply.click()
            request = panel.pending.copy()
            self.assertTrue(window.service_busy)
            self.assertFalse(window.phone.isEnabled())
            self.assertFalse(window.filesystem_panel.available)
            wait(lambda: panel.pending is None)
            self.assertIn('Creative Zen', panel.status.text())
            self.assertTrue(window.phone.isEnabled())
            self.assertFalse(window.service_busy)
            window.show_display()
            self.assertEqual(window.phone.frame, window.display_window.screen.frame)
            window.display_window.grab().save(str(ROOT / 'reports/virtual-network-enlarged.png'))
            window.toggle_pause()
            wait(lambda: window.paused)
            self.assertFalse(panel.apply.isEnabled())
            frozen = window.phone.frame.copy()
            QTest.qWait(100)
            self.assertEqual(frozen, window.phone.frame)
            window.toggle_pause()
            wait(lambda: not window.paused)
            panel.clear.click()
            wait(lambda: panel.pending is None)
            self.assertIn('restored', panel.status.text())
            self.assertIs(window.worker, worker)
            panel.reset()
            status = panel.status.text()
            window.service_completed(dict(request=request, data={'name': 'stale'}))
            self.assertEqual(panel.status.text(), status)
        finally:
            window.close()
            wait(lambda: window.worker is None)
        self.assertTrue(window.last_report['source_unchanged'])
        self.assertIsNone(window.last_report['virtual_network_name']['name'])


if __name__ == '__main__': unittest.main()
