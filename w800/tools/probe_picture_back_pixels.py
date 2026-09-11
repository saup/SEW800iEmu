"""Compare original picture-list Back redraw pixels with their DMA source."""
import json
import os
import struct

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from w800.backend import ROOT
from w800.guest_ui import OriginalUISession
from w800.tests.test_menu_services import press


def main():
    directory = ROOT / 'reports/picture-back-color/native-pixels'
    directory.mkdir(parents=True, exist_ok=True)
    transfers = []
    phase = None
    pixels = {}
    with OriginalUISession(start_screen='menu', default_theme=True) as session:
        log = directory / 'hardware.log'
        session.q.command('human-monitor-command', {'command-line': f'logfile {log}'})
        session.q.command('human-monitor-command', {'command-line': 'log unimp,guest_errors'})
        original_registers = session.d.registers
        def registers():
            values = original_registers()
            if values[15] == 0x44ab88b4 and phase is not None:
                pdi = session.d.read(0xf7000100, 256)
                entry = pdi[0xfc]
                outer = struct.unpack_from('<H', pdi, entry + 24)[0]
                inner = struct.unpack_from('<H', pdi, entry + 28)[0]
                size = outer * inner
                end = session.word(0xf20001a0)
                data = session.d.read(end - size, size)
                name = f'{len(transfers):02d}-{phase}.bin'
                (directory / name).write_bytes(data)
                row = {'phase': phase, 'source': hex(end - size), 'bytes': size,
                       'pdi': pdi.hex(), 'file': name}
                transfers.append(row)
                # Evidenced R61500_B full-width RGB565 redraw family.
                prefix = pdi[0x76:0x80]
                if (prefix[:6] != bytes.fromhex('000000000021') or
                        prefix[7:] != bytes.fromhex('000022') or size % 352):
                    raise RuntimeError('Unexpected partial/color DMA form: ' + json.dumps(row))
                y0 = prefix[6]
                row['start_y'] = y0
                for index, (value,) in enumerate(struct.iter_unpack('>H', data)):
                    x, y = index % 176, y0 + index // 176
                    pixels[(x, y)] = ((value >> 11) * 255 // 31,
                                      ((value >> 5) & 63) * 255 // 63,
                                      (value & 31) * 255 // 31)
            return values
        session.d.registers = registers
        press(session, 'down', 'left')
        phase = 'enter-categories'
        press(session, 'select')
        before = session.frame()
        before.save(str(directory / 'categories.png'))
        native_before = pixels.copy()
        phase = None
        press(session, 'select', 'down')
        phase = 'return-categories'
        pixels.clear()
        press(session, 'back')
        after = session.frame()
        after.save(str(directory / 'categories-after.png'))
        differences = []
        for y in range(20, 195):
            for x in range(176):
                a, b = before.pixelColor(x, y), after.pixelColor(x, y)
                observed_a = (a.red(), a.green(), a.blue())
                observed_b = (b.red(), b.green(), b.blue())
                if observed_a != observed_b:
                    differences.append({'x': x, 'y': y, 'before': observed_a,
                                        'after': observed_b,
                                        'guest_before': native_before.get((x, y)),
                                        'guest_after': pixels.get((x, y))})
        report = {'difference_count': len(differences), 'differences': differences,
                  'all_differences_match_guest_pixels': all(
                      d['before'] == d['guest_before'] and d['after'] == d['guest_after']
                      for d in differences), 'transfers': transfers}
        settled = []
        for seconds in range(1, 4):
            phase = f'returned-idle-{seconds}'
            session.advance(1000)
            frame = session.frame()
            frame.save(str(directory / f'{phase}.png'))
            changed = []
            for y in range(170, 184):
                for x in range(5, 21):
                    a, b = before.pixelColor(x, y), frame.pixelColor(x, y)
                    rgb = (b.red(), b.green(), b.blue())
                    if a != b:
                        changed.append({'x': x, 'y': y, 'after': rgb,
                                        'guest_after': pixels.get((x, y))})
            settled.append({'seconds': seconds, 'changed': changed})
        report['settled'] = settled
        report['session'] = session.report()
        (directory / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps({'difference_count': len(differences),
                          'all_differences_match_guest_pixels': report['all_differences_match_guest_pixels'],
                          'idle_icon_difference_counts': [len(row['changed']) for row in settled]}))


if __name__ == '__main__':
    main()
