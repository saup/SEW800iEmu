"""Install supplied customization files through original guest file APIs, then boot.

Experimental, session-local file provisioning. CPU arguments are set for
ordinary malloc/file/free calls and restored before the original startup
continues. No integrity result, startup mode, status marker or MAIN instruction
is replaced. The VM and its file changes are discarded when the tool exits.
"""
import hashlib
import json
from pathlib import PurePosixPath
from zipfile import ZipFile

from w800.backend import QemuBackend, ROOT
from w800.tools.debug import Debugger
from w800.tools.trace_reset_boot import observe


from w800.guest_files import ANCHOR, OriginalFiles, validate_firmware


def main():
    flash = ROOT / 'firmware/prepared/flash-gdfs.bin'
    initial = flash.read_bytes()
    validate_firmware(flash)
    package = ROOT / 'firmware/W800i_CDA102425_38_EMEA_1.zip'
    report = {'package_sha256': hashlib.sha256(package.read_bytes()).hexdigest(),
              'installed': [], 'guest_instruction_patches': False,
              'debugger_register_setup': True, 'storage_scope': 'temporary QEMU session',
              'status_marker_fabricated': False}
    with QemuBackend(exploratory=True, debug=True, mmio_limit=1000000) as q:
        d = Debugger(q)
        d.sock.settimeout(10)
        d.breakpoint(ANCHOR, thumb=True)
        try:
            d.run()
            if d.registers()[15] != ANCHOR:
                raise RuntimeError('Original customization startup checkpoint was not reached')
            files = OriginalFiles(d)
            try:
                report['custom_directory_lookup'] = files.lookup('/tpa/preset', 'custom')
                report['customize_xml_before'] = files.lookup('/tpa/preset/custom', 'customize.xml')
                with ZipFile(package) as archive:
                    for item in archive.infolist():
                        path = PurePosixPath(item.filename)
                        if item.is_dir() or path.parent != PurePosixPath('tpa/preset/custom'):
                            continue
                        if item.file_size > 15360 or path.name in ('.', '..'):
                            raise ValueError('Unsupported customization member')
                        data = archive.read(item)
                        files.install('/tpa/preset/custom', path.name, data)
                        report['installed'].append({'name': str(path), 'size': len(data),
                                                    'sha256': hashlib.sha256(data).hexdigest(),
                                                    'original_file_readback_matches': True})
                        print(f'Installed and read back {path.name}', flush=True)
                report['customize_xml_after'] = files.lookup('/tpa/preset/custom', 'customize.xml')
            finally:
                files.close()
            d.breakpoint(ANCHOR, enabled=False, thumb=True)
        except (RuntimeError, ValueError, TimeoutError) as error:
            report['installation_error'] = str(error)
        finally:
            q.command('stop')
            d.close()
        if 'installation_error' not in report:
            report['boot_after_installation'] = observe(q, 'customization-installed')
        report['diagnostics'] = q.diagnostics()
    report['source_unchanged'] = flash.read_bytes() == initial
    (ROOT / 'reports/customization-install-boot.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: value for key, value in report.items()
                      if key not in ('boot_after_installation', 'installed')}, indent=2))


if __name__ == '__main__':
    main()
