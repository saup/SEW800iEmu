"""Inspect mounted files using original R1L002 guest APIs in a private QEMU VM.

MAIN remains unchanged. The debugger supplies ordinary function arguments in
allocated RAM and restores the suspended caller after each completed call.
"""
from pathlib import PurePosixPath
import struct
from .backend import QemuBackend, ROOT
from .firmware import parse_babe
from .tools.debug import Debugger


ANCHOR = 0x44c80862


class OriginalFiles:
    def __init__(self, debugger):
        self.d = debugger
        self.saved = bytes.fromhex(self.d.packet('g'))
        self.ready = True
        self.buffer = self.call(0x44e4d1b4, 16384)
        if not 0x4c000000 <= self.buffer <= 0x4c7fc000:
            raise RuntimeError('Original allocator did not supply the staging buffer')

    def call(self, function, *arguments):
        if not self.ready:
            raise RuntimeError('Guest call context is unresolved; this VM must be discarded')
        packet = bytearray(self.saved)
        for index, value in enumerate(arguments):
            struct.pack_into('<I', packet, index * 4, value)
        struct.pack_into('<I', packet, 14 * 4, ANCHOR | 1)
        struct.pack_into('<I', packet, 15 * 4, function)
        self.ready = False
        if self.d.packet('G' + packet.hex()) != 'OK':
            raise RuntimeError('Unable to establish original file-call arguments')
        self.d.run()
        regs = self.d.registers()
        if regs[15] != ANCHOR:
            raise RuntimeError(f'Original file call stopped at {regs[15]:08x}')
        result = regs[0]
        if self.d.packet('G' + self.saved.hex()) != 'OK':
            raise RuntimeError('Unable to restore the original caller registers')
        self.ready = True
        return result if result < 0x80000000 else result - 0x100000000

    def stage(self, offset, data):
        if not 0 <= offset <= 16384 - len(data):
            raise ValueError('Staging data exceeds its allocated guest buffer')
        for index in range(0, len(data), 512):
            chunk = data[index:index + 512]
            address = self.buffer + offset + index
            if self.d.packet(f'M{address:x},{len(chunk):x}:' + chunk.hex()) != 'OK':
                raise RuntimeError('Unable to stage file data in allocated RAM')
        return self.buffer + offset

    def names(self, directory, name):
        if any(len(value.encode('utf-16le')) > 510 or '\0' in value
               for value in (directory, name)):
            raise ValueError('File path exceeds the supported guest argument buffer')
        return (self.stage(0, directory.encode('utf-16le') + b'\0\0'),
                self.stage(512, name.encode('utf-16le') + b'\0\0'))

    def lookup(self, directory, name):
        return self.call(0x44d5c6c4, *self.names(directory, name), 0)

    def install(self, directory, name, data):
        if len(data) > 15360:
            raise ValueError('Customization member exceeds the bounded file buffer')
        parent, leaf = self.names(directory, name)
        content = self.stage(1024, data)
        # These flags and permissions are used by MAIN's own file writer.
        fd = self.call(0x44d83be0, parent, leaf, 0x304, 0x180)
        if fd < 0:
            raise RuntimeError(f'Original file create failed for {name}: {fd}')
        try:
            count = self.call(0x44d83c00, fd, content, len(data))
            if count != len(data):
                raise RuntimeError(f'Original file write was incomplete for {name}: {count}')
        finally:
            if self.ready:
                self.call(0x44d83bf8, fd)
        fd = self.call(0x44d83be0, parent, leaf, 1, 0x180)
        if fd < 0:
            raise RuntimeError(f'Original file readback open failed for {name}: {fd}')
        try:
            self.stage(1024, bytes(len(data)))
            count = self.call(0x44d83bf0, fd, content, len(data))
            if count != len(data) or self.d.read(content, count) != data:
                raise RuntimeError(f'Original file readback differs for {name}')
        finally:
            if self.ready:
                self.call(0x44d83bf8, fd)

    def close(self):
        if self.ready:
            self.call(0x44e4d1ac, self.buffer)


def validate_firmware(flash):
    initial = flash.read_bytes()
    source = parse_babe((ROOT / 'firmware/W800_R1L002_MAIN_EU_EMEA_RED49.bin').read_bytes())
    if source.sha256 != '8f0dc6ba55e6e970cfbdcea91e68845f80eaee46bb411e2c2ad966e999de8298':
        raise RuntimeError('Filesystem inspection requires the supported R1L002 MAIN')
    if any(initial[s.address - 0x44000000:s.address - 0x44000000 + len(s.data)] != s.data
           for s in source.segments):
        raise RuntimeError('Prepared MAIN differs from the supported source image')


