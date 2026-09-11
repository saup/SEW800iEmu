"""Observe the original battery HAL's calibrated voltage and temperature.

Natural mode uses debugger breakpoints/read operations only. The explicit
isolated-temperature mode sets CPU arguments for an original interpolation
function, then discards the VM without resuming startup. ROM is never patched.
"""
import argparse
import json
import struct
from w800.backend import QemuBackend, ROOT
from w800.tools.debug import Debugger


def capture(isolated_temperature=False):
    with QemuBackend(exploratory=True, debug=True, mmio_limit=1000000,
                     flash_path=ROOT / 'firmware/prepared/flash-gdfs.bin') as q:
        d = Debugger(q)
        # The periodic temperature request starts well after battery init.
        d.sock.settimeout(45)
        try:
            resistance_pc = 0x44ab56e2
            d.breakpoint(resistance_pc, thumb=True)
            d.run()
            regs = d.registers()
            if regs[15] != resistance_pc:
                raise RuntimeError('Boot stopped before battery identification')
            resistance = regs[1]
            if not 8200 <= resistance < 84001:
                raise RuntimeError(f'Unexpected battery identification {resistance} ohms')
            d.breakpoint(resistance_pc, enabled=False, thumb=True)
            voltage = 0x44ab5050  # Original HAL stores converted millivolts.
            d.breakpoint(voltage, thumb=True)
            d.run()
            regs = d.registers()
            if regs[15] != voltage:
                raise RuntimeError('Boot stopped before battery voltage conversion: ' + q.diagnostics())
            millivolts = regs[0]
            if not 3895 <= millivolts <= 3905:
                raise RuntimeError(f'Unexpected original HAL voltage {millivolts} mV')
            calibration = d.read(0x4c089ae8 + 0x7a, 6)
            offset = struct.unpack_from('<h', calibration)[0]
            slope = struct.unpack_from('<f', calibration, 2)[0]
            auxiliary = d.read(0x4c04a3a8 + 0x44, 8)
            auxiliary_offset = struct.unpack_from('<h', auxiliary)[0]
            auxiliary_slope = struct.unpack_from('<f', auxiliary, 4)[0]
            battery_fitted = d.read(0x4c04a3a9, 1)[0]
            battery_type = d.read(0x4c089ae8, 1)[0]
            result = {'resistance_pc': hex(resistance_pc), 'battery_resistance_ohms': resistance,
                      'battery_fitted': battery_fitted, 'battery_type': battery_type,
                      'voltage_pc': hex(voltage), 'millivolts': millivolts,
                      'voltage_calibration_slope': slope, 'voltage_calibration_offset': offset,
                      'auxiliary_calibration_slope': auxiliary_slope,
                      'auxiliary_calibration_offset': auxiliary_offset,
                      'instruction_patches': False, 'debugger_register_setup': False,
                      'temperature_verification': 'pending'}
            d.breakpoint(voltage, enabled=False, thumb=True)
            temperature_input = 0x44ab5a80
            if isolated_temperature:
                # An isolated function test after natural calibration loaded.
                # This VM is discarded at the store and never resumes boot.
                f32 = lambda value: struct.unpack('<f', struct.pack('<f', value))[0]
                thermistor_mv = int(f32(f32(38 * auxiliary_slope) + auxiliary_offset) + 0.5)
                changes = {0: thermistor_mv, 1: regs[13] - 64,
                           13: regs[13] - 256, 15: temperature_input}
                register_packet = bytearray.fromhex(d.packet('g'))
                for index, value in changes.items():
                    struct.pack_into('<I', register_packet, index * 4, value)
                if d.packet('G' + register_packet.hex()) != 'OK':
                    raise RuntimeError('Unable to establish isolated function arguments')
                result['debugger_register_setup'] = True
                result['temperature_verification'] = 'isolated original function; not natural startup'
            else:
                d.breakpoint(temperature_input, thumb=True)
                d.run()
                regs = d.registers()
                if regs[15] != temperature_input:
                    (ROOT / 'reports/power-calibration-natural-pending.json').write_text(json.dumps(result, indent=2) + '\n')
                    (ROOT / 'reports/power-calibration-stop-mmio.txt').write_text(q.diagnostics())
                    raise RuntimeError(f'Boot stopped at {regs[15]:08x} before temperature measurement: ' + q.diagnostics()[-1500:])
                thermistor_mv = regs[0]
                d.breakpoint(temperature_input, enabled=False, thumb=True)
                result['temperature_verification'] = 'natural original guest execution'
            temperature = 0x44ab5b2e  # Original interpolated temperature store.
            d.breakpoint(temperature, thumb=True)
            d.run()
            regs = d.registers()
            if regs[15] != temperature:
                raise RuntimeError('Boot stopped before temperature interpolation: ' + q.diagnostics())
            celsius = struct.unpack('<i', struct.pack('<I', regs[0]))[0]
            if celsius != 25:
                raise RuntimeError(f'Unexpected original HAL temperature {celsius} C')
            result.update({'thermistor_millivolts': thermistor_mv,
                           'temperature_pc': hex(temperature), 'temperature_celsius': celsius})
            return result
        finally:
            d.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--isolated-temperature', action='store_true',
                        help='Explicit isolated original interpolation call after natural calibration; does not resume boot')
    args = parser.parse_args()
    result = capture(isolated_temperature=args.isolated_temperature)
    (ROOT / 'reports/power-calibration-result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
