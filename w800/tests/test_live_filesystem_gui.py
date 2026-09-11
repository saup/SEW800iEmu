"""Exercise file transfers through the running phone's actual GUI worker."""
import hashlib
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from w800.backend import ROOT
from w800.ui_gui import UIWindow

app = QApplication.instance() or QApplication([])


@unittest.skipUnless((ROOT / 'build/qemu-system-arm').exists(), 'Local QEMU required')
class LiveFilesystemGuiTests(unittest.TestCase):
    def wait_for(self, condition, timeout=60):
        deadline = time.monotonic() + timeout
        while not condition():
            app.processEvents()
            if time.monotonic() > deadline:
                self.fail('Live filesystem GUI did not complete before its deadline')
            time.sleep(.01)
        app.processEvents()

    def test_same_worker_transfer_pause_restart_and_stale_result(self):
        flash = ROOT / 'firmware/prepared/flash-gdfs.bin'
        source_hash = hashlib.sha256(flash.read_bytes()).digest()
        window = UIWindow(audio_output='none')
        window.show()
        panel = window.filesystem_panel

        def idle():
            return not panel.busy and panel.pending is None and not window.filesystem_busy

        def select_file(name):
            items = [panel.tree.topLevelItem(i) for i in range(panel.tree.topLevelItemCount())]
            item = next((item for item in items if item.text(0) == name), None)
            self.assertIsNotNone(item, panel.status.text())
            panel.tree.setCurrentItem(item)
            return item

        try:
            self.wait_for(lambda: window.ready or window.failure)
            self.assertIsNone(window.failure)
            worker = window.worker
            QTest.mouseClick(panel.refresh, Qt.MouseButton.LeftButton)
            self.wait_for(idle)
            self.assertEqual(panel.directory, '/tpa/user/other')
            with tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / 'Live GUI upload.txt'
                payload = b'Written through the running W800 firmware.\n' * 17
                source.write_bytes(payload)
                with patch('w800.live_filesystem_gui.QFileDialog.getOpenFileName',
                           return_value=(str(source), '')):
                    QTest.mouseClick(panel.upload, Qt.MouseButton.LeftButton)
                self.wait_for(idle)
                select_file(source.name)
                self.assertIs(window.worker, worker)
                self.assertTrue(panel.export.isEnabled())
                self.assertFalse(panel.install.isEnabled())
                QTest.mouseClick(panel.preview_button, Qt.MouseButton.LeftButton)
                self.wait_for(idle)
                self.assertIn('Written through the running W800 firmware.', panel.preview.toPlainText())
                destination = Path(directory) / 'exported.txt'
                with patch('w800.live_filesystem_gui.QFileDialog.getSaveFileName',
                           return_value=(str(destination), '')):
                    QTest.mouseClick(panel.export, Qt.MouseButton.LeftButton)
                self.wait_for(idle)
                self.assertEqual(destination.read_bytes(), payload)

            # Pause disables all file operations without replacing the VM.
            window.toggle_pause()
            self.wait_for(lambda: window.paused)
            self.assertFalse(panel.refresh.isEnabled())
            panel.request('list', '/')
            self.assertIsNone(panel.pending)
            window.toggle_pause()
            self.wait_for(lambda: not window.paused)
            self.assertTrue(panel.refresh.isEnabled())
            self.assertIs(window.worker, worker)
            window.grab().save(str(ROOT / 'reports/live-filesystem-gui.png'))

            # A new VM must clear file state, and an old worker response must
            # never replace the new session's current folder or controls.
            previous_id = panel.serial
            window.restart_ui()
            self.wait_for(lambda: window.worker is not None and window.worker is not worker
                          and window.ready, timeout=90)
            self.assertEqual(panel.tree.topLevelItemCount(), 0)
            stale = dict(request=dict(id=previous_id, operation='list', path='/'),
                         data=[dict(name='stale', path='/stale', directory=False, size=1)])
            window.filesystem_completed(stale)
            self.assertEqual(panel.tree.topLevelItemCount(), 0)
            QTest.mouseClick(panel.refresh, Qt.MouseButton.LeftButton)
            self.wait_for(idle)
            self.assertNotIn('Live GUI upload.txt',
                             [panel.tree.topLevelItem(i).text(0)
                              for i in range(panel.tree.topLevelItemCount())])
            self.assertIsNone(window.failure)
        finally:
            window.close()
            self.wait_for(lambda: window.worker is None)
        self.assertFalse(window.isVisible())
        self.assertEqual(hashlib.sha256(flash.read_bytes()).digest(), source_hash)

