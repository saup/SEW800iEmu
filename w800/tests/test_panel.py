import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import unittest
from PySide6.QtCore import QPoint, Qt, QEvent
from PySide6.QtGui import QImage, QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from w800.gui import PhonePanel, KEYS

app=QApplication.instance() or QApplication([])
class PanelTests(unittest.TestCase):
    def setUp(self):
        self.panel=PhonePanel();self.panel.show();app.processEvents()
    def tearDown(self):self.panel.close()
    def test_hit_areas_at_multiple_scales(self):
        for width,height in ((420,800),(800,480),(300,700)):
            self.panel.resize(width,height)
            scale,x,y=self.panel.geometry_scale()
            for key,rect in KEYS.items():
                point=QPoint(round(x+rect.center().x()*scale),round(y+rect.center().y()*scale))
                self.assertEqual(self.panel.hit(point),key)
    def test_mouse_and_keyboard_press_release(self):
        events=[];self.panel.key_changed.connect(lambda k,v:events.append((k,v)))
        self.panel.resize(420,800)
        scale,x,y=self.panel.geometry_scale();r=KEYS['5']
        point=QPoint(round(x+r.center().x()*scale),round(y+r.center().y()*scale))
        QTest.mouseClick(self.panel,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point)
        QTest.keyClick(self.panel,Qt.Key.Key_Up)
        self.assertEqual(events,[('5',True),('5',False),('up',True),('up',False)])
        self.assertFalse(self.panel.pressed)
    def test_frame_size_contract(self):
        with self.assertRaises(ValueError):self.panel.set_frame(QImage(320,240,QImage.Format.Format_RGB32))
        frame=QImage(176,220,QImage.Format.Format_RGB32);frame.fill(Qt.GlobalColor.black)
        self.panel.set_frame(frame)
        self.assertEqual(self.panel.frame.size(),frame.size())
    def test_shifted_symbol_release_uses_the_pressed_physical_key(self):
        events=[];self.panel.key_changed.connect(lambda k,v:events.append((k,v)))
        # Cocoa reports its hardware key code as nativeVirtualKey with a
        # zero nativeScanCode. Shift may be released before the number key.
        for scan_code, virtual_key in ((30, 0), (0, 20)):
            with self.subTest(scan_code=scan_code, virtual_key=virtual_key):
                events.clear()
                QApplication.sendEvent(self.panel, QKeyEvent(QEvent.Type.KeyPress,
                    Qt.Key.Key_NumberSign, Qt.KeyboardModifier.ShiftModifier,
                    scan_code, virtual_key, 0, '#'))
                QApplication.sendEvent(self.panel, QKeyEvent(QEvent.Type.KeyRelease,
                    Qt.Key.Key_3, Qt.KeyboardModifier.NoModifier,
                    scan_code, virtual_key, 0, '3'))
                self.assertEqual(events, [('#', True), ('#', False)])
                self.assertFalse(self.panel.pressed)
                self.assertFalse(self.panel.keyboard_down)

    def test_two_host_keys_mapped_to_select_preserve_held_input(self):
        events=[];self.panel.key_changed.connect(lambda k,v:events.append((k,v)))
        QTest.keyPress(self.panel, Qt.Key.Key_Return)
        QTest.keyPress(self.panel, Qt.Key.Key_Enter)
        QTest.keyRelease(self.panel, Qt.Key.Key_Return)
        self.assertEqual(events, [('select', True)])
        QTest.keyRelease(self.panel, Qt.Key.Key_Enter)
        self.assertEqual(events, [('select', True), ('select', False)])

class WindowInputTests(unittest.TestCase):
    def test_widget_routes_keys_to_real_matrix_and_releases_focus(self):
        from w800.gui import Window
        from w800.backend import QemuBackend, ROOT
        from w800.tests.test_storage import Bus
        if not (ROOT/'build/qemu-system-arm').exists():self.skipTest('Local QEMU build required')
        window=Window();window.show();app.processEvents()
        window.backend=QemuBackend(bus_test=True);window.backend.start()
        window.backend.command('cont');window.running=True;window.update_controls()
        bus=Bus(window.backend)
        def row(n):
            bus.put(0xfb010002,0x3f & ~(1<<n),'w')
            return (bus.get(0xfb010002,'w')>>6)|0xe0
        try:
            self.assertFalse(window.cap.isChecked())
            window.phone.setFocus();app.processEvents()
            QTest.keyPress(window.phone,Qt.Key.Key_5)
            bus.command('clock_step 6000000')
            self.assertEqual(row(2),0xfd)
            # Focus loss sends a real release, so contacts do not stick.
            window.hotspots.setFocus();app.processEvents()
            bus.command('clock_step 6000000')
            self.assertEqual(row(2),0xff)
            self.assertFalse(window.backend.pressed_keys)
            window.phone.setFocus();app.processEvents()
            QTest.keyPress(window.phone,Qt.Key.Key_F12)
            bus.command('clock_step 6000000')
            self.assertEqual(row(3),0xfe)
            QTest.keyRelease(window.phone,Qt.Key.Key_F12)
            bus.command('clock_step 6000000')
            self.assertEqual(row(3),0xff)
            # Host volume shortcuts reach the emulated phone's side-key
            # matrix, rather than changing the host speaker setting.
            for key, matrix_row in ((Qt.Key.Key_PageUp, 3), (Qt.Key.Key_PageDown, 2)):
                QTest.keyPress(window.phone, key)
                bus.command('clock_step 6000000')
                self.assertEqual(row(matrix_row), 0xef)
                QTest.keyRelease(window.phone, key)
                bus.command('clock_step 6000000')
                self.assertEqual(row(matrix_row), 0xff)
            self.assertFalse(window.backend.pressed_keys)
            QTest.keyPress(window.phone,Qt.Key.Key_5)
            window.toggle_pause()
            self.assertFalse(window.running)
            self.assertFalse(window.backend.pressed_keys)
            window.toggle_pause()
            self.assertTrue(window.running)
            self.assertEqual(row(2),0xff)
            window.phone.set_frame(QImage(176,220,QImage.Format.Format_RGB32))
            window.shutdown()
            self.assertTrue(window.phone.frame.isNull())
        finally:
            bus.close();window.close()
