"""Observe SecLD storage and OTP prerequisites in an unchanged local boot.

Only breakpoints and reads are used. The report records sizes, status and
payload equality; it does not export provisioning bytes or replace results.
"""
import hashlib
import json
import struct

from w800.backend import QemuBackend, ROOT
from w800.firmware import parse_babe
from w800.gdfs import parse_backup
from w800.tools.debug import Debugger


def capture():
    units = {(unit.bank, unit.number): unit.data for unit in
             parse_backup((ROOT / 'firmware/W800_GDFS.bin').read_bytes())}
    flash = ROOT / 'firmware/prepared/flash-gdfs.bin'
    prepared = flash.read_bytes()
    main = parse_babe((ROOT / 'firmware/W800_R1L002_MAIN_EU_EMEA_RED49.bin').read_bytes())
    unchanged = all(prepared[s.address - 0x44000000:
                             s.address - 0x44000000 + len(s.data)] == s.data
                    for s in main.segments)
    points = {0x44d9ea60: 'static record length result',
              0x44d9eaae: 'static record read result',
              0x44d9ec98: 'OTP field count result',
              0x44d9ecc6: 'OTP first descriptor result',
              0x44d9ed34: 'OTP read completion',
              0x44d9ed72: 'OTP byte comparison',
              0x44d9ed86: 'OTP prerequisite completion',
              0x44d9ec38: 'static check completion'}
    report = {'main_segments_unchanged': unchanged,
              'prepared_sha256': hashlib.sha256(prepared).hexdigest(),
              'instruction_patches': False, 'debugger_register_setup': False,
              'events': [], 'otp_byte_comparison_reached': False}
    with QemuBackend(exploratory=True, debug=True, mmio_limit=1000000) as q:
        d = Debugger(q)
        d.sock.settimeout(5)
        try:
            for pc in points:
                d.breakpoint(pc, thumb=True)
            for _ in range(32):
                d.run()
                r = d.registers()
                pc = r[15]
                if pc not in points:
                    report['stop'] = f'guest stopped at {pc:#x}'
                    break
                event = {'checkpoint': points[pc], 'pc': hex(pc)}
                if pc in (0x44d9ea60, 0x44d9eaae):
                    event['status'] = r[0] & 255
                    length = struct.unpack('<I', d.read(r[13], 4))[0]
                    event['length'] = length
                    if pc == 0x44d9eaae and event['status'] == 0 and 0 < length <= 65536:
                        event['matches_backup_unit_0_19'] = d.read(r[4], length) == units[0, 19]
                elif pc == 0x44d9ec98:
                    event['status'] = r[0] & 255
                    event['field_count'] = struct.unpack('<I', d.read(r[13] + 4, 4))[0]
                elif pc == 0x44d9ecc6:
                    event['status'] = r[0] & 255
                    event['first_field_length'] = struct.unpack('<I', d.read(r[13] + 8, 4))[0]
                elif pc == 0x44d9ed34:
                    event['result'] = r[4]
                elif pc == 0x44d9ed72:
                    report['otp_byte_comparison_reached'] = True
                    event['comparison_length'] = r[2]
                elif pc == 0x44d9ed86:
                    event['result'] = r[5]
                elif pc == 0x44d9ec38:
                    event['result'] = r[6]
                    report['stop'] = 'original static check returned'
                report['events'].append(event)
                if pc == 0x44d9ec38:
                    break
                d.step_over_breakpoint(pc, thumb=True)
        finally:
            q.command('stop')
            report['diagnostics'] = q.diagnostics()
            d.close()
    return report


if __name__ == '__main__':
    result = capture()
    (ROOT / 'reports/secld-input-audit.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
