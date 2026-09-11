"""Create the local emulator bundle and verify which source binary it contains."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from w800.backend import ROOT


def main():
    build = ROOT / 'build'
    source = build / 'qemu-system-arm'
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix='w800-app-', dir=build) as temporary:
        bundle = Path(temporary) / 'W800 QEMU.app'
        contents = bundle / 'Contents'
        executable = contents / 'MacOS/qemu-system-arm'
        executable.parent.mkdir(parents=True)
        shutil.copy2(source, executable)
        shutil.copy2(ROOT / 'qemu/W800-QEMU-Info.plist', contents / 'Info.plist')
        subprocess.run(['codesign', '--force', '--sign', '-',
                        '--preserve-metadata=entitlements', str(bundle)], check=True)
        subprocess.run(['codesign', '--verify', '--strict', str(bundle)], check=True)
        manifest = {'source_sha256': source_sha,
                    'binary_sha256': hashlib.sha256(executable.read_bytes()).hexdigest(),
                    'bundle_identifier': 'local.w800.qemu'}
        destination = build / bundle.name
        # Retain the old inode for any process that is already running it.
        backup = Path(temporary) / 'previous.app'
        if destination.exists():
            destination.rename(backup)
        bundle.rename(destination)
        (build / 'qemu-app.json').write_text(json.dumps(manifest, indent=2) + '\n')


if __name__ == '__main__':
    main()
