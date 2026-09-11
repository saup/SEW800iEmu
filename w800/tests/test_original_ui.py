"""Real QEMU navigation, startup choices, and native window lifecycle."""
import hashlib
import json
import os
import time
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from w800.backend import ROOT
from w800.guest_ui import OriginalUISession
from w800.ui_gui import UIWindow

app = QApplication.instance() or QApplication([])


def body(frame):
    crop = frame.copy(0, 20, 176, 175)
    return crop.constBits().tobytes()


def colors(frame):
    return len({frame.pixel(x, y) for x in range(176) for y in range(20, 195)})


@unittest.skipUnless((ROOT / 'build/qemu-system-arm').exists(), 'Local QEMU build required')
class OriginalUITests(unittest.TestCase):
    def press(self, session, key):
        session.advance(120, (key, True))
        session.advance(450, (key, False))

    def test_original_video_preview_and_back(self):
        with OriginalUISession(default_theme=True) as session:
            # Start phone > File manager > Videos > Demo Tour. Physical
            # selection changes the original panel from RGB565 to RGB666.
            for key in ('select', 'down', 'left', 'select', 'down', 'select', 'down'):
                self.press(session, key)
            listing = body(session.frame())
            completions = session.frames_completed
            self.press(session, 'select')
            # Page creation precedes its asynchronous full RGB666 transfer.
            # Wait for the native frame rather than assuming that View has
            # drawn everything by the end of the key-release interval.
            preview = session.frame()
            for _ in range(8):
                if body(preview) != listing:
                    break
                session.advance(250)
                preview = session.frame()
            preview.save(str(ROOT / 'reports/original-ui-video-preview.png'))
            self.assertIn('Goto ImageViewer_Main_Page', session.messages)
            self.assertGreater(session.frames_completed, completions)
            self.assertTrue(body(preview) != listing, 'Video preview did not reach the LCD within two seconds')
            self.press(session, 'back')
            self.assertEqual(body(session.frame()), listing)
            self.assertFalse(session.q.pressed_keys)
            self.assertTrue(session.report()['source_unchanged'])

    def test_original_orange_theme_browser_and_full_service_catalog(self):
        with OriginalUISession(default_theme=True) as session:
            rows = session.provenance['components']
            self.assertEqual(len(rows), 52)
            self.assertEqual(sum(row['started'] for row in rows), 51)
            self.assertEqual(session.provenance['service_creation_errors'], [])
            self.assertEqual(session.provenance['service_mode_exclusions'],
                             [{'id': 30, 'name': 'Customization', 'mode_mask': '0x80'}])
            self.assertEqual(session.provenance['theme'], '/tpa/user/theme/Default.thm')
            startup = session.frame()
            pixel = startup.pixelColor(10, 150)
            self.assertGreater(pixel.red(), 200)
            self.assertLess(pixel.blue(), 32)
            startup.save(str(ROOT / 'reports/original-ui-orange-startup.png'))
            # Enter Settings > Display > Themes and apply Default using only
            # the phone keys. A second original ThemeBook result proves that
            # the file list completed and its activation callback ran.
            for key in ('select', 'right', 'down', 'down', 'select',
                        'right', 'right', 'down', 'select'):
                self.press(session, key)
            session.advance(600)
            session.frame().save(str(ROOT / 'reports/original-ui-themes.png'))
            self.press(session, 'select')
            results = []
            for _ in range(8):
                session.advance(1000)
                results = [e for e in session.events if e['checkpoint'] == 'Theme activation result']
                if len(results) >= 2:
                    break
            self.assertEqual([e['args'][1] for e in results], [0xf, 0xf])
            session.advance(500)
            self.assertTrue(session.report()['source_unchanged'])

    def test_original_startup_choices_settings_and_unchanged_flash(self):
        flash = ROOT / 'firmware/prepared/flash-gdfs.bin'
        before = hashlib.sha256(flash.read_bytes()).digest()
        with OriginalUISession() as session:
            process = session.q.process
            startup = session.frame()
            self.assertIn('Goto InitBook_StartupMode_Page', session.messages)
            # Physical selection enters the original menu renderer, not the
            # SIM dialogue. Hundreds of new icon colors must reach the LCD.
            self.press(session, 'select')
            menu = session.frame()
            self.assertIn('Goto Menu_First_Page', session.messages)
            self.assertFalse(any('SimAuth_InsertSIM_Page' in m for m in session.messages))
            self.assertGreater(colors(menu), colors(startup) + 1000)
            self.assertNotEqual(body(menu), body(startup))
            self.assertFalse(session.q.pressed_keys)
            menu.save(str(ROOT / 'reports/original-ui-main-menu.png'))
            # Grid navigation: Messaging -> Walkman -> Radio -> Settings.
            for key in ('right', 'down', 'down'):
                self.press(session, key)
            selected = session.frame()
            self.assertNotEqual(body(selected), body(menu))
            self.press(session, 'select')
            settings = session.frame()
            self.assertLess(colors(settings), 300)
            self.assertNotEqual(body(settings), body(selected))
            settings.save(str(ROOT / 'reports/original-ui-settings.png'))
            self.press(session, 'back')
            self.assertGreater(colors(session.frame()), 1500)
            self.press(session, 'back')
            # The unmodified InitBook is still beneath the menu. Its second
            # choice must retain the original Music only callback.
            self.press(session, 'down')
            self.press(session, 'select')
            self.assertIn('Goto MM_Browser_Toplevel_Bk_MainPage', session.messages)
            music = session.frame()
            self.assertNotEqual(body(music), body(startup))
            music.save(str(ROOT / 'reports/original-ui-walkman.png'))
            self.press(session, 'back')
            self.assertLess(colors(session.frame()), 1000)
            self.assertTrue(session.word(0x4c289034) & (1 << 22))
            # Returning from Music only must use Start phone's own mode
            # setter before opening the regular menu again.
            self.press(session, 'up')
            self.press(session, 'select')
            self.assertFalse(session.word(0x4c289034) & (1 << 22))
            self.assertGreater(colors(session.frame()), 1500)
            self.assertTrue(session.ready)
            self.assertEqual(bytes.fromhex(session.d.packet('g')), session.files.saved)
            report = session.report()
            self.assertTrue(report['source_unchanged'])
            self.assertGreater(report['lcd_completions'], 10)
            self.assertFalse(any('User called assert' in m for m in session.messages))
            (ROOT / 'reports/original-ui-validation.json').write_text(json.dumps(report, indent=2) + '\n')
        self.assertIsNotNone(process.poll())
        self.assertEqual(hashlib.sha256(flash.read_bytes()).digest(), before)

    def wait_for(self, condition, window=None):
        deadline = time.monotonic() + 30
        while not condition():
            app.processEvents()
            if time.monotonic() > deadline:
                self.fail('UI operation timed out: ' + (window.status_label.text() if window else ''))
            time.sleep(.01)
        app.processEvents()

    def test_gui_physical_input_pause_restart_and_shutdown(self):
        window = UIWindow()
        window.show()
        try:
            self.wait_for(lambda: window.ready, window)
            startup = body(window.phone.frame)
            self.assertTrue(window.phone.isEnabled())
            QTest.keyClick(window.phone, Qt.Key.Key_Return)
            self.wait_for(lambda: colors(window.phone.frame) > 1500, window)
            self.assertNotEqual(body(window.phone.frame), startup)
            window.grab().save(str(ROOT / 'reports/original-ui-gui.png'))
            # Pausing releases a held physical contact and freezes execution.
            QTest.keyPress(window.phone, Qt.Key.Key_Right)
            window.toggle_pause()
            self.wait_for(lambda: window.paused, window)
            self.assertFalse(window.phone.pressed)
            paused = body(window.phone.frame)
            QTest.qWait(200)
            app.processEvents()
            self.assertEqual(body(window.phone.frame), paused)
            window.toggle_pause()
            self.wait_for(lambda: not window.paused, window)
            old_worker = window.worker
            window.restart_ui()
            self.wait_for(lambda: window.worker is not old_worker and window.ready, window)
            self.assertLess(colors(window.phone.frame), 1000)
            window.toggle_power()
            self.wait_for(lambda: window.worker is None, window)
            self.assertTrue(window.last_report['source_unchanged'])
        finally:
            window.close()
            self.wait_for(lambda: window.worker is None, window)
            self.assertFalse(window.isVisible())

    def test_close_during_startup(self):
        window = UIWindow(autostart=False)
        window.show()
        window.start_ui()
        window.close()
        self.wait_for(lambda: window.worker is None, window)
        self.assertFalse(window.isVisible())
