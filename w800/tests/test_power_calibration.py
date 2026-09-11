"""Check physical ADC inputs against supplied calibration and original HAL."""
from pathlib import Path
import struct
import unittest
from w800.backend import ROOT
from w800.gdfs import parse_backup
from w800.tests import test_power


@unittest.skipUnless((ROOT / 'build/qemu-system-arm').exists() and
                     (ROOT / 'firmware/W800_GDFS.bin').exists(),
                     'QEMU build and original calibration backup required')
class PowerCalibrationTests(unittest.TestCase):
    def test_original_voltage_and_isolated_temperature_hal(self):
        from w800.tools.trace_power_calibration import capture
        result = capture(isolated_temperature=True)
        self.assertEqual(result['millivolts'], 3899)
        self.assertEqual(result['battery_fitted'], 1)
        self.assertEqual(result['battery_type'], 1)
        self.assertEqual(result['thermistor_millivolts'], 401)
        self.assertEqual(result['temperature_celsius'], 25)
        self.assertTrue(result['debugger_register_setup'])
        self.assertFalse(result['instruction_patches'])

    def test_stationary_battery_adc_and_thermistor_calibration(self):
        units = {(u.bank, u.number): u.data for u in
                 parse_backup((ROOT / 'firmware/W800_GDFS.bin').read_bytes())}
        high_v, low_v, high_adc, low_adc = struct.unpack('<HHBB', units[0, 9])
        high_v *= 10; low_v *= 10
        slope = (high_v - low_v) / (high_adc - low_adc)
        expected_adc = round(low_adc + (3900 - low_v) / slope)
        self.assertEqual(expected_adc, 108)
        therm_table = list(struct.iter_unpack('<Hh', units[0, 1403]))
        therm_mv = next(mv for mv, temperature in therm_table if temperature == 25)
        aux_high, aux_low, adc_high, adc_low = struct.unpack('<HHBB', units[0, 12])
        # MAIN44ab55e6/55ec replaces the backup's ADC endpoints.
        adc_high, adc_low = 255, 1
        therm_adc = round(adc_low + (therm_mv - aux_low * 10) *
                          (adc_high - adc_low) / ((aux_high - aux_low) * 10))
        self.assertEqual(therm_adc, 38)
        fixture = test_power.PowerTests(methodName='runTest')
        fixture.setUp()
        try:
            for register in (0x75, 0x76, 0x77):
                self.assertEqual(fixture.get(register), expected_adc)
                fixture.put(register, 0)
                self.assertEqual(fixture.get(register), expected_adc)
            # Cold-start MAIN44a948de uses 6A before the ordinary AA mode.
            # Both paths must measure the same electrical battery input.
            self.assertEqual(fixture.get(0x71), 0)
            fixture.put(0x70, 0x6a)
            self.assertEqual(fixture.get(0x71), expected_adc)
            fixture.put(0x70, 0xaa)
            self.assertEqual(fixture.get(0x71), expected_adc)
            fixture.put(0x70, 0xab)
            self.assertEqual(fixture.get(0x71), therm_adc)
            fixture.put(0x71, 0xff)
            self.assertEqual(fixture.get(0x71), therm_adc)
            # Conversion of a different channel must not change VBAT outputs.
            self.assertEqual(fixture.get(0x77), expected_adc)
            # The cold-start command must replace a previously latched channel,
            # and disabling conversions must retain the result for later reads.
            fixture.put(0x70, 0x6a)
            self.assertEqual(fixture.get(0x71), expected_adc)
            fixture.put(0x70, 0)
            fixture.put(0x71, 0xff)
            self.assertEqual(fixture.get(0x71), expected_adc)
        finally:
            fixture.tearDown()
