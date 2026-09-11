"""Capture an original GDFS_Read result and compare its bytes with the backup.

Read-only GDB breakpoints inspect a real boot; no caller or result is injected.
Only content hashes are recorded because calibration backups can contain IDs.
"""
import hashlib
import json
from pathlib import Path
from w800.backend import QemuBackend, ROOT
from w800.gdfs import parse_backup
from w800.tools.debug import Debugger


def capture(flash_path=None, bank=0, number=29):
    source = {(u.bank, u.number): u.data for u in
              parse_backup((ROOT / 'firmware/W800_GDFS.bin').read_bytes())}
    expected = source[bank, number]
    flash_path = flash_path or ROOT / 'firmware/prepared/flash-gdfs.bin'
    with QemuBackend(exploratory=True, debug=True, mmio_limit=1000000,
                     flash_path=flash_path) as q:
        d = Debugger(q)
        try:
            entry = 0x44c76dd0
            d.breakpoint(entry, thumb=True)
            for _ in range(128):
                d.run()
                registers = d.registers()
                if registers[15] != entry:
                    raise RuntimeError('Boot stopped before GDFS_Read: ' + q.diagnostics())
                if registers[0] - 0x4466ad90 == number and registers[3] == bank:
                    break
                d.step_over_breakpoint(entry, thumb=True)
            else:
                raise RuntimeError('Requested unit was not read during observed startup')
            d.breakpoint(entry, enabled=False, thumb=True)
            return_address = registers[14] & ~1
            d.breakpoint(return_address, thumb=bool(registers[14] & 1))
            d.run()
            returned = d.registers()
            if returned[15] != return_address or returned[13] != registers[13]:
                raise RuntimeError('GDFS_Read did not return to the observed caller')
            if returned[0] != 0:
                raise RuntimeError(f'Original GDFS_Read failed with status {returned[0]}')
            if registers[2] != len(expected):
                raise RuntimeError('Observed read does not request the complete backup unit')
            actual = d.read(registers[1], len(expected))
            if actual != expected:
                raise RuntimeError('Original guest read differs from backup payload')
            return {'entry_pc': hex(entry), 'caller_return_pc': hex(return_address),
                    'bank': bank, 'unit': number, 'length': len(expected),
                    'result': returned[0], 'guest_sha256': hashlib.sha256(actual).hexdigest(),
                    'backup_sha256': hashlib.sha256(expected).hexdigest(),
                    'payload_matches': True, 'guest_instruction_patches': False}
        finally:
            d.close()


if __name__ == '__main__':
    result = capture()
    target = ROOT / 'reports/gdfs-read-result.json'
    target.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
