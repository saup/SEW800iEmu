"""Native controls for the original firmware UI component session."""
from queue import Empty, Queue
import sys

from PySide6.QtCore import QEvent, QThread, QTimer, Signal, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (QApplication, QCheckBox, QDockWidget, QHBoxLayout, QLabel,
    QMainWindow, QPushButton, QVBoxLayout, QWidget)

from .guest_ui import OriginalUISession
from .gui import PhonePanel
from .display_gui import DisplayWindow
from .live_filesystem import LiveFilesystem
from .live_filesystem_gui import LiveFilesystemPanel
from .virtual_phone_gui import VirtualPhonePanel
from .uart_gui import UARTPanel


class UIWorker(QThread):
    frame_ready = Signal(QImage)
    status = Signal(str)
    active = Signal(bool)
    failed = Signal(str)
    evidence = Signal(object)
    filesystem_result = Signal(object)
    filesystem_progress = Signal(str)
    service_result = Signal(object)
    uart_endpoint = Signal(object)

    def __init__(self, parent=None, audio_output='coreaudio', game_boost=False, host_camera=True):
        super().__init__(parent)
        self.requests = Queue()
        self.audio_output = audio_output
        self.game_boost = game_boost
        self.host_camera = host_camera

    def submit(self, operation, value=None):
        self.requests.put((operation, value))

    def stop(self):
        self.requestInterruption()
        self.requests.put(('stop', None))

    def run(self):
        session = OriginalUISession(self.isInterruptionRequested, self.status.emit,
                                    default_theme=True, audio_output=self.audio_output,
                                    game_boost=self.game_boost, host_camera=self.host_camera,
                                    start_phone_standby=True, uart=True, host_web=True)
        try:
            session.start()
            self.uart_endpoint.emit({port: str(path) for port, path in session.q.uart_paths.items()})
            def progress(message):
                self.filesystem_progress.emit(message)
                self.frame_ready.emit(session.frame())
            files = LiveFilesystem(session, progress)
            paused = False
            self.frame_ready.emit(session.frame())
            self.active.emit(True)
            while not self.isInterruptionRequested():
                try:
                    operation, value = (self.requests.get(timeout=.1) if paused
                                        else self.requests.get_nowait())
                except Empty:
                    operation, value = 'tick', None
                if self.isInterruptionRequested() or operation == 'stop':
                    break
                if operation == 'boost':
                    session.set_game_boost(value)
                elif operation == 'service':
                    try:
                        if paused:
                            raise OSError('Resume the phone before changing virtual services')
                        for key in tuple(session.q.pressed_keys):
                            session.advance(50, (key, False))
                        if value['operation'] == 'network_name':
                            data = session.set_virtual_network_name(value['name'])
                        elif value['operation'] == 'web_open':
                            data = session.host_browser.open(value['url'])
                        else:
                            raise ValueError('Unknown virtual phone service')
                        self.service_result.emit(dict(request=value, data=data))
                    except InterruptedError:
                        raise
                    except Exception as error:
                        if not session.ready:
                            raise
                        self.service_result.emit(dict(request=value, error=str(error)))
                elif operation == 'filesystem':
                    try:
                        if paused:
                            raise OSError('Resume the phone before accessing its files')
                        for key in tuple(session.q.pressed_keys):
                            session.advance(50, (key, False))
                        data = files.execute(value)
                        self.filesystem_result.emit(dict(request=value, data=data))
                    except InterruptedError:
                        raise
                    except Exception as error:
                        if not session.ready:
                            raise
                        self.filesystem_result.emit(dict(request=value, error=str(error)))
                elif operation == 'pause':
                    for key in tuple(session.q.pressed_keys):
                        session.advance(50, (key, False))
                    paused = bool(value)
                    if self.host_camera:
                        session.q.command('qom-set', {'path': '/machine',
                            'property': 'camera-paused', 'value': paused})
                    self.active.emit(not paused)
                    self.status.emit('Firmware paused' if paused else 'Original firmware UI running')
                elif not paused:
                    # Boost samples the actual QMP display while the CPU is
                    # running, rather than stopping it for every host frame.
                    # Preserve established contact time for phone keys.
                    session.advance(100 if operation == 'key' else session.idle_window_ms,
                                    value if operation == 'key' else None,
                                    frame_callback=(lambda: self.frame_ready.emit(session.frame()))
                                    if session.game_boost else None)
                if not paused or operation == 'pause':
                    self.frame_ready.emit(session.frame())
        except InterruptedError:
            pass
        except Exception as error:
            session.provenance['stop'] = str(error) or 'Original firmware stopped responding'
            self.failed.emit(str(error) or 'Original firmware stopped responding')
        finally:
            try:
                self.evidence.emit(session.report())
            finally:
                session.close()


