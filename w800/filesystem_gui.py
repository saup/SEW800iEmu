"""Native, read-only browser for files returned by original firmware APIs."""
from pathlib import PurePosixPath
from queue import Queue
import sys

from PySide6.QtCore import Qt, QThread, Signal, QSaveFile, QIODevice
from PySide6.QtWidgets import (QApplication, QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QSplitter, QTreeWidget, QTreeWidgetItem,
    QPlainTextEdit, QFileDialog, QAbstractItemView)

from .guest_files import FilesystemSession, guest_path


class FilesystemWorker(QThread):
    result = Signal(object)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.requests = Queue()

    def submit(self, operation, path, extra=None):
        self.requests.put((operation, path, extra))

    def stop(self):
        self.requestInterruption()
        self.requests.put(None)

    def run(self):
        session = None
        try:
            while not self.isInterruptionRequested():
                request = self.requests.get()
                if request is None or self.isInterruptionRequested():
                    break
                operation, path, extra = request
                try:
                    if session is None:
                        session = FilesystemSession(self.isInterruptionRequested).start()
                    if operation == 'list':
                        data = session.list_directory(path)
                    else:
                        limit = 128 * 1024 if operation == 'preview' else 64 * 1024 * 1024
                        data = session.read_file(path, limit)
                        if operation == 'export' and len(data) != extra['size']:
                            raise OSError('Export read length differs from the directory entry')
                    self.result.emit((operation, path, data, extra))
                except Exception as error:
                    self.failed.emit(str(error))
                    if session:
                        session.close()
                        session = None
        finally:
            if session:
                session.close()


def preview_text(data):
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        return data.decode('utf-16', errors='replace')
    try:
        text = data.decode('utf-8-sig')
        if all(c.isprintable() or c in '\r\n\t' for c in text):
            return text
    except UnicodeError:
        pass
    lines = []
    for offset in range(0, len(data), 16):
        chunk = data[offset:offset + 16]
        hexes = ' '.join(f'{b:02x}' for b in chunk)
        ascii_text = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
        lines.append(f'{offset:08x}  {hexes:<47}  {ascii_text}')
    return '\n'.join(lines)


