"""Event-driven terminal for the running phone's native UART0 socket."""
import codecs

from PySide6.QtCore import Qt
from PySide6.QtNetwork import QLocalSocket
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                              QPushButton, QVBoxLayout, QWidget)


class UARTPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.endpoint = None
        self.endpoints = {}
        self.running = False
        self.socket = QLocalSocket(self)
        self.socket.setReadBufferSize(65536)
        self.decoder = codecs.getincrementaldecoder('utf-8')('replace')
        layout = QVBoxLayout(self)
        self.port = QComboBox()
        for number, name in ((0, 'UART0 · AT service'), (1, 'UART1'), (4, 'UART4')):
            self.port.addItem(name, number)
        layout.addWidget(self.port)
        self.port.currentIndexChanged.connect(self.select_port)
        self.status = QLabel('Start the phone to connect to UART0.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumBlockCount(2000)
        self.output.setStyleSheet('font-family: Menlo, monospace; font-size:12px;')
        layout.addWidget(self.output)
        self.command = QLineEdit()
        self.command.setMaxLength(4096)
        self.command.setPlaceholderText('AT command · Enter to send')
        self.send = QPushButton('Send')
        row = QHBoxLayout()
        row.addWidget(self.command, 1)
        row.addWidget(self.send)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.connection = QPushButton('Connect')
        self.clear = QPushButton('Clear output')
        row.addWidget(self.connection)
        row.addWidget(self.clear)
        layout.addLayout(row)
        note = QLabel('UART0 runs the native AT service. Other ports use their firmware protocol.\n'
                      'Commands end with a carriage return. Disconnect here to use an external terminal.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.command.returnPressed.connect(self.send_command)
        self.send.clicked.connect(self.send_command)
        self.clear.clicked.connect(self.output.clear)
        self.connection.clicked.connect(self.toggle_connection)
        self.socket.connected.connect(self.connected)
        self.socket.disconnected.connect(self.disconnected)
        self.socket.readyRead.connect(self.receive)
        self.socket.errorOccurred.connect(self.error)
        self.update_controls()

    def set_endpoint(self, endpoint):
        self.set_endpoints({0: endpoint})

    def set_endpoints(self, endpoints):
        self.reset()
        self.endpoints = {int(port): str(path) for port, path in endpoints.items()
                          if isinstance(port, int)}
        self.select_port()

    def select_port(self, _index=None):
        self.socket.abort()
        self.decoder.reset()
        self.command.clear()
        self.output.clear()
        self.endpoint = self.endpoints.get(self.port.currentData())
        if self.endpoint:
            self.connect_socket()
        else:
            self.status.setText('Selected UART is unavailable in this session.')
            self.update_controls()

    def port_name(self):
        return f'UART{self.port.currentData()}'

    def reset(self):
        self.endpoints = {}
        self.endpoint = None
        self.running = False
        self.socket.abort()
        self.decoder.reset()
        self.command.clear()
        self.status.setText('Phone stopped · UART disconnected.')
        self.update_controls()

    def set_running(self, running):
        self.running = running
        if self.is_connected():
            self.status.setText('Connected to ' + self.port_name() + ('' if running else ' · phone paused'))
        self.update_controls()

    def is_connected(self):
        return self.socket.state() == QLocalSocket.LocalSocketState.ConnectedState

    def update_controls(self):
        enabled = self.running and self.is_connected()
        self.command.setEnabled(enabled)
        self.send.setEnabled(enabled)
        self.connection.setEnabled(self.endpoint is not None)
        self.connection.setText('Disconnect' if self.socket.state() != QLocalSocket.LocalSocketState.UnconnectedState else 'Connect')

    def connect_socket(self):
        if self.endpoint:
            self.decoder.reset()
            self.status.setText('Connecting to ' + self.port_name() + '…')
            self.socket.connectToServer(self.endpoint)
            self.update_controls()

    def toggle_connection(self):
        if self.socket.state() != QLocalSocket.LocalSocketState.UnconnectedState:
            self.socket.abort()
            self.disconnected()
        else:
            self.connect_socket()

    def connected(self):
        self.set_running(self.running)

    def disconnected(self):
        self.status.setText(self.port_name() + ' disconnected.')
        self.update_controls()

    def error(self, _error):
        self.status.setText(self.port_name() + ': ' + self.socket.errorString())
        self.update_controls()

    def send_command(self):
        if not self.running or not self.is_connected():
            return
        data = self.command.text().encode('utf-8') + b'\r'
        if self.socket.bytesToWrite() + len(data) > 65536:
            self.status.setText('UART transmit queue is full; wait before sending again.')
            return
        if self.socket.write(data) < 0:
            self.error(None)
            return
        self.command.clear()
        self.command.setFocus(Qt.FocusReason.OtherFocusReason)

    def receive(self):
        text = self.decoder.decode(bytes(self.socket.readAll()))
        # Render terminal control bytes visibly; firmware output is plain text,
        # never interpreted as HTML or terminal escape sequences.
        text = ''.join(c if c in '\n\t' or c.isprintable() else
                       '' if c == '\r' else f'\\x{ord(c):02x}' for c in text)
        bar = self.output.verticalScrollBar()
        following = bar.value() == bar.maximum()
        cursor = self.output.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(text)
        excess = self.output.document().characterCount() - 131072
        if excess > 0:
            cursor.movePosition(cursor.MoveOperation.Start)
            cursor.movePosition(cursor.MoveOperation.Right, cursor.MoveMode.KeepAnchor, excess)
            cursor.removeSelectedText()
        if following:
            bar.setValue(bar.maximum())
