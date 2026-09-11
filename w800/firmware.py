"""Parse addressed BABE v3/v4 images without executing their service loader.

Layout reference: seftool src/emp/v3/babe.h and flash.c (see reference/SOURCES.md).
SHA-256 identifies inputs; RSA signatures are not authenticated by this parser.
"""
from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import struct

FLASH_BASE = 0x44000000
FLASH_SIZE = 0x02000000
MAX_INPUT = 64 * 1024 * 1024

@dataclass(frozen=True)
class Segment:
    address: int
    data: bytes
    offset: int

@dataclass(frozen=True)
class BabeImage:
    version: int
    platform: int
    cid: int
    sha256: str
    segments: tuple[Segment, ...]


def parse_babe(data: bytes) -> BabeImage:
    if not 0x380 <= len(data) <= MAX_INPUT:
        raise ValueError('BABE image size outside supported bounds')
    if data[:2] != b'\xba\xbe' or data[3] not in (3, 4):
        raise ValueError('Expected a BABE v3/v4 image')
    version = data[3]
    platform, = struct.unpack_from('<I', data, 8)
    cid, = struct.unpack_from('<I', data, 0x10)
    if platform != 0x00100000:
        raise ValueError(f'Expected DB2010 platform, found {platform:#x}')
    count, = struct.unpack_from('<I', data, 0x2e8)
    if not 1 <= count <= 4096:
        raise ValueError('Invalid BABE block count')
    pos = 0x380 + count * (20 if version == 4 else 1)
    segments = []
    for index in range(count):
        if pos + 8 > len(data):
            raise ValueError(f'Truncated block header {index}')
        address, size = struct.unpack_from('<II', data, pos)
        pos += 8
        if not 0 < size <= 0x10000 or pos + size > len(data):
            raise ValueError(f'Invalid or truncated block {index}')
        if address < FLASH_BASE or address + size > FLASH_BASE + FLASH_SIZE:
            raise ValueError(f'Block {index} outside W800 flash')
        segments.append(Segment(address, data[pos:pos + size], pos))
        pos += size
    if pos != len(data):
        raise ValueError('Unexpected trailing BABE data')
    ordered = sorted(segments, key=lambda s: s.address)
    for left, right in zip(ordered, ordered[1:]):
        if left.address + len(left.data) > right.address:
            raise ValueError('Overlapping BABE blocks')
    return BabeImage(version, platform, cid, hashlib.sha256(data).hexdigest(), tuple(segments))


def prepare(paths: list[Path], output: Path) -> dict:
    images = []
    for path in paths:
        if path.stat().st_size > MAX_INPUT:
            raise ValueError('Input too large')
        images.append((path, parse_babe(path.read_bytes())))
    segments = sorted((s for _, i in images for s in i.segments), key=lambda s:s.address)
    for left, right in zip(segments, segments[1:]):
        if left.address + len(left.data) > right.address:
            raise ValueError('Overlapping input images')
    flash = bytearray(b'\xff') * FLASH_SIZE
    for segment in segments:
        offset = segment.address - FLASH_BASE
        flash[offset:offset + len(segment.data)] = segment.data
    manifest = {
        'model': 'Sony Ericsson W800i', 'firmware': 'R1L002',
        'flash_base': FLASH_BASE, 'flash_size': FLASH_SIZE,
        'entry': 0x44020000, 'boot_mode': 'direct MAIN vector (EROM absent)',
        'signature_verified': False,
        'flash_sha256': hashlib.sha256(flash).hexdigest(),
        'inputs': [{'file': p.name, 'sha256': i.sha256, 'version': i.version,
                    'cid': i.cid, 'segments': [{'address': s.address, 'size': len(s.data),
                    'file_offset': s.offset} for s in i.segments]} for p,i in images],
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / 'flash.bin').write_bytes(flash)
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('images', nargs='+', type=Path)
    parser.add_argument('--output', type=Path, default=Path('w800/firmware/prepared'))
    args = parser.parse_args()
    result = prepare(args.images, args.output)
    print(json.dumps({k:v for k,v in result.items() if k != 'inputs'}, indent=2))