class UIWindow(QMainWindow):
    def __init__(self, autostart=True, audio_output='coreaudio', host_camera=True):
        super().__init__()
        self.worker = None
        self.ready = False
        self.paused = False
        self.stopping = False
        self.closing = False
        self.restart_pending = False
        self.failure = None
        self.last_report = None
        self.audio_output = audio_output
        self.filesystem_busy = False
        self.service_busy = False
        self.setWindowTitle('Sony Ericsson W800i · Original firmware UI')
        self.resize(1180, 920)
        self.setStyleSheet('''QWidget {background:#16191e; color:#e7e9ed; font-size:13px;}
            QLabel {background:transparent;}
            QPushButton {background:#292e36; border:1px solid #414853;
                border-radius:6px; padding:9px 12px;}
            QPushButton:hover {border-color:#ff8a20;}
            QPushButton:disabled {color:#657080;}''')
        container = QWidget()
        layout = QHBoxLayout(container)
        self.setCentralWidget(container)
        self.phone = PhonePanel()
        self.display_window = DisplayWindow(self)
        self.display_window.screen.key_changed.connect(self.send_key)
        layout.addWidget(self.phone, 3)
        side = QVBoxLayout()
        layout.addLayout(side, 2)
        title = QLabel('W800i')
        title.setStyleSheet('font-size:28px; font-weight:600;')
        side.addWidget(title)
        side.addWidget(QLabel('Original R1L002 firmware · QEMU'))
        self.status_label = QLabel('UI session stopped')
        self.status_label.setWordWrap(True)
        side.addWidget(self.status_label)
        self.power = QPushButton('Start UI')
        self.power.clicked.connect(self.toggle_power)
        side.addWidget(self.power)
        self.pause = QPushButton('Pause')
        self.pause.clicked.connect(self.toggle_pause)
        side.addWidget(self.pause)
        self.restart = QPushButton('Restart UI')
        self.restart.clicked.connect(self.restart_ui)
        side.addWidget(self.restart)
        self.hotspots = QCheckBox('Show button areas')
        self.hotspots.toggled.connect(self.show_hotspots)
        side.addWidget(self.hotspots)
        self.game_boost = QCheckBox('Game boost (higher CPU use)')
        self.game_boost.setToolTip('Smoother display updates with fewer diagnostic pauses. '
                                  'Phone timers and audio speed stay unchanged. Applies without restarting.')
        self.game_boost.toggled.connect(self.set_game_boost)
        side.addWidget(self.game_boost)
        self.enlarge = QPushButton('Enlarge screen…')
        self.enlarge.clicked.connect(self.show_display)
        side.addWidget(self.enlarge)
        self.camera = QCheckBox('Use host camera (next start)')
        self.camera.setChecked(host_camera)
        self.camera.setToolTip('The original Camera app uses the Mac camera. '
                               'Changes apply when you next start or restart the phone.')
        side.addWidget(self.camera)
        for control in (self.power, self.pause, self.restart, self.hotspots, self.game_boost):
            # Mouse use of host controls keeps typing on the phone. Tab
            # navigation can still deliberately focus the host controls.
            control.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        help_text = QLabel('Click the phone buttons or use the keyboard.\n\n'
            'Arrows · Navigate\nEnter · Select\nF1 / F2 · Soft keys\n'
            'Esc · Back\nBackspace · Clear\nW · Walkman\n'
            'Page Up / Down · Phone volume\n0–9, * and # · Keypad\n'
            'Text: number keys · Hold * for T9 / multi-tap\n'
            'Choose English under Writing language for T9.')
        help_text.setWordWrap(True)
        side.addWidget(help_text)
        side.addStretch()
        note = QLabel('Start phone opens standby; its Menu soft key opens the phone menu. Music only opens Walkman. '
                      'MIDI, supported MP3 ringtones and videos play through QEMU to host audio. '
                      'Some codecs and services are still being completed. '
                      'Restart UI returns to the startup menu.')
        note.setWordWrap(True)
        note.setStyleSheet('color:#a5acb7;')
        side.addWidget(note)
        self.phone.key_changed.connect(self.send_key)
        self.filesystem_panel = LiveFilesystemPanel()
        self.filesystem_panel.requested.connect(self.request_filesystem)
        self.filesystem_dock = QDockWidget('Running phone files', self)
        self.filesystem_dock.setWidget(self.filesystem_panel)
        self.filesystem_dock.setMinimumWidth(380)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.filesystem_dock)
        view_menu = self.menuBar().addMenu('View')
        view_menu.addAction(self.filesystem_dock.toggleViewAction())
        view_menu.addAction('Enlarge screen…', self.show_display)
        self.virtual_panel = VirtualPhonePanel()
        self.virtual_panel.requested.connect(self.request_service)
        self.virtual_dock = QDockWidget('Virtual phone', self)
        self.virtual_dock.setWidget(self.virtual_panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.virtual_dock)
        self.tabifyDockWidget(self.filesystem_dock, self.virtual_dock)
        self.filesystem_dock.raise_()
        view_menu.addAction(self.virtual_dock.toggleViewAction())
        self.uart_panel = UARTPanel()
        self.uart_dock = QDockWidget('UART terminal', self)
        self.uart_dock.setWidget(self.uart_panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.uart_dock)
        self.tabifyDockWidget(self.virtual_dock, self.uart_dock)
        view_menu.addAction(self.uart_dock.toggleViewAction())
        self.filesystem_dock.raise_()
        self.update_controls()
        if autostart:
            QTimer.singleShot(0, self.start_ui)

    def show_hotspots(self, enabled):
        self.phone.show_hotspots = enabled
        self.phone.update()

    def show_display(self):
        self.release_keys()
        self.display_window.show()
        self.display_window.raise_()
        self.display_window.activateWindow()
        self.display_window.screen.setFocus()

    def set_frame(self, frame):
        self.phone.set_frame(frame)
        self.display_window.screen.set_frame(frame)

    def release_keys(self):
        self.phone.release_all()
        self.display_window.screen.release_all()

    def set_game_boost(self, enabled):
        if self.worker and not self.stopping:
            self.worker.submit('boost', enabled)

    def update_controls(self):
        live = self.worker is not None
        self.power.setText('Stopping…' if self.stopping else 'Stop UI' if live else 'Start UI')
        self.power.setEnabled(not self.stopping and not self.closing)
        self.pause.setText('Resume' if self.paused else 'Pause')
        self.pause.setEnabled(self.ready and not self.stopping and not self.filesystem_busy and not self.service_busy)
        self.restart.setEnabled(live and not self.stopping)
        self.game_boost.setEnabled(not self.stopping and not self.closing)
        self.camera.setEnabled(not self.stopping and not self.closing)
        self.phone.setEnabled(self.ready and not self.paused and not self.stopping
                              and not self.filesystem_busy and not self.service_busy)
        self.display_window.screen.setEnabled(self.phone.isEnabled())
        self.filesystem_panel.set_available(self.ready and not self.paused and not self.stopping and not self.service_busy)
        self.uart_panel.set_running(self.ready and not self.paused and not self.stopping)
        self.virtual_panel.set_available(self.ready and not self.paused and not self.stopping and not self.filesystem_busy)

    def start_ui(self):
        if self.worker or self.closing:
            return
        self.ready = self.paused = self.stopping = False
        self.failure = None
        self.filesystem_busy = False
        self.service_busy = False
        self.filesystem_panel.reset()
        self.virtual_panel.reset()
        self.uart_panel.reset()
        self.set_frame(QImage())
        self.worker = UIWorker(self, audio_output=self.audio_output,
                               game_boost=self.game_boost.isChecked(),
                               host_camera=self.camera.isChecked())
        self.worker.frame_ready.connect(self.set_frame)
        self.worker.status.connect(self.status_label.setText)
        self.worker.active.connect(self.set_active)
        self.worker.failed.connect(self.failed)
        self.worker.evidence.connect(self.store_report)
        self.worker.finished.connect(self.worker_finished)
        self.worker.filesystem_result.connect(self.filesystem_completed)
        self.worker.filesystem_progress.connect(self.filesystem_panel.status.setText)
        self.worker.service_result.connect(self.service_completed)
        self.worker.uart_endpoint.connect(self.connect_uart)
        self.status_label.setText('Starting firmware…')
        self.worker.start()
        self.update_controls()

    def connect_uart(self, endpoint):
        if self.worker and not self.stopping and not self.closing:
            self.uart_panel.set_endpoints(endpoint)
            self.uart_panel.set_running(self.ready and not self.paused)

    def set_active(self, active):
        if self.stopping or self.closing:
            return
        self.ready = True
        self.paused = not active
        self.update_controls()
        if active:
            self.phone.setFocus()

    def failed(self, message):
        self.failure = message
        self.status_label.setText(message)

    def store_report(self, report):
        self.last_report = report

    def send_key(self, key, down):
        if self.worker and self.ready and not self.paused and not self.stopping and not self.filesystem_busy and not self.service_busy:
            self.worker.submit('key', (key, down))

    def request_service(self, request):
        if self.worker and self.ready and not self.paused and not self.stopping and not self.filesystem_busy:
            self.release_keys()
            self.service_busy = True
            self.worker.submit('service', request)
            self.update_controls()

    def service_completed(self, response):
        if self.virtual_panel.pending != response['request']:
            return
        self.service_busy = False
        self.virtual_panel.completed(response)
        self.update_controls()
        self.phone.setFocus()

    def request_filesystem(self, request):
        if self.worker and self.ready and not self.paused and not self.stopping and not self.service_busy:
            self.release_keys()
            self.filesystem_busy = True
            self.worker.submit('filesystem', request)
            self.update_controls()

    def filesystem_completed(self, response):
        if self.filesystem_panel.pending != response['request']:
            return
        self.filesystem_busy = False
        self.filesystem_panel.completed(response)
        self.update_controls()
        if response['request']['operation'] == 'install' and 'error' not in response:
            self.phone.setFocus()

    def toggle_power(self):
        if self.worker:
            self.stop_ui()
        else:
            self.start_ui()

    def stop_ui(self):
        if self.worker and not self.stopping:
            self.release_keys()
            self.stopping = True
            self.uart_panel.reset()
            self.worker.stop()
            self.status_label.setText('Stopping UI session…')
            self.update_controls()

    def restart_ui(self):
        self.restart_pending = True
        self.stop_ui()

    def toggle_pause(self):
        if self.worker and self.ready and not self.stopping:
            self.release_keys()
            self.worker.submit('pause', not self.paused)
            self.pause.setEnabled(False)

    def worker_finished(self):
        worker = self.worker
        self.worker = None
        if worker:
            worker.deleteLater()
        self.ready = self.paused = self.stopping = False
        self.filesystem_busy = False
        self.service_busy = False
        self.filesystem_panel.reset()
        self.virtual_panel.reset()
        self.uart_panel.reset()
        self.release_keys()
        self.update_controls()
        if self.closing:
            self.close()
        elif self.restart_pending:
            self.restart_pending = False
            self.start_ui()
        elif not self.failure:
            self.set_frame(QImage())
            self.status_label.setText('UI session stopped')

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ActivationChange and not self.isActiveWindow() and hasattr(self, 'phone'):
            self.release_keys()
        super().changeEvent(event)

    def closeEvent(self, event):
        if self.worker:
            self.closing = True
            self.restart_pending = False
            self.stop_ui()
            event.ignore()
        else:
            self.display_window.close()
            event.accept()


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    window = UIWindow()
    window.show()
    return app.exec()
