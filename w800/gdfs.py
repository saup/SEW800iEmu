"""Convert the supplied W800 GDFS unit backup into an initial NOR bank image.

This creates storage metadata understood by the original MAIN GDFS driver.
It never patches MAIN/FS instructions or changes any backup unit's payload.
The inferred v0 layout and evidence are documented in reports/gdfs-layout.md.
"""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import struct
import tempfile

from w800.firmware import FLASH_BASE, FLASH_SIZE

GDFS_BASE = 0x45f00000
GDFS_SIZE = 0x100000
BLOCK_SIZE = 0x20000
BANK_COUNT = 7
RECORD_SIZE = 16
MAX_BACKUP_SIZE = GDFS_SIZE
MAX_RECORDS = BANK_COUNT * ((BLOCK_SIZE - 4) // (RECORD_SIZE + 4))


@dataclass(frozen=True)
class Unit:
    bank: int
    number: int
    data: bytes


def parse_backup(data: bytes) -> tuple[Unit, ...]:
    if not 4 <= len(data) <= MAX_BACKUP_SIZE:
        raise ValueError('GDFS backup size outside supported bounds')
    count, = struct.unpack_from('<I', data)
    if not 1 <= count <= MAX_RECORDS:
        raise ValueError('Invalid GDFS record count')
    records = []
    seen = set()
    position = 4
    for index in range(count):
        if position + 7 > len(data):
            raise ValueError(f'Truncated GDFS header at record {index}')
        bank, number, length = struct.unpack_from('<BHI', data, position)
        position += 7
        if bank >= BANK_COUNT:
            raise ValueError(f'Invalid GDFS bank {bank} at record {index}')
        if number in seen:
            raise ValueError(f'Duplicate GDFS unit {bank}:{number}')
        if not 0 < length <= BLOCK_SIZE - 4 - RECORD_SIZE:
            raise ValueError(f'Invalid GDFS payload length at record {index}')
        if position + length > len(data):
            raise ValueError(f'Truncated GDFS payload at record {index}')
        seen.add(number)
        records.append(Unit(bank, number, data[position:position + length]))
        position += length
    if position != len(data):
        raise ValueError('Unexpected trailing GDFS data')
    return tuple(records)


def encode_banks(records: tuple[Unit, ...]) -> bytes:
    """Seven active v0 banks and one erased spare, preserving record order."""
    region = bytearray(b'\xff') * GDFS_SIZE
    seen = set()
    positions = [4] * BANK_COUNT
    counts = [0] * BANK_COUNT
    for bank in range(BANK_COUNT):
        struct.pack_into('<BBH', region, bank * BLOCK_SIZE, 0x1f, bank, 0)
    for unit in records:
        if not 0 <= unit.bank < BANK_COUNT or not 0 <= unit.number <= 0xffff:
            raise ValueError('GDFS bank or unit number outside supported bounds')
        if unit.number in seen:
            raise ValueError(f'Duplicate GDFS unit {unit.bank}:{unit.number}')
        if not unit.data:
            raise ValueError('Empty GDFS payload')
        seen.add(unit.number)
        start = positions[unit.bank]
        end = start + len(unit.data)
        next_start = (end + 3) & ~3
        descriptor = BLOCK_SIZE - (counts[unit.bank] + 1) * RECORD_SIZE
        if next_start > descriptor:
            raise ValueError(f'GDFS payload/index overlap in bank {unit.bank}')
        base = unit.bank * BLOCK_SIZE
        region[base + start:base + end] = unit.data
        struct.pack_into('<HHIII', region, base + descriptor,
                         0x3ff, unit.number, start, len(unit.data), 0xffffffff)
        positions[unit.bank] = next_start
        counts[unit.bank] += 1
    return bytes(region)


def combine_flash(flash: bytes, records: tuple[Unit, ...]) -> bytes:
    if len(flash) != FLASH_SIZE:
        raise ValueError('Expected the prepared 32 MiB W800 NOR image')
    start = GDFS_BASE - FLASH_BASE
    if flash[start:start + GDFS_SIZE] != b'\xff' * GDFS_SIZE:
        raise ValueError('GDFS destination contains data; refusing to overwrite it')
    region = encode_banks(records)
    return flash[:start] + region + flash[start + GDFS_SIZE:]


def prepare(flash_path: Path, backup_path: Path, output: Path) -> dict:
    flash_path, backup_path, output = map(Path, (flash_path, backup_path, output))
    manifest_path = output.with_suffix('.manifest.json')
    inputs = {flash_path.resolve(), backup_path.resolve()}
    if output.resolve() in inputs or manifest_path.resolve() in inputs:
        raise ValueError('Output must differ from both original inputs')
    if flash_path.stat().st_size != FLASH_SIZE:
        raise ValueError('Expected the prepared 32 MiB W800 NOR image')
    if backup_path.stat().st_size > MAX_BACKUP_SIZE:
        raise ValueError('GDFS backup too large')
    flash = flash_path.read_bytes()
    backup = backup_path.read_bytes()
    records = parse_backup(backup)
    combined = combine_flash(flash, records)
    digest = lambda value: hashlib.sha256(value).hexdigest()
    manifest = {
        'model': 'Sony Ericsson W800i', 'layout': 'inferred GDFS v0 active banks',
        'flash_base': FLASH_BASE, 'flash_size': FLASH_SIZE,
        'gdfs_base': GDFS_BASE, 'gdfs_size': GDFS_SIZE,
        'block_size': BLOCK_SIZE, 'active_banks': BANK_COUNT, 'erased_spare_blocks': 1,
        'unit_count': len(records),
        'banks': [{'bank': bank,
                   'unit_count': sum(unit.bank == bank for unit in records),
                   'payload_bytes': sum(len(unit.data) for unit in records if unit.bank == bank)}
                  for bank in range(BANK_COUNT)],
        'inputs': [{'file': flash_path.name, 'sha256': digest(flash)},
                   {'file': backup_path.name, 'sha256': digest(backup)}],
        'flash_sha256': digest(combined),
        'gdfs_sha256': digest(combined[GDFS_BASE - FLASH_BASE:]),
        'preserved_main_fs_bytes': True,
        'original_boot_rom_supplied': False,
        'note': 'Initial storage metadata is reconstructed; backup payloads remain exact.',
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    pending = []
    try:
        for target, payload in [(output, combined),
                                (manifest_path, (json.dumps(manifest, indent=2) + '\n').encode())]:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=target.name + '.',
                                             suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                pending.append(temporary)
                stream.write(payload)
            temporary.replace(target)
            pending.remove(temporary)
    finally:
        for temporary in pending:
            temporary.unlink(missing_ok=True)
    return manifest


if __name__ == '__main__':
    import argparse
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--flash', type=Path, default=root / 'firmware/prepared/flash.bin')
    parser.add_argument('--backup', type=Path, default=root / 'firmware/W800_GDFS.bin')
    parser.add_argument('--output', type=Path, default=root / 'firmware/prepared/flash-gdfs.bin')
    args = parser.parse_args()
    print(json.dumps(prepare(args.flash, args.backup, args.output), indent=2))