def guest_path(path):
    if not path.startswith('/') or '\0' in path or '..' in PurePosixPath(path).parts:
        raise ValueError('Use an absolute phone path without parent traversal')
    path = '/' + str(PurePosixPath(path)).lstrip('/')
    if len(path.encode('utf-16le')) > 510:
        raise ValueError('Phone path exceeds 255 UTF-16 units')
    return path


class FilesystemSession:
    """A disposable boot stopped after mounting, before customization startup.

    Browsing calls only open/read/readdir/close. Ordinary boot can write to
    private emulated storage; no changes are saved to the prepared image.
    """
    def __init__(self, cancelled=lambda: False):
        self.cancelled = cancelled
        self.q = self.d = self.files = None

    def check_cancelled(self):
        if self.cancelled():
            raise InterruptedError('Filesystem operation cancelled')

    def start(self):
        self.check_cancelled()
        validate_firmware(ROOT / 'firmware/prepared/flash-gdfs.bin')
        try:
            self.q = QemuBackend(exploratory=True, debug=True, mmio_limit=1000000)
            self.q.start()
            self.d = Debugger(self.q)
            self.d.breakpoint(ANCHOR, thumb=True)
            self.d.run()
            if self.d.registers()[15] != ANCHOR:
                raise RuntimeError('Firmware did not reach the mounted-filesystem checkpoint')
            self.files = OriginalFiles(self.d)
            return self
        except Exception:
            self.close()
            raise

    def list_directory(self, path):
        path = guest_path(path)
        self.check_cancelled()
        f = self.files
        pointer = f.stage(0, path.encode('utf-16le') + b'\0\0')
        handle = f.call(0x45107738, pointer)
        if handle <= 0:
            raise OSError(f'Firmware could not open directory {path}')
        entries = []
        try:
            for _ in range(10000):
                self.check_cancelled()
                stat = f.stage(2048, bytes(128))
                name_pointer = f.call(0x451078dc, handle, stat)
                if name_pointer == 0:
                    break
                if not 0x4c000000 <= name_pointer <= 0x4c7ffdf6:
                    raise RuntimeError('Firmware returned an invalid directory entry')
                raw = self.d.read(name_pointer, 522)
                name = raw.decode('utf-16le').split('\0')[0]
                if name in ('.', '..'):
                    continue
                if not name or '/' in name or '\\' in name:
                    raise RuntimeError('Firmware returned an invalid filename')
                mode, _, _, size = struct.unpack('<4I', self.d.read(stat, 16))
                entries.append({'name': name, 'path': path.rstrip('/') + '/' + name,
                                'directory': bool(mode & 0x4000), 'size': size})
            else:
                raise RuntimeError('Directory exceeds the 10,000-entry inspection limit')
        finally:
            if f.ready:
                f.call(0x451079a8, handle)
        return sorted(entries, key=lambda e: (not e['directory'], e['name'].casefold()))

    def read_file(self, path, limit=64 * 1024 * 1024):
        path = PurePosixPath(guest_path(path))
        if not 0 <= limit <= 64 * 1024 * 1024:
            raise ValueError('Unsupported read limit')
        self.check_cancelled()
        f = self.files
        # Use the same underlying filesystem API as directory enumeration.
        # The application-facing FSX open wrapper used by customization does
        # not resolve /ifs files, despite their mounted directory entries.
        pointer = f.stage(0, str(path).encode('utf-16le') + b'\0\0')
        fd = f.call(0x45105f48, pointer, 1, 0x1b6)
        if fd < 0:
            raise OSError(f'Firmware could not open file {path}')
        result = bytearray()
        try:
            while len(result) < limit:
                self.check_cancelled()
                count = min(8192, limit - len(result))
                pointer = f.buffer + 1024
                read = f.call(0x451063d4, fd, pointer, count)
                if not 0 <= read <= count:
                    raise OSError(f'Firmware read failed for {path}: {read}')
                if read == 0:
                    break
                result.extend(self.d.read(pointer, read))
        finally:
            if f.ready:
                f.call(0x45106254, fd, 0)
        return bytes(result)

    def close(self):
        # Discard the inspection VM, including any unresolved call and private
        # boot-time storage writes. No guest call is needed during shutdown.
        if self.d:
            self.d.close()
            self.d = None
        if self.q:
            self.q.close()
            self.q = None
        self.files = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *args):
        self.close()
