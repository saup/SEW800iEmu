"""File controls for the existing phone worker, with no separate emulator."""
from pathlib import Path, PurePosixPath

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget)

from .filesystem_gui import preview_text
from .guest_files import guest_path
from .live_filesystem import PREVIEW_LIMIT


class LiveFilesystemPanel(QWidget):
    requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.available = self.busy = False
        self.serial = 0
        self.pending = None
        self.directory = '/tpa/user/other'
        layout = QVBoxLayout(self)
        notice = QLabel('Files in the running phone. Uploads and installations are live; '
                        'Restart UI or Stop UI discards this session’s changes.')
        notice.setWordWrap(True)
        layout.addWidget(notice)
        navigation = QHBoxLayout()
        self.root = QPushButton('Root')
        self.up = QPushButton('Up')
        self.refresh = QPushButton('Refresh')
        for widget in (self.root, self.up, self.refresh):
            navigation.addWidget(widget)
        layout.addLayout(navigation)
        self.path = QLineEdit(self.directory)
        self.path.setPlaceholderText('Absolute phone folder')
        layout.addWidget(self.path)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['Name', 'Type', 'Bytes'])
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setRootIsDecorated(False)
        self.tree.setColumnWidth(0, 190)
        layout.addWidget(self.tree, 3)
        actions = QHBoxLayout()
        self.upload = QPushButton('Upload file…')
        self.export = QPushButton('Export…')
        self.preview_button = QPushButton('Preview')
        for widget in (self.upload, self.export, self.preview_button):
            actions.addWidget(widget)
        layout.addLayout(actions)
        self.install = QPushButton('Install selected JAR on phone')
        layout.addWidget(self.install)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText('Select a file and choose Preview. Double-click a folder to browse.')
        layout.addWidget(self.preview, 2)
        self.status = QLabel('Start the phone UI to browse files.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.root.clicked.connect(lambda: self.browse('/'))
        self.up.clicked.connect(lambda: self.browse(str(PurePosixPath(self.directory).parent)))
        self.refresh.clicked.connect(lambda: self.browse(self.directory))
        self.path.returnPressed.connect(lambda: self.browse(self.path.text()))
        self.tree.itemSelectionChanged.connect(self.update_controls)
        self.tree.itemDoubleClicked.connect(self.open_item)
        self.upload.clicked.connect(self.choose_upload)
        self.export.clicked.connect(self.choose_export)
        self.preview_button.clicked.connect(lambda: self.request_selected('preview'))
        self.install.clicked.connect(lambda: self.request_selected('install'))
        self.update_controls()

    def reset(self):
        self.serial += 1
        self.pending = None
        self.busy = self.available = False
        self.tree.clear()
        self.preview.clear()
        self.status.setText('Waiting for the running phone session…')
        self.update_controls()

    def set_available(self, available):
        if available and not self.available and not self.tree.topLevelItemCount() and not self.busy:
            self.status.setText('Choose Refresh or enter a folder path to browse the running phone.')
        self.available = available
        self.update_controls()

    def selected(self):
        item = self.tree.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def update_controls(self):
        enabled = self.available and not self.busy
        for widget in (self.root, self.up, self.refresh, self.path, self.tree, self.upload):
            widget.setEnabled(enabled)
        entry = self.selected()
        file_selected = enabled and entry is not None and not entry['directory']
        self.export.setEnabled(file_selected)
        self.preview_button.setEnabled(file_selected)
        self.install.setEnabled(file_selected and entry['name'].lower().endswith('.jar'))

    def request(self, operation, path, **extra):
        if not self.available or self.busy:
            return
        try:
            path = guest_path(path)
        except ValueError as error:
            self.status.setText(str(error))
            return
        self.serial += 1
        self.pending = dict(id=self.serial, operation=operation, path=path, **extra)
        self.busy = True
        self.status.setText(f'{operation.capitalize()}: {path}')
        self.update_controls()
        self.requested.emit(self.pending)

    def browse(self, path):
        self.request('list', path)

    def open_item(self, item, _column):
        entry = item.data(0, Qt.ItemDataRole.UserRole)
        if entry['directory']:
            self.browse(entry['path'])
        else:
            self.request('preview', entry['path'])

    def request_selected(self, operation):
        entry = self.selected()
        if entry and not entry['directory']:
            self.request(operation, entry['path'])

    def choose_upload(self):
        source, _ = QFileDialog.getOpenFileName(self, 'Upload into ' + self.directory)
        if not source:
            return
        name = Path(source).name
        existing = [self.tree.topLevelItem(index).data(0, Qt.ItemDataRole.UserRole)
                    for index in range(self.tree.topLevelItemCount())]
        replace = any(row['name'].casefold() == name.casefold() for row in existing)
        if replace and QMessageBox.question(self, 'Replace phone file?',
                f'Replace {name} in {self.directory}?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.request('upload', self.directory.rstrip('/') + '/' + name,
                     source=source, replace=replace)

    def choose_export(self):
        entry = self.selected()
        if not entry or entry['directory']:
            return
        destination, _ = QFileDialog.getSaveFileName(self, 'Export phone file', entry['name'])
        if destination:
            self.request('export', entry['path'], destination=destination)

    def completed(self, response):
        request = response['request']
        if self.pending is None or request['id'] != self.pending['id']:
            return  # A stopped/restarted VM cannot update this session's list.
        self.busy = False
        self.pending = None
        if 'error' in response:
            self.status.setText(response['error'])
            self.update_controls()
            return
        data = response['data']
        operation = request['operation']
        if operation == 'list':
            self.directory = request['path']
            self.path.setText(self.directory)
            self.tree.clear()
            for entry in data:
                item = QTreeWidgetItem([entry['name'], 'Folder' if entry['directory'] else 'File',
                                       '' if entry['directory'] else f"{entry['size']:,}"])
                item.setData(0, Qt.ItemDataRole.UserRole, entry)
                self.tree.addTopLevelItem(item)
            self.status.setText(f'{len(data)} entries in the running phone')
        elif operation == 'preview':
            self.preview.setPlainText(preview_text(data))
            self.status.setText(f'Preview: {len(data):,} bytes (limit {PREVIEW_LIMIT:,})')
        elif operation == 'upload':
            self.status.setText(f"Uploaded and verified {data['size']:,} bytes. The phone has received its content-change notification.")
            self.browse(self.directory)
        elif operation == 'export':
            self.status.setText(f"Exported {data['size']:,} bytes to {data['destination']}")
        elif operation == 'install':
            self.status.setText('Installer opened on the phone. Use the phone keys to answer its dialogs.')
        self.update_controls()
