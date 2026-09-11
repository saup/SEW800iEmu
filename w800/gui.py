"""Native phone photo panel. Only QEMU framebuffer images may enter the LCD."""
from pathlib import Path
import sys
import time
from PySide6.QtCore import Qt, QRectF, QPointF, QTimer, Signal, QEvent
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap, QKeyEvent
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QLabel, QPushButton, QCheckBox,
                             QPlainTextEdit, QSizePolicy)
from .backend import QemuBackend, ROOT

# Coordinates on the 957 x 1643 upright asset, before viewport scaling.
LCD = QRectF(265, 193, 428, 579)
KEYS = {
    'soft_left': QRectF(223,840,188,126), 'walkman': QRectF(418,840,118,91),
    'soft_right': QRectF(546,840,193,126), 'back': QRectF(225,989,153,82),
    'clear': QRectF(578,989,156,82), 'up': QRectF(442,936, 70,40),
    'down': QRectF(442,1065,70,36), 'left': QRectF(385,984,41,72),
    'right': QRectF(531,984,41,72), 'select': QRectF(433,976,91,87),
    # Right-side rail buttons. The volume/zoom rocker sits beside the upper
    # LCD; its top half is volume up (zoom in) and lower half volume down
    # (zoom out). The camera shutter is the long key lower on the same rail.
    'volume_up': QRectF(801,463,25,18), 'volume_down': QRectF(801,481,25,18),
    'camera': QRectF(801,962,25,114),
}
for row, names in enumerate(('123','456','789','*0#')):
    for col, key in enumerate(names):
        KEYS[key] = QRectF((216,388,579)[col], (1106,1199,1291,1386)[row], (162,180,165)[col], 69)
KEYBOARD = {
    Qt.Key.Key_Up:'up', Qt.Key.Key_Down:'down', Qt.Key.Key_Left:'left',
    Qt.Key.Key_Right:'right', Qt.Key.Key_Return:'select', Qt.Key.Key_Enter:'select',
    Qt.Key.Key_Escape:'back', Qt.Key.Key_Backspace:'clear', Qt.Key.Key_F1:'soft_left',
    Qt.Key.Key_F2:'soft_right', Qt.Key.Key_W:'walkman', Qt.Key.Key_F3:'walkman',
    Qt.Key.Key_F12:'power',
    Qt.Key.Key_PageUp:'volume_up', Qt.Key.Key_PageDown:'volume_down',
}

