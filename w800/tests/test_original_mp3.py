"""Original Latin picker, guest DSP stream, PCM and physical Back teardown."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from w800.backend import ROOT


@unittest.skipUnless(sys.platform == 'darwin' and (ROOT / 'build/qemu-system-arm').exists(),
                     'Local macOS QEMU decoder required')
class OriginalMP3Tests(unittest.TestCase):
    def test_latin_native_pcm_and_back_release_without_firmware_changes(self):
        # Isolate the read-only breakpoint observer from other GUI tests.
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'latin.json'
            capture = Path(directory) / 'latin.wav'
            environment = dict(os.environ, QT_QPA_PLATFORM='offscreen')
            result = subprocess.run([
                sys.executable, '-m', 'w800.tools.trace_audio_hardware',
                '--media', 'latin', '--seconds', '5', '--back-count', '1',
                '--wav', str(capture), '--output', str(output),
            ], cwd=ROOT.parent, env=environment, capture_output=True, text=True, timeout=180)
            self.assertEqual(result.returncode, 0, result.stdout[-5000:] + result.stderr[-5000:])
            report = json.loads(output.read_text())
            self.assertNotIn('error', report)
            self.assertTrue(report['source_unchanged'])
            self.assertFalse(report['guest_instruction_patches'])
            self.assertFalse(report['validation_result_changes'])
            self.assertEqual(report['target_media'], 'Latin.mp3')
            # Identify the selected asset from its actual compressed guest
            # bytes, not only the probe's requested filename/route label.
            first_record = next(row for row in report['samples']
                                if row['event'] == 'Original stream write'
                                and row['registers'][0] == '0x4')
            fixture = Path(__file__).with_name('fixtures') / 'latin-guest-stream4.bin'
            self.assertEqual(bytes.fromhex(first_record['payload'])[12:], fixture.read_bytes()[12:512])
            pcm = report['pcm_capture']
            self.assertEqual((pcm['rate'], pcm['channels'], pcm['sample_width']), (44100, 2, 2))
            self.assertGreater(pcm['frames'], 100000)
            self.assertGreater(pcm['nonzero_samples'], 100000)
            diagnostics = report['hardware_diagnostics']
            self.assertIn('AAC_CONFIG codec=MP3 rate=44100 channels=2', diagnostics)
            self.assertIn('AAC_PCM source=guest-stream4 rate=44100', diagnostics)
            self.assertIn('AAC_RESOURCE operation=4 capacity=0', diagnostics)
            self.assertRegex(diagnostics, r'AAC_FLUSH returned=[1-9][0-9]*')
            self.assertNotIn('UNSUPPORTED', diagnostics)
            self.assertNotIn('DECODE_ERROR', diagnostics)
            self.assertNotIn('CONFIG_ERROR', diagnostics)
            self.assertNotIn('Version responder TIMED OUT', '\n'.join(report['messages']))
            self.assertIsNotNone(re.search(r'AAC_END packets=[1-9][0-9]* decoded=[1-9][0-9]* '
                                          r'submitted=[1-9][0-9]* nonzero=[1-9][0-9]*', diagnostics))
            controls = [row for row in report['samples']
                        if row['event'] == 'Original compressed decoder control']
            self.assertTrue(any(row['phase'] == 'back-up' and row['registers'][0] == '0x1'
                                for row in controls))
            self.assertTrue(any(row['phase'] == 'back-up' and row['registers'][0] == '0x4'
                                for row in controls))
            keys = [row for row in report['samples'] if row['event'] == 'Physical key signal'
                    and row['registers'][1] == '0x7']
            self.assertEqual([(row['phase'], row['registers'][0]) for row in keys],
                             [('back-down', '0x0'), ('back-up', '0x1')])
