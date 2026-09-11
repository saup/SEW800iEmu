"""Observe category label animation completion and its real DMA pixels."""
import hashlib
import json
import os
import struct

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from w800.backend import ROOT
from w800.guest_ui import OriginalUISession
from w800.tests.test_menu_services import press


def main():
    directory = ROOT / 'reports/picture-back-color/settle'
    directory.mkdir(parents=True, exist_ok=True)
    rows = []
    with OriginalUISession(start_screen='menu', default_theme=True) as session:
        def capture(label, elapsed):
            image = session.frame()
            image.save(str(directory / f'{label}-{elapsed:04d}.png'))
            region = image.copy(24, 20, 152, 175)
            pdi = session.d.read(0xf7000100, 256)
            entry = pdi[0xfc]
            size = (struct.unpack_from('<H', pdi, entry + 24)[0] *
                    struct.unpack_from('<H', pdi, entry + 28)[0])
            source = session.word(0xf20001a0) - size
            row = {'label': label, 'elapsed_ms': elapsed,
                   'labels_sha256': hashlib.sha256(region.constBits().tobytes()).hexdigest(),
                   'dma_source': hex(source), 'dma_bytes': size}
            if size == 77440 and pdi[0x7c:0x80] == bytes.fromhex('00000022'):
                data = session.d.read(source, size)
                (directory / f'{label}-{elapsed:04d}.bin').write_bytes(data)
                mismatches = []
                for y in range(20, 195):
                    for x in range(24, 176):
                        value = struct.unpack_from('>H', data, (y * 176 + x) * 2)[0]
                        expected = ((value >> 11) * 255 // 31,
                                    ((value >> 5) & 63) * 255 // 63,
                                    (value & 31) * 255 // 31)
                        color = image.pixelColor(x, y)
                        observed = color.red(), color.green(), color.blue()
                        if expected != observed:
                            mismatches.append([x, y, expected, observed])
                row['source_mismatches'] = len(mismatches)
                row['source_mismatch_examples'] = mismatches[:4]
            rows.append(row)
        press(session, 'down', 'left', 'select')
        for ms in range(0, 701, 100):
            if ms:
                session.advance(100)
            capture('entry', ms)
        press(session, 'select', 'down', 'back')
        for ms in range(0, 701, 100):
            if ms:
                session.advance(100)
            capture('return', ms)
        (directory / 'result.json').write_text(json.dumps({'rows': rows, 'session': session.report()}, indent=2) + '\n')
        print(json.dumps(rows))


if __name__ == '__main__':
    main()
