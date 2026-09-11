"""Inventory and inspect actual mounted file bytes through original guest APIs."""
import hashlib
import json
import struct
from w800.backend import ROOT
from w800.guest_files import FilesystemSession


def main():
    flash = ROOT / 'firmware/prepared/flash-gdfs.bin'
    before = hashlib.sha256(flash.read_bytes()).hexdigest()
    report = {'entries': [], 'errors': [], 'source_sha256': before,
              'qemu_sha256': hashlib.sha256((ROOT / 'build/qemu-system-arm').read_bytes()).hexdigest(),
              'guest_instruction_patches': False, 'debugger_register_setup': True,
              'filesystem_write_calls': False,
              'scope': 'Mounted files at the original customization startup checkpoint'}
    needles = {'KGEN': b'KGEN', 'SecLD': b'SecLD', 'ETX': b'ETX',
               'ELF': b'\x7fELF', 'BABE': b'\xbe\xba',
               'DB2010_KGEN_init_pointer': struct.pack('<I', 0xffff12a3),
               'DB2010_ETX_pointer': struct.pack('<I', 0xffff0ddd)}
    with FilesystemSession() as session:
        pending = ['/']
        while pending:
            path = pending.pop(0)
            children = session.list_directory(path)
            report['entries'].extend(children)
            pending.extend(e['path'] for e in children if e['directory'])
            if len(report['entries']) > 10000:
                raise RuntimeError('Inventory limit exceeded')
        for entry in report['entries']:
            if entry['directory']:
                continue
            try:
                data = session.read_file(entry['path'])
                if len(data) != entry['size']:
                    raise RuntimeError('Read length differs from directory entry')
                entry['sha256'] = hashlib.sha256(data).hexdigest()
                entry['matches'] = {name: data.find(needle) for name, needle in needles.items()
                                    if needle in data}
                # Magic hits, especially two-byte BABE in compressed media,
                # are candidates only and do not identify executable code.
                if entry['matches']:
                    print(entry['path'], entry['matches'], flush=True)
            except Exception as error:
                report['errors'].append({'path': entry['path'], 'error': str(error)})
                if not session.files.ready:
                    break
    report['source_unchanged'] = hashlib.sha256(flash.read_bytes()).hexdigest() == before
    report['files_read'] = sum('sha256' in e for e in report['entries'])
    report['bytes_read'] = sum(e['size'] for e in report['entries'] if 'sha256' in e)
    (ROOT / 'reports/mounted-file-content-audit.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'entries'}, indent=2))


if __name__ == '__main__':
    main()
