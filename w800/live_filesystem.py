"""Bounded file operations on the running UI session's original filesystem.

This adapter owns no VM and no debugger context. Every native call goes through
OriginalUISession.invoke so scheduler, service replies and checkpoints survive.
The caller must serialize operations on the session's worker thread.
"""
import hashlib
import os
from pathlib import Path, PurePosixPath
import struct
import tempfile
import threading
import time

from .guest_files import guest_path
from .java_services import _call_installer_on_mmi, observe_java_state


MAX_TRANSFER = 32 * 1024 * 1024
PREVIEW_LIMIT = 64 * 1024


class LiveFilesystem:
    def __init__(self, session, progress=lambda message: None):
        self.session = session
        self.thread = threading.get_ident()
        self.progress = progress
        self.last_progress = 0

    def pulse(self, message):
        self.session.advance(40)
        if time.monotonic() - self.last_progress >= .25:
            self.last_progress = time.monotonic()
            self.progress(message)

    def check(self):
        if threading.get_ident() != self.thread:
            raise RuntimeError('Phone files must run on the UI session worker')
        self.session.check_cancelled()
        if not self.session.ready:
            raise RuntimeError('The original UI call has not completed')

    def call(self, function, *args):
        self.check()
        result = self.session.invoke(function, *args)
        return result if result < 0x80000000 else result - 0x100000000

    def list_directory(self, path):
        self.check()
        path = guest_path(path)
        f, d = self.session.files, self.session.d
        pointer = f.stage(0, path.encode('utf-16le') + b'\0\0')
        handle = self.call(0x45107738, pointer)
        if handle <= 0:
            raise OSError(f'Firmware could not open folder {path}')
        entries = []
        try:
            for _ in range(10000):
                stat = f.stage(2048, bytes(128))
                name_pointer = self.call(0x451078dc, handle, stat)
                if not name_pointer:
                    break
                if not 0x4c000000 <= name_pointer <= 0x4c7ffdf6:
                    raise OSError('Firmware returned an invalid folder entry')
                raw_name = d.read(name_pointer, 522)
                end = next((index for index in range(0, len(raw_name), 2)
                            if raw_name[index:index + 2] == b'\0\0'), None)
                if end is None:
                    raise OSError('Firmware filename is not terminated')
                name = raw_name[:end].decode('utf-16le')
                if name in ('.', '..'):
                    continue
                if not name or '/' in name or '\\' in name:
                    raise OSError('Firmware returned an invalid filename')
                mode, _, _, size = struct.unpack('<4I', d.read(stat, 16))
                entries.append(dict(name=name, path=path.rstrip('/') + '/' + name,
                                    directory=bool(mode & 0x4000), size=size))
                if len(entries) % 32 == 0:
                    self.pulse(f'Listed {len(entries):,} entries in {path}')
            else:
                raise OSError('Folder exceeds the 10,000-entry limit')
        finally:
            if self.session.ready and not self.session.cancelled():
                self.call(0x451079a8, handle)
        return sorted(entries, key=lambda row: (not row['directory'], row['name'].casefold()))

    def read_file(self, path, limit=MAX_TRANSFER, preview=False):
        self.check()
        path = guest_path(path)
        if not 0 <= limit <= MAX_TRANSFER:
            raise ValueError('Unsupported transfer size')
        f = self.session.files
        pointer = f.stage(0, path.encode('utf-16le') + b'\0\0')
        fd = self.call(0x45105f48, pointer, 1, 0x1b6)
        if fd < 0:
            raise OSError(f'Firmware could not read {path}: {fd}')
        data = bytearray()
        try:
            while True:
                # One additional byte distinguishes exact-limit EOF from a
                # truncated export. Preview explicitly permits truncation.
                count = min(8192, limit + 1 - len(data))
                read = self.call(0x451063d4, fd, f.buffer + 1024, count)
                if not 0 <= read <= count:
                    raise OSError(f'Firmware read failed for {path}: {read}')
                if not read:
                    return bytes(data)
                data.extend(self.session.d.read(f.buffer + 1024, read))
                if len(data) > limit:
                    if preview:
                        return bytes(data[:limit])
                    raise OSError(f'File exceeds the {limit:,}-byte transfer limit')
                self.pulse(f'Read {len(data):,} bytes from {path}')
        finally:
            if self.session.ready and not self.session.cancelled():
                self.call(0x45106254, fd, 0)

    def upload_bytes(self, path, data, replace=False):
        self.check()
        path = PurePosixPath(guest_path(path))
        if len(data) > MAX_TRANSFER:
            raise ValueError('Uploads are limited to 32 MiB per file')
        existing = next((entry for entry in self.list_directory(str(path.parent))
                         if entry['name'].casefold() == path.name.casefold()), None)
        if existing and existing['directory']:
            raise IsADirectoryError(str(path))
        if existing and not replace:
            raise FileExistsError(f'{path.name} already exists; choose Replace to overwrite it')
        f = self.session.files
        parent, leaf = f.names(str(path.parent), path.name)
        fd = self.call(0x44d83be0, parent, leaf, 0x304, 0x180)
        if fd < 0:
            raise OSError(f'Firmware could not create {path}: {fd}')
        try:
            for start in range(0, len(data), 8192):
                chunk = data[start:start + 8192]
                pointer = f.stage(1024, chunk)
                count = self.call(0x44d83c00, fd, pointer, len(chunk))
                if count != len(chunk):
                    raise OSError(f'Incomplete upload: {path} may contain a partial file ({count})')
                self.pulse(f'Uploaded {start + len(chunk):,} / {len(data):,} bytes to {path}')
        finally:
            if self.session.ready and not self.session.cancelled():
                self.call(0x44d83bf8, fd)
        actual = self.read_file(str(path))
        if actual != data:
            raise OSError(f'Upload readback differs: {path} may contain a partial file')
        result = dict(path=str(path), size=len(data), sha256=hashlib.sha256(actual).hexdigest(),
                      replaced=bool(existing))
        self.notify_changed()
        result['native_content_change_event'] = '0x0f6e'
        self.session.provenance.setdefault('live_uploads', []).append(result)
        return result

    def begin_installation(self, path):
        """Submit the native asynchronous installer; leave all choices on LCD."""
        self.check()
        path = guest_path(path)
        if not path.lower().endswith('.jar'):
            raise ValueError('Select a JAR file to install')
        state = observe_java_state(self.session)
        if (not getattr(self.session, '_application_startup_notified', False)
                or state['java_waiting_for_startup'] or state['oaf_flags'] != 0):
            raise OSError('Choose Start phone and wait for the Java service to finish starting')
        if self.session.q.pressed_keys:
            raise OSError('Release the phone keys before starting an installation')
        target = PurePosixPath(path)
        entry = next((row for row in self.list_directory(str(target.parent))
                      if row['name'] == target.name and not row['directory']), None)
        if entry is None:
            raise FileNotFoundError(path)
        jar = self.session.files.stage(1024, path.encode('utf-16le') + b'\0\0')
        correlation = self.session.invoke(0x44c9039c)
        context = self.session.files.stage(12000, struct.pack('<2I', 1, correlation))
        result = _call_installer_on_mmi(self.session, context, 0, jar)
        if result != 1:
            raise OSError(f'Original installer did not accept the request: {result:#x}')
        record = dict(path=path, accepted=result, correlation=correlation,
                      interactive=True, automatic_dialog_answers=0)
        self.session.provenance.setdefault('live_jar_installations', []).append(record)
        self.session.advance(250)
        return record

    def notify_changed(self):
        """Broadcast the original DataBrowser content-change notification.

        Original file/content producers publish event 0xf6e through this UI
        API (for example 44d59e2a). Its native handler 44e0ee84 enumerates the
        current filesystem and updates the existing FileList. No book address,
        synthetic selection or replacement folder entries are supplied.
        """
        self.check()
        self.session.call_on_mmi(0x44e6d2c8, 0xf6e)
        self.session.advance(250)

    def execute(self, request):
        """Worker request contract; host files are read/written on this thread."""
        operation, path = request['operation'], guest_path(request['path'])
        if operation == 'list':
            return self.list_directory(path)
        if operation == 'preview':
            return self.read_file(path, PREVIEW_LIMIT, preview=True)
        if operation == 'upload':
            source = Path(request['source'])
            with source.open('rb') as stream:
                data = stream.read(MAX_TRANSFER + 1)
            return self.upload_bytes(path, data, bool(request.get('replace')))
        if operation == 'export':
            data = self.read_file(path)
            destination = Path(request['destination'])
            fd, temporary = tempfile.mkstemp(prefix='.w800-export-', dir=destination.parent)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(data)
                os.replace(temporary, destination)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            return dict(destination=str(destination), size=len(data),
                        sha256=hashlib.sha256(data).hexdigest())
        if operation == 'install':
            return self.begin_installation(path)
        raise ValueError('Unknown phone file operation')
