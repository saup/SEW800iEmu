"""UART dock transport/lifecycle and original firmware round trip."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import time
import unittest
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtNetwork import QLocalServer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from w800.uart_gui import UARTPanel
from w800.ui_gui import UIWindow
from w800.backend import ROOT

app = QApplication.instance() or QApplication([])


def wait(condition, timeout=10):
    deadline = time.monotonic() + timeout
    while not condition():
        app.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError('Timed out waiting for UART GUI condition')
        time.sleep(.01)
    app.processEvents()


class UARTGuiTests(unittest.TestCase):
    def test_socket_pause_reconnect_and_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            server = QLocalServer()
            self.assertTrue(server.listen(str(Path(directory)/'uart.sock')))
            panel = UARTPanel()
            panel.show()
            try:
                panel.set_endpoint(server.fullServerName())
                panel.set_running(True)
                wait(lambda: server.hasPendingConnections() and panel.is_connected())
                peer = server.nextPendingConnection()
                panel.command.setFocus()
                QTest.keyClicks(panel.command, 'AT')
                QTest.keyClick(panel.command, Qt.Key.Key_Return)
                wait(lambda: peer.bytesAvailable() > 0)
                self.assertEqual(bytes(peer.readAll()), b'AT\r')
                peer.write(b'\r\nOK\r\n\x1b[31m')
                wait(lambda: 'OK' in panel.output.toPlainText())
                self.assertIn('\\x1b[31m', panel.output.toPlainText())
                panel.set_running(False)
                self.assertFalse(panel.send.isEnabled())
                panel.command.setText('AT+CGMI')
                panel.send_command()
                QTest.qWait(30)
                self.assertEqual(peer.bytesAvailable(), 0)
                panel.toggle_connection()
                self.assertFalse(panel.is_connected())
                panel.toggle_connection()
                wait(lambda: server.hasPendingConnections() and panel.is_connected())
                panel.reset()
                self.assertFalse(panel.is_connected())
                self.assertFalse(panel.send.isEnabled())
                self.assertEqual(panel.command.text(), '')
            finally:
                panel.reset()
                panel.close()
                server.close()

    def test_native_firmware_commands_in_dock(self):
        window = UIWindow(audio_output='none', host_camera=False)
        window.show()
        try:
            wait(lambda: window.ready or window.failure, 120)
            self.assertIsNone(window.failure)
            window.uart_dock.raise_()
            wait(window.uart_panel.is_connected)
            window.send_key('select', True)
            window.send_key('select', False)
            panel = window.uart_panel
            panel.command.setFocus()
            QTest.keyClicks(panel.command, 'AT+CGMI')
            QTest.keyClick(panel.command, Qt.Key.Key_Return)
            wait(lambda: ('Sony Ericsson' in panel.output.toPlainText() and
                          'OK' in panel.output.toPlainText()) or window.failure, 180)
            self.assertIsNone(window.failure)
            self.assertIn('Sony Ericsson', panel.output.toPlainText())
            self.assertIn('OK', panel.output.toPlainText())
            window.grab().save(str(ROOT/'reports/uart-terminal-gui.png'))
            window.toggle_pause()
            wait(lambda: window.paused or window.failure, 180)
            self.assertIsNone(window.failure)
            self.assertFalse(panel.send.isEnabled())
            self.assertIn('paused', panel.status.text())
            window.toggle_pause()
            wait(lambda: not window.paused)
            self.assertTrue(panel.send.isEnabled())
        finally:
            window.close()
            wait(lambda: window.worker is None, 30)
        self.assertFalse(window.uart_panel.is_connected())
        self.assertTrue(window.last_report['source_unchanged'])


if __name__ == '__main__': unittest.main()
