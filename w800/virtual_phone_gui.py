"""Controls for local virtual-phone services in the running firmware session."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QPushButton,
                              QVBoxLayout, QWidget)
from .host_web import validate_url
from .virtual_network import validate_name, MAX_NAME_LENGTH


class VirtualPhonePanel(QWidget):
    requested = Signal(object)

    def __init__(self):
        super().__init__()
        self.available = False
        self.pending = None
        self.serial = 0
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Virtual network name'))
        self.name = QLineEdit()
        self.name.setMaxLength(MAX_NAME_LENGTH)
        self.name.setPlaceholderText('Your network name')
        layout.addWidget(self.name)
        buttons = QHBoxLayout()
        self.apply = QPushButton('Apply and show standby')
        self.clear = QPushButton('Clear')
        buttons.addWidget(self.apply)
        buttons.addWidget(self.clear)
        layout.addLayout(buttons)
        notice = QLabel('Shows your name in the original phone’s standby screen. '
                        'The radio stays offline. Clear restores the original operator label. '
                        'Restart UI discards the active name.')
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.status = QLabel('Start the phone to apply a name.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addWidget(QLabel('Web page'))
        self.url = QLineEdit('https://example.com/')
        self.url.setMaxLength(2048)
        self.open_web = QPushButton('Open in phone browser')
        layout.addWidget(self.url)
        layout.addWidget(self.open_web)
        self.open_web.clicked.connect(self.request_web)
        self.url.returnPressed.connect(self.request_web)
        layout.addStretch()
        self.apply.clicked.connect(lambda: self.request(self.name.text()))
        self.clear.clicked.connect(lambda: self.request(None))
        self.name.returnPressed.connect(lambda: self.request(self.name.text()))
        self.update_controls()

    def set_available(self, available):
        self.available = available
        self.update_controls()

    def update_controls(self):
        enabled = self.available and self.pending is None
        for widget in (self.name, self.apply, self.clear, self.url, self.open_web):
            widget.setEnabled(enabled)

    def reset(self):
        self.serial += 1
        self.pending = None
        self.available = False
        self.status.setText('Apply a name to the new phone session when it is ready.')
        self.update_controls()

    def request(self, name):
        if not self.available or self.pending is not None:
            return
        try:
            name = validate_name(name)
        except ValueError as error:
            self.status.setText(str(error))
            return
        self.serial += 1
        self.pending = dict(id=self.serial, operation='network_name', name=name)
        self.status.setText('Updating the original standby screen…')
        self.update_controls()
        self.requested.emit(self.pending)

    def request_web(self):
        if not self.available or self.pending is not None:
            return
        try:
            url = validate_url(self.url.text().strip())
        except ValueError as error:
            self.status.setText(str(error))
            return
        self.serial += 1
        self.pending = dict(id=self.serial, operation='web_open', url=url)
        self.status.setText('Loading web page…')
        self.update_controls()
        self.requested.emit(self.pending)

    def completed(self, response):
        if self.pending != response['request']:
            return
        self.pending = None
        if 'error' in response:
            self.status.setText(response['error'])
        elif response['request']['operation'] == 'web_open':
            self.status.setText('Page queued. Loading progress appears beside the phone.')
        else:
            name = response['data']['name']
            self.status.setText(f'Virtual network: {name} · offline' if name else 'Original operator label restored.')
        self.update_controls()