class FilesystemBrowser(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('W800i · Firmware filesystem')
        self.resize(1040, 720)
        self.current_path = '/'
        self.busy = False
        self.closing = False
        layout = QVBoxLayout(self)
        hint = QLabel('Browse the mounted firmware files. Read-only inspection in a separate '
                      'QEMU session; the prepared image stays unchanged.')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        navigation = QHBoxLayout()
        self.root_button = QPushButton('Root')
        self.up_button = QPushButton('Up')
        self.path = QLineEdit('/')
        self.path.setAccessibleName('Phone directory path')
        self.go_button = QPushButton('Open folder')
        for widget in (self.root_button, self.up_button, self.path, self.go_button):
            navigation.addWidget(widget)
        layout.addLayout(navigation)
        splitter = QSplitter()
        self.entries = QTreeWidget()
        self.entries.setHeaderLabels(['Name', 'Type', 'Bytes'])
        self.entries.setRootIsDecorated(False)
        self.entries.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.entries.setColumnWidth(0, 250)
        self.entries.setColumnWidth(1, 65)
        self.entries.setAccessibleName('Firmware directory contents')
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.preview.setStyleSheet('font-family:monospace; font-size:12px;')
        self.preview.setPlaceholderText('Double-click a file to preview text or hexadecimal bytes.')
        splitter.addWidget(self.entries)
        splitter.addWidget(self.preview)
        splitter.setSizes([460, 580])
        layout.addWidget(splitter, 1)
        actions = QHBoxLayout()
        self.preview_button = QPushButton('Preview file')
        self.export_button = QPushButton('Export file…')
        actions.addWidget(self.preview_button)
        actions.addWidget(self.export_button)
        actions.addStretch()
        layout.addLayout(actions)
        self.status = QLabel('Starting firmware and mounting filesystems…')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.worker = FilesystemWorker(self)
        self.worker.result.connect(self.completed)
        self.worker.failed.connect(self.failed)
        self.worker.finished.connect(self.worker_finished)
        self.root_button.clicked.connect(lambda: self.navigate('/'))
        self.up_button.clicked.connect(lambda: self.navigate(str(PurePosixPath(self.current_path).parent)))
        self.go_button.clicked.connect(lambda: self.navigate(self.path.text()))
        self.path.returnPressed.connect(lambda: self.navigate(self.path.text()))
        self.entries.itemActivated.connect(self.activate)
        self.entries.itemSelectionChanged.connect(self.update_controls)
        self.preview_button.clicked.connect(self.preview_selected)
        self.export_button.clicked.connect(self.export_selected)
        self.worker.start()
        self.navigate('/')

    def selected(self):
        items = self.entries.selectedItems()
        return items[0].data(0, Qt.ItemDataRole.UserRole) if items else None

    def update_controls(self):
        entry = self.selected()
        available = not self.busy and not self.closing
        for widget in (self.root_button, self.path, self.go_button, self.entries):
            widget.setEnabled(available)
        self.up_button.setEnabled(available and self.current_path != '/')
        is_file = bool(entry and not entry['directory'])
        self.preview_button.setEnabled(available and is_file)
        self.export_button.setEnabled(available and is_file)

    def request(self, operation, path, extra=None):
        if self.busy or self.closing:
            return
        self.busy = True
        self.update_controls()
        self.status.setText(f'{"Opening folder" if operation == "list" else "Reading file"}: {path}…')
        self.worker.submit(operation, path, extra)

    def navigate(self, path):
        try:
            path = guest_path(path.strip())
        except ValueError as error:
            self.status.setText(str(error))
            return
        self.request('list', path)

    def activate(self, item, column=0):
        entry = item.data(0, Qt.ItemDataRole.UserRole)
        if entry['directory']:
            self.navigate(entry['path'])
        else:
            self.preview_selected()

    def preview_selected(self):
        entry = self.selected()
        if entry and not entry['directory']:
            self.request('preview', entry['path'], entry)

    def export_selected(self):
        entry = self.selected()
        if entry and not entry['directory']:
            destination, _ = QFileDialog.getSaveFileName(self, 'Export firmware file', entry['name'])
            if destination:
                self.request('export', entry['path'], {**entry, 'destination': destination})

    def completed(self, result):
        if self.closing:
            return
        operation, path, data, extra = result
        self.busy = False
        if operation == 'list':
            self.current_path = path
            self.path.setText(path)
            self.entries.clear()
            self.preview.clear()
            for entry in data:
                item = QTreeWidgetItem([entry['name'], 'Folder' if entry['directory'] else 'File',
                                       '' if entry['directory'] else f'{entry["size"]:,}'])
                item.setData(0, Qt.ItemDataRole.UserRole, entry)
                self.entries.addTopLevelItem(item)
            self.status.setText(f'{path} · {len(data)} entries' if data else f'{path} · Empty folder')
        elif operation == 'preview':
            self.preview.setPlainText(preview_text(data))
            suffix = ' · Preview truncated to 128 KiB; export to read the complete file' if len(data) < extra['size'] else ''
            self.status.setText(f'{path} · {extra["size"]:,} bytes{suffix}')
        else:
            output = QSaveFile(extra['destination'])
            if not output.open(QIODevice.OpenModeFlag.WriteOnly):
                self.failed(output.errorString())
                return
            if output.write(data) != len(data):
                output.cancelWriting()
                self.failed(output.errorString())
                return
            if not output.commit():
                self.failed(output.errorString())
                return
            self.status.setText(f'Exported {len(data):,} bytes to {extra["destination"]}')
        self.update_controls()

    def failed(self, message):
        self.busy = False
        if not self.closing:
            self.status.setText('Could not complete operation: ' + message)
            self.update_controls()

    def reject(self):
        self.close()

    def closeEvent(self, event):
        if self.worker.isRunning():
            self.closing = True
            self.status.setText('Closing filesystem inspection session…')
            self.update_controls()
            self.worker.stop()
            event.ignore()
        else:
            event.accept()

    def worker_finished(self):
        if self.closing:
            self.close()


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    browser = FilesystemBrowser()
    browser.show()
    sys.exit(app.exec())