class PhonePanel(QWidget):
    key_changed = Signal(str, bool)
    def __init__(self, screen_only=False):
        super().__init__()
        self.photo = QPixmap(str(ROOT / 'assets/w800-overlay.png'))
        if self.photo.isNull(): raise RuntimeError('Phone overlay is missing')
        self.frame = QImage()
        self.pressed = set()
        self.keyboard_down = {}
        self.mouse_key = None
        self.show_hotspots = False
        self.screen_only = screen_only
        self.setMinimumSize(176,220) if screen_only else self.setMinimumSize(250,430)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Expanding)
        self.setAccessibleName('Sony Ericsson W800i keypad')

    def geometry_scale(self):
        scale = min(self.width()/957, self.height()/1643)
        return scale, (self.width()-957*scale)/2, (self.height()-1643*scale)/2

    def set_frame(self, frame):
        if not frame.isNull() and (frame.width(),frame.height()) != (176,220):
            raise ValueError('W800 LCD must be 176 by 220 pixels')
        self.frame = frame.copy()
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor('#16191e'))
        if self.screen_only:
            target = self.screen_rect()
            p.fillRect(target, Qt.GlobalColor.black)
            if not self.frame.isNull():
                p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
                p.drawImage(target, self.frame)
            p.end()
            return
        scale, x,y = self.geometry_scale()
        p.translate(x,y); p.scale(scale,scale)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform,True)
        p.drawPixmap(QRectF(0,0,957,1643), self.photo, QRectF(self.photo.rect()))
        p.fillRect(LCD, Qt.GlobalColor.black)
        if not self.frame.isNull():
            # Preserve the physical LCD aspect ratio inside the photo aperture.
            h = LCD.width()*220/176
            target = QRectF(LCD.x(), LCD.center().y()-h/2, LCD.width(),h)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform,False)
            p.drawImage(target, self.frame)
        for key, rect in KEYS.items():
            if self.show_hotspots or key in self.pressed:
                p.setPen(QPen(QColor('#ff962f'),3))
                p.setBrush(QColor(255,133,22,90 if key in self.pressed else 18))
                p.drawRoundedRect(rect,8,8)
        p.end()

    def screen_rect(self):
        scale = min(self.width() / 176, self.height() / 220)
        return QRectF((self.width() - 176 * scale) / 2,
                      (self.height() - 220 * scale) / 2, 176 * scale, 220 * scale)

    def hit(self, pos):
        if self.screen_only:
            return None
        scale,x,y = self.geometry_scale()
        point = QPointF((pos.x()-x)/scale,(pos.y()-y)/scale)
        return next((k for k,r in KEYS.items() if r.contains(point)),None)

    def emit_key(self,key,down):
        if down:
            if key in self.pressed:return
            self.pressed.add(key)
        else:
            if key not in self.pressed:return
            self.pressed.remove(key)
        self.key_changed.emit(key,down)
        self.update()

    def mousePressEvent(self,event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(); self.mouse_key = self.hit(event.position())
            if self.mouse_key:self.emit_key(self.mouse_key,True)
    def mouseReleaseEvent(self,event):
        if self.mouse_key and self.mouse_key not in self.keyboard_down.values():
            self.emit_key(self.mouse_key,False)
        self.mouse_key = None
    def mouseMoveEvent(self,event):
        key = self.hit(event.position())
        self.setCursor(Qt.CursorShape.PointingHandCursor if key else Qt.CursorShape.ArrowCursor)
        self.setToolTip(key.replace('_',' ').title() if key else '')
    def release_all(self):
        self.keyboard_down.clear()
        for key in list(self.pressed):self.emit_key(key,False)
        self.mouse_key=None
    def focusOutEvent(self,event):
        self.release_all()
        super().focusOutEvent(event)
    @staticmethod
    def physical_host_key(event):
        if event.nativeScanCode():
            return ('scan', event.nativeScanCode())
        if event.nativeVirtualKey():
            # Cocoa supplies the hardware key code here, with scan code 0.
            return ('native', event.nativeVirtualKey())
        return ('qt', event.key())
    def keyPressEvent(self,event):
        if event.isAutoRepeat():return
        key=KEYBOARD.get(event.key()) or (event.text() if event.text() in '0123456789*#' and event.text() else None)
        if key:
            # Remember the physical host key: its text/Qt key can change
            # when Shift is released before a shifted number-row symbol.
            host_key = self.physical_host_key(event)
            self.keyboard_down.setdefault(host_key, key)
            self.emit_key(self.keyboard_down[host_key],True)
            event.accept()
        else:super().keyPressEvent(event)
    def keyReleaseEvent(self,event):
        if event.isAutoRepeat():return
        key = self.keyboard_down.pop(self.physical_host_key(event), None)
        if key:
            if key not in self.keyboard_down.values() and key != self.mouse_key:
                self.emit_key(key,False)
            event.accept()
        else:super().keyReleaseEvent(event)

class Window(QMainWindow):
    def __init__(self, mask_rom_path=None, chip_id=0x8040, nor_otp_path=None):
        super().__init__()
        self.backend = None
        self.mask_rom_path = mask_rom_path
        self.chip_id = chip_id
        self.nor_otp_path = nor_otp_path
        self.display_available = None
        self.started_at = 0
        self.running = False
        self.setWindowTitle('Sony Ericsson W800i · QEMU')
        self.resize(820,940)
        self.setStyleSheet('''QWidget { background:#16191e; color:#e7e9ed; font-size:13px; }
        QPushButton { background:#292e36; border:1px solid #414853; border-radius:6px; padding:8px 12px; }
        QPushButton:hover { border-color:#ff8a20; } QPushButton:disabled {color:#657080;}
        QPlainTextEdit {background:#101317; border:1px solid #333b45; font-family:monospace; font-size:11px;}
        QLabel { background:transparent; }''')
        container=QWidget();layout=QHBoxLayout(container);self.setCentralWidget(container)
        self.phone=PhonePanel();layout.addWidget(self.phone,3)
        side=QVBoxLayout();layout.addLayout(side,2)
        title=QLabel('W800i');title.setStyleSheet('font-size:26px;font-weight:600;');side.addWidget(title)
        side.addWidget(QLabel('R1L002 · DB2010 · QEMU 10.2.2'))
        self.status=QLabel('Emulator stopped');self.status.setWordWrap(True);side.addWidget(self.status)
        self.power=QPushButton('Start emulator');self.power.clicked.connect(self.toggle_power);side.addWidget(self.power)
        self.pause_button=QPushButton('Pause');self.pause_button.clicked.connect(self.toggle_pause);side.addWidget(self.pause_button)
        self.reset_button=QPushButton('Reset firmware');self.reset_button.clicked.connect(self.reset);side.addWidget(self.reset_button)
        self.power_key=QPushButton('Hold phone power key · F12');side.addWidget(self.power_key)
        self.power_key.pressed.connect(lambda:self.key_changed('power',True))
        self.power_key.released.connect(lambda:self.key_changed('power',False))
        self.power_key.installEventFilter(self)
        for button in (self.power,self.pause_button,self.reset_button,self.power_key):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.explore=QCheckBox('Experimental hardware');self.explore.setChecked(True);side.addWidget(self.explore)
        self.cap=QCheckBox('Pause after 10 seconds (debug)');self.cap.setChecked(False);side.addWidget(self.cap)
        self.filesystem_button=QPushButton('Browse firmware files…')
        self.filesystem_button.clicked.connect(self.browse_filesystem);side.addWidget(self.filesystem_button)
        self.filesystem_browser=None
        hint=QLabel('Original firmware LCD and keypad connected. Phone startup is incomplete; working menus are not yet verified.')
        hint.setWordWrap(True);hint.setStyleSheet('color:#ffc17e;');side.addWidget(hint)
        self.hotspots=QCheckBox('Show button hit areas');self.hotspots.toggled.connect(self.set_hotspots);side.addWidget(self.hotspots)
        instructions=QLabel('Arrows: joystick · Enter: select\nF1 / F2: soft keys · Esc: back\nBackspace: C · W / F3: Walkman\n0–9, * and #: keypad · F12: power')
        instructions.setWordWrap(True);side.addWidget(instructions)
        self.key_status=QLabel('Start the emulator to use the phone keys');self.key_status.setWordWrap(True);side.addWidget(self.key_status)
        self.log=QPlainTextEdit();self.log.setReadOnly(True);self.log.setMaximumBlockCount(300);side.addWidget(self.log,1)
        self.phone.key_changed.connect(self.key_changed)
        self.timer=QTimer(self);self.timer.setInterval(200);self.timer.timeout.connect(self.poll)
        self.update_controls()

    def set_hotspots(self,value):self.phone.show_hotspots=value;self.phone.update()
    def browse_filesystem(self):
        from .filesystem_gui import FilesystemBrowser
        if self.filesystem_browser is None:
            self.filesystem_browser=FilesystemBrowser()
        elif self.filesystem_browser.closing or not self.filesystem_browser.worker.isRunning():
            if self.filesystem_browser.worker.isRunning():return
            self.filesystem_browser.deleteLater()
            self.filesystem_browser=FilesystemBrowser()
        self.filesystem_browser.show();self.filesystem_browser.raise_();self.filesystem_browser.activateWindow()
    def update_controls(self):
        present=self.backend is not None
        self.power.setText('Stop emulator' if present else 'Start emulator')
        self.pause_button.setEnabled(present)
        self.pause_button.setText('Pause' if self.running else 'Resume')
        self.reset_button.setEnabled(present)
        self.power_key.setEnabled(present and self.running)
        self.explore.setEnabled(not present)
    def key_changed(self,key,down):
        label=key.replace('_',' ').title()
        if not self.backend or not self.running:
            self.key_status.setText('Resume the emulator to use the phone keys' if self.backend else
                                    'Start the emulator to use the phone keys')
            return
        try:
            self.backend.send_key(key,down)
            self.key_status.setText(f'{label}: {"pressed" if down else "released"} · sent to phone')
        except (OSError,RuntimeError,ValueError) as error:
            self.key_status.setText(f'{label}: input could not be delivered')
            self.log.appendPlainText(f'Input error: {error}')
    def release_inputs(self):
        self.phone.release_all()
        self.power_key.setDown(False)
        if self.backend:
            try:self.backend.release_keys()
            except (OSError,RuntimeError,ValueError) as error:
                self.log.appendPlainText(f'Input release: {error}')
    def eventFilter(self,watched,event):
        if watched is self.power_key and event.type()==QEvent.Type.FocusOut:
            self.release_inputs()
        return super().eventFilter(watched,event)
    def changeEvent(self,event):
        if event.type()==QEvent.Type.ActivationChange and not self.isActiveWindow() and hasattr(self,'phone'):
            self.release_inputs()
        super().changeEvent(event)
    def toggle_power(self):
        if self.backend:
            self.shutdown();return
        try:
            self.backend=QemuBackend(exploratory=self.explore.isChecked(),mmio_limit=1000000,
                                     mask_rom_path=self.mask_rom_path,chip_id=self.chip_id,
                                     nor_otp_path=self.nor_otp_path)
            self.backend.start();self.backend.command('cont');self.running=True
            self.display_available=None;self.started_at=time.monotonic()
            self.phone.set_frame(QImage());self.log.clear();self.update_controls()
            self.status.setText('Executing original firmware…');self.timer.start();self.phone.setFocus()
        except Exception as error:
            self.log.setPlainText(str(error));self.shutdown();self.status.setText('QEMU could not start')
    def toggle_pause(self):
        if not self.backend:return
        try:
            if self.running:
                self.release_inputs();self.backend.command('stop');self.running=False
                self.status.setText('Firmware paused');self.poll()
            else:
                self.backend.command('cont');self.running=True;self.release_inputs();self.started_at=time.monotonic()
                self.status.setText('Executing original firmware…');self.timer.start();self.phone.setFocus()
            self.update_controls()
        except Exception as error:
            self.log.appendPlainText(str(error));self.shutdown();self.status.setText('QEMU control failed')
    def reset(self):
        if not self.backend:return
        try:
            self.release_inputs();self.backend.command('system_reset');self.backend.pressed_keys.clear();self.backend.command('cont')
            self.running=True;self.display_available=None;self.started_at=time.monotonic()
            self.phone.set_frame(QImage());self.log.clear();self.timer.start();self.update_controls()
            self.status.setText('Executing original firmware…');self.phone.setFocus()
        except Exception as error:
            self.log.setPlainText(str(error));self.shutdown();self.status.setText('QEMU reset failed')
    def poll(self):
        if not self.backend:return
        try:
            status=self.backend.command('query-status')
            budget_reached=self.cap.isChecked() and status['running'] and time.monotonic()-self.started_at>=10
            if budget_reached:
                self.release_inputs();self.backend.command('stop');status=self.backend.command('query-status')
            self.running=status['running'];diagnostic=self.backend.diagnostics().strip()
            if not self.running:
                self.release_inputs()
                self.status.setText('Paused after 10-second debug run' if budget_reached else
                    'Stopped: internal ROM bytes are missing' if 'W800_MASK_ROM_UNAVAILABLE' in diagnostic else
                    'Paused at development access limit' if 'W800_MMIO_LIMIT' in diagnostic else
                    'Boot stopped at unmodeled hardware' if 'W800_UNMODELED' in diagnostic else 'Firmware paused')
                report=self.backend.snapshot()
                self.log.setPlainText(diagnostic+'\n'+report['registers'].split('s00=')[0]
                    +'\nGuest RAM strings (may be stale or partial):\n'+'\n'.join(report['guest_diagnostic_strings']))
                self.timer.stop()
            else:
                self.status.setText('Original firmware running · live LCD')
            if self.display_available is not False:
                try:
                    frame=QImage(str(self.backend.framebuffer_path()))
                    if frame.isNull():raise RuntimeError('QEMU returned an empty display capture')
                    if (frame.width(),frame.height())==(176,220):
                        self.phone.set_frame(frame);self.display_available=True
                    else:self.display_available=False
                except RuntimeError as error:
                    if 'no graphical display' in str(error).lower():self.display_available=False
                    else:raise
            self.update_controls()
        except Exception as error:
            self.log.setPlainText(str(error));self.shutdown();self.status.setText('QEMU connection ended')
    def shutdown(self):
        self.timer.stop();self.release_inputs()
        if self.backend:self.backend.close();self.backend=None
        self.running=False;self.phone.set_frame(QImage());self.display_available=None
        self.update_controls();self.status.setText('Emulator stopped')
        self.key_status.setText('Start the emulator to use the phone keys')
    def closeEvent(self,event):
        self.shutdown()
        if self.filesystem_browser:self.filesystem_browser.close()
        event.accept()

def main(mask_rom_path=None, chip_id=0x8040, nor_otp_path=None):
    app=QApplication.instance() or QApplication(sys.argv)
    window=Window(mask_rom_path=mask_rom_path,chip_id=chip_id,nor_otp_path=nor_otp_path)
    window.show();sys.exit(app.exec())
