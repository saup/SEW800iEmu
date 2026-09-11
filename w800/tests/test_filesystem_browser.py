"""Exercise the original filesystem and its native browser with real QEMU."""
import hashlib
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from w800.backend import ROOT
from w800.guest_files import FilesystemSession
from w800.filesystem_gui import FilesystemBrowser

app = QApplication.instance() or QApplication([])


@unittest.skipUnless((ROOT / 'build/qemu-system-arm').exists(), 'Local QEMU build required')
class FilesystemTests(unittest.TestCase):
    def test_original_directory_and_file_reads_preserve_source(self):
        flash = ROOT / 'firmware/prepared/flash-gdfs.bin'
        before = hashlib.sha256(flash.read_bytes()).digest()
        with FilesystemSession() as session:
            process = session.q.process
            root = session.list_directory('/')
            self.assertTrue({'tpa', 'ifs', 'system', 'smsdata'} <= {e['name'] for e in root})
            self.assertTrue(all(e['directory'] for e in root))
            entry, = session.list_directory('/tpa/preset/system/menu')
            self.assertEqual(entry['name'], 'menu.ml')
            self.assertFalse(entry['directory'])
            data = session.read_file(entry['path'])
            self.assertEqual(len(data), entry['size'])
            self.assertTrue(data.startswith(b'<?xml'))
            self.assertIn(b'<element id="MainMenu">', data)
            self.assertEqual(session.read_file(entry['path'], 31), data[:31])
            # Internal mounted files need the underlying full-path API;
            # application FSX open rejects these while readdir succeeds.
            internal = session.read_file('/ifs/corrections/BT_PatchInfo.txt')
            self.assertEqual(len(internal), 998)
            self.assertIn(b'BT Patch Information File', internal)
            version, = [e for e in session.list_directory('/ifs/version') if e['name'] == 'R2A']
            self.assertEqual(len(session.read_file(version['path'])), version['size'])
            with self.assertRaises(OSError):
                session.list_directory('/this-folder-does-not-exist')
            with self.assertRaises(OSError):
                session.read_file('/tpa/preset/system/menu/absent.ml')
            # Failed opens must not exhaust handles or invalidate the VM.
            self.assertEqual(session.list_directory('/tpa/preset/system/menu'), [entry])
        self.assertIsNotNone(process.poll())
        self.assertEqual(hashlib.sha256(flash.read_bytes()).digest(), before)

    def wait_for(self, condition):
        deadline = time.monotonic() + 30
        while not condition():
            app.processEvents()
            if time.monotonic() > deadline:
                self.fail('Filesystem GUI did not complete within 30 seconds')
            time.sleep(.01)
        app.processEvents()

    def test_gui_navigation_preview_export_and_shutdown(self):
        browser = FilesystemBrowser()
        browser.show()
        try:
            self.wait_for(lambda: not browser.busy)
            self.assertGreater(browser.entries.topLevelItemCount(), 0, browser.status.text())
            browser.navigate('/tpa/preset/system/menu')
            self.wait_for(lambda: not browser.busy)
            item = browser.entries.topLevelItem(0)
            self.assertEqual(item.text(0), 'menu.ml', browser.status.text())
            browser.entries.setCurrentItem(item)
            self.assertTrue(browser.export_button.isEnabled())
            browser.activate(item)
            self.wait_for(lambda: not browser.busy)
            self.assertIn('<element id="MainMenu">', browser.preview.toPlainText())
            with tempfile.TemporaryDirectory() as folder:
                destination = Path(folder) / 'menu.ml'
                with patch('w800.filesystem_gui.QFileDialog.getSaveFileName', return_value=(str(destination), '')):
                    browser.export_selected()
                self.wait_for(lambda: not browser.busy)
                exported = destination.read_bytes()
                self.assertEqual(len(exported), item.data(0, Qt.ItemDataRole.UserRole)['size'])
                self.assertTrue(exported.startswith(b'<?xml'))
            browser.grab().save(str(ROOT / 'reports/filesystem-browser.png'))
            browser.navigate('/not-a-directory')
            self.wait_for(lambda: not browser.busy)
            self.assertIn('Could not complete', browser.status.text())
            # Previous listing remains available; the next request restarts a
            # failed inspection session and returns real data again.
            browser.navigate('/ifs')
            self.wait_for(lambda: not browser.busy)
            self.assertEqual(browser.current_path, '/ifs', browser.status.text())
        finally:
            browser.close()
            self.wait_for(lambda: not browser.worker.isRunning())
            self.assertFalse(browser.isVisible())

    def test_close_while_mounting(self):
        browser = FilesystemBrowser()
        browser.show()
        browser.close()
        self.wait_for(lambda: not browser.worker.isRunning())
        self.assertFalse(browser.isVisible())
