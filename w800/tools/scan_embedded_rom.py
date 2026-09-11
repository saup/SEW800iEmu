"""Bounded local search for recognizable compressed streams and ROM clues.

This does not exclude raw deflate, proprietary compression, encryption, or
filesystem-fragmented files. Nothing is executed or installed as a ROM.
"""
from collections import Counter, deque
from email import policy
from email.parser import BytesParser
import bz2
import hashlib
import json
import lzma
from pathlib import Path
import re
import struct
import zipfile
import zlib

from w800.backend import ROOT

LIMIT = 4 * 1024 * 1024
TOTAL_LIMIT = 64 * 1024 * 1024
SHA1_IV = struct.pack('<5I', 0x67452301, 0xefcdab89, 0x98badcfe, 0x10325476, 0xc3d2e1f0)
MAGIC = re.compile(rb'\x78[\x01\x5e\x9c\xda]|\x1f\x8b\x08|BZh[1-9]|\xfd7zXZ\x00|\x5d\x00\x00[\x01-\x80]\x00')


def clues(data):
    # String/constants are clues only: ordinary applications contain SHA-1 too.
    names = []
    for m in re.finditer(rb'[ -~]{5,160}', data):
        if re.search(rb'(?i)boot.?rom|mask.?rom|internal.?rom|db201[02]|marita|\bKGEN\b', m[0]):
            names.append({'offset': hex(m.start()), 'text': m[0].decode('ascii')})
    return {'rom_related_strings': names,
            'sha1_iv_offsets': [hex(m.start()) for m in re.finditer(re.escape(SHA1_IV), data)]}


def decode(data, offset):
    head = data[offset:offset + 13]
    if head[:2] == b'\x1f\x8b':
        kind, d = 'gzip', zlib.decompressobj(31)
    elif head.startswith(b'BZh'):
        kind, d = 'bzip2', bz2.BZ2Decompressor()
    elif head.startswith(b'\xfd7zXZ\x00'):
        kind, d = 'xz', lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=64 * 1024 * 1024)
    elif head[0] == 0x5d:
        if len(head) < 13:
            return None
        size = int.from_bytes(head[5:13], 'little')
        if size != 0xffffffffffffffff and not 1 <= size <= LIMIT:
            return None
        kind, d = 'lzma-alone', lzma.LZMADecompressor(format=lzma.FORMAT_ALONE, memlimit=64 * 1024 * 1024)
    else:
        kind, d = 'zlib', zlib.decompressobj()
    chunk = data[offset:offset + LIMIT]
    output = d.decompress(chunk, LIMIT + 1)
    if not d.eof or len(output) > LIMIT or not output:
        return None
    return kind, len(chunk) - len(d.unused_data), output


def main():
    queue = deque()
    # The prepared image strips inter-block BABE headers and preserves payload
    # addresses, allowing stream recognition across contiguous flash segments.
    for name in ('prepared/flash.bin', 'W800_GDFS.bin'):
        path = ROOT / 'firmware' / name
        queue.append((name, path.read_bytes(), 0))
    archive = ROOT / 'firmware/W800i_CDA102425_38_EMEA_1.zip'
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            if not info.is_dir():
                queue.append(('customization/' + info.filename, z.read(info), 0))
    report = {'limits': {'output_per_stream': LIMIT, 'decoded_total': TOTAL_LIMIT, 'recursion_depth': 2},
              'coverage': 'signature-bearing compression plus decoded ZIP and MIME members; not exhaustive',
              'inputs': [], 'streams': [], 'duplicate_buffers_skipped': 0}
    seen, total = set(), 0
    output_dir = ROOT / 'reports/embedded-streams'
    output_dir.mkdir(exist_ok=True)
    while queue:
        name, data, depth = queue.popleft()
        digest = hashlib.sha256(data).hexdigest()
        if digest in seen:
            report['duplicate_buffers_skipped'] += 1
            continue
        seen.add(digest)
        info = {'source': name, 'size': len(data), 'sha256': digest, 'depth': depth, **clues(data)}
        report['inputs'].append(info)
        if name.lower().endswith(('.mht', '.mhtml')) and depth < 2:
            msg = BytesParser(policy=policy.default).parsebytes(data)
            for i, part in enumerate(msg.walk()):
                payload = part.get_payload(decode=True)
                if payload and len(payload) <= LIMIT:
                    total += len(payload)
                    if total <= TOTAL_LIMIT:
                        queue.append((name + f'/mime-{i}', payload, depth + 1))
        if depth >= 2:
            continue
        counts = Counter()
        for match in MAGIC.finditer(data):
            counts['signature_candidates'] += 1
            try:
                decoded = decode(data, match.start())
            except (zlib.error, lzma.LZMAError, OSError, EOFError, ValueError):
                counts['rejected'] += 1
                continue
            if decoded is None:
                counts['incomplete_or_limit'] += 1
                continue
            kind, used, output = decoded
            counts['valid'] += 1
            total += len(output)
            child_hash = hashlib.sha256(output).hexdigest()
            rec = {'source': name, 'offset': hex(match.start()), 'kind': kind,
                   'consumed_bytes': used, 'size': len(output), 'sha256': child_hash, **clues(output)}
            if total > TOTAL_LIMIT:
                rec['skipped'] = 'total decoded-byte budget'
            else:
                # Store only outputs with an actual ROM-related clue. This
                # retains candidates without filling the project with image data.
                if rec['rom_related_strings'] or rec['sha1_iv_offsets']:
                    saved = output_dir / (child_hash + '.bin')
                    saved.write_bytes(output)
                    rec['saved'] = str(saved.relative_to(ROOT))
                queue.append((name + f'/{kind}@{match.start():x}', output, depth + 1))
            report['streams'].append(rec)
        info['compression_scan'] = dict(counts)
    report['decoded_total'] = total
    report['verified_mask_rom_images'] = []
    target = ROOT / 'reports/embedded-rom-scan.json'
    target.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'report': str(target), 'unique_inputs': len(report['inputs']),
                      'valid_streams': len(report['streams']), 'decoded_bytes': total,
                      'streams_with_clues': [r for r in report['streams'] if r.get('saved')]}))


if __name__ == '__main__':
    main()
