"""Actual C FIR implementation against independent zero-stuff/convolution."""
import ctypes
from pathlib import Path
import random
import shutil
import subprocess
import tempfile
import unittest

from w800.backend import ROOT


@unittest.skipUnless(shutil.which('cc'), 'C compiler required')
class ResamplerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        source = Path(cls.directory.name) / 'filter.c'
        library = Path(cls.directory.name) / 'filter.dylib'
        source.write_text('''#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include "w800-dsp-resample.inc"
void *make(const int16_t *coefficients, unsigned count, unsigned interpolation) {
    W800DspResampler *filter = calloc(1, sizeof(*filter));
    if (!w800_dsp_resampler_init(filter, coefficients, count, interpolation)) {
        free(filter);
        return NULL;
    }
    return filter;
}
unsigned process(void *filter, const int16_t *input, unsigned frames,
                 unsigned channels, int16_t *output) {
    return w800_dsp_resampler_process(filter, input, frames, channels, output);
}
void destroy(void *filter) { free(filter); }
''')
        subprocess.run(['cc', '-shared', '-fPIC', '-O2', '-Wall', '-Werror',
                        '-I', str(ROOT / 'qemu'), str(source), '-o', str(library)],
                       check=True, capture_output=True)
        cls.lib = ctypes.CDLL(str(library))
        samples = ctypes.POINTER(ctypes.c_int16)
        cls.lib.make.argtypes = [samples, ctypes.c_uint, ctypes.c_uint]
        cls.lib.make.restype = ctypes.c_void_p
        cls.lib.process.argtypes = [ctypes.c_void_p, samples, ctypes.c_uint,
                                    ctypes.c_uint, samples]
        cls.lib.process.restype = ctypes.c_uint
        cls.lib.destroy.argtypes = [ctypes.c_void_p]

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def render(self, coefficients, inputs, chunks, interpolation=3):
        handle = self.lib.make((ctypes.c_int16 * len(coefficients))(*coefficients),
                               len(coefficients), interpolation)
        self.assertTrue(handle)
        result = []
        offset = 0
        try:
            for frames in chunks:
                values = inputs[offset * 2:(offset + frames) * 2]
                output = (ctypes.c_int16 * (frames * interpolation * 2))()
                self.assertEqual(self.lib.process(handle,
                    (ctypes.c_int16 * len(values))(*values), frames, 2, output), frames * interpolation)
                result.extend(output)
                offset += frames
            self.assertEqual(offset * 2, len(inputs))
            return result
        finally:
            self.lib.destroy(handle)

    def test_impulse_and_stereo_history_across_packet_boundaries(self):
        randomizer = random.Random(800)
        coefficients = [randomizer.randrange(-5000, 5000) for _ in range(48)]
        inputs = [randomizer.randrange(-2000, 2000) for _ in range(130)]
        kernel = coefficients + coefficients[::-1]
        expected = []
        # Independent reference: insert two zero frames after every input,
        # then convolve the full96-tap kernel without a circular history.
        expanded = [[inputs[2 * (n // 3) + side] if n % 3 == 0 else 0
                     for n in range(65 * 3)] for side in range(2)]
        for n in range(65 * 3):
            for side in range(2):
                total = sum(kernel[tap] * expanded[side][n - tap]
                            for tap in range(min(n + 1, len(kernel))))
                expected.append(max(-32768, min(32767, (total + 16384) // 32768)))
        self.assertEqual(self.render(coefficients, inputs, [65]), expected)
        self.assertEqual(self.render(coefficients, inputs, [1, 7, 24, 1, 32]), expected)
        impulse = [16384, -16384] + [0] * (64 * 2)
        impulse_expected = [value for coefficient in kernel
                            for value in ((coefficient + 1) // 2, (-coefficient + 1) // 2)]
        impulse_expected += [0] * (65 * 6 - len(impulse_expected))
        self.assertEqual(self.render(coefficients, impulse, [32, 33]), impulse_expected)

    def test_saturation_preserves_opposite_stereo_polarities(self):
        output = self.render([32767] * 48, [32767, -32768] * 64, [17, 47])
        self.assertEqual(output[-6:], [32767, -32768] * 3)

    def test_twofold_filter_convolution_and_complete_history_tail(self):
        # Original Stop descriptor uses32 half-taps, interpolation2/history32.
        coefficients = [((i * 997) % 8000) - 4000 for i in range(32)]
        kernel = coefficients + coefficients[::-1]
        inputs = [16384, -16384] + [0] * (31 * 2)
        expected = [sample for coefficient in kernel
                    for sample in ((coefficient + 1) // 2, (-coefficient + 1) // 2)]
        self.assertEqual(self.render(coefficients, inputs, [1, 7, 24], 2), expected)
        # Resource dimensions must match the modeled state before acceptance.
        self.assertFalse(self.lib.make((ctypes.c_int16 * 32)(*coefficients),32,3))
