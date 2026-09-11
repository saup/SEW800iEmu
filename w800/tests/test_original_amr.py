"""Original Rock Star AMR records, real PCM, pause/resume and Back/replay."""
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
class OriginalAMRTests(unittest.TestCase):
    def test_rock_star_actual_amr_pcm_pause_stop_and_replay(self):
        commands = [
            dict(label='playing', keys=['down','select','down','down','down','down','select','select'], seconds=3),
            dict(label='pause', keys=['soft_left'], seconds=1),
            dict(label='held', seconds=2),
            dict(label='resume', keys=['soft_left'], seconds=2),
            dict(label='stop', keys=['back'], seconds=1),
            dict(label='replay', keys=['select'], seconds=2),
            dict(label='stop-replay', keys=['back'], seconds=1),
            dict(finish=True),
        ]
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-m', 'w800.tools.probe_walkman',
                '--directory', directory], cwd=ROOT.parent,
                env=dict(os.environ, QT_QPA_PLATFORM='offscreen'),
                input=''.join(json.dumps(row)+'\n' for row in commands),
                capture_output=True, text=True, timeout=180)
            self.assertEqual(result.returncode, 0, result.stdout[-4000:]+result.stderr[-4000:])
            report = json.loads((Path(directory)/'report.json').read_text())
            self.assertTrue(report['source_unchanged'])
            self.assertFalse(report['guest_instruction_patches'])
            self.assertFalse(report['validation_result_changes'])
            first = next(row for row in report['audio_trace']
                         if row['event']=='Original stream write' and row['registers'][0]=='0x4')
            fixture = Path(__file__).with_name('fixtures')/'rock-star-guest-stream4.bin'
            self.assertEqual(bytes.fromhex(first['payload'])[12:], fixture.read_bytes()[12:46])
            actions = {row['label']: row for row in report['actions']}
            self.assertEqual(actions['pause']['capture_bytes'], actions['held']['capture_bytes'])
            self.assertEqual(actions['pause']['event_counts']['Original stream write'],
                             actions['held']['event_counts']['Original stream write'])
            self.assertGreater(actions['resume']['capture_bytes'], actions['held']['capture_bytes'])
            controls = [row for row in report['audio_trace']
                        if row['event']=='Original compressed decoder control']
            for phase in ('stop', 'stop-replay'):
                values = {row['registers'][0] for row in controls if row['phase']==phase}
                self.assertTrue({'0x1','0x4'} <= values, values)
            log = report['diagnostics']
            self.assertEqual(log.count('AAC_CONFIG codec=AMR-NB rate=8000 channels=1'), 2)
            self.assertEqual(log.count('AMR_FILTER slot=0 half_taps=32 history=32 ratio=2:1'), 2)
            self.assertEqual(log.count('AMR_FILTER slot=1 half_taps=48 history=32 ratio=3:1'), 2)
            self.assertEqual(log.count('AAC_PCM source=guest-stream4 rate=48000'), 2)
            self.assertEqual(log.count('AAC_RESOURCE operation=4 capacity=0'), 2)
            ended = re.findall(r'AAC_END packets=(\d+) decoded=(\d+) submitted=(\d+) nonzero=(\d+)', log)
            self.assertEqual(len(ended), 2)
            self.assertTrue(all(all(int(value)>0 for value in row) for row in ended))
            for error in ('UNSUPPORTED', 'CONFIG_ERROR', 'DECODE_ERROR'):
                self.assertNotIn(error, log)
            self.assertNotIn('Version responder TIMED OUT', '\n'.join(report['messages']))
            pcm = json.loads((Path(directory)/'pcm.json').read_text())
            self.assertEqual((pcm['rate'], pcm['channels']), (44100, 2))
            self.assertGreater(pcm['frames'], 30000)
            self.assertGreater(pcm['nonzero_bytes'], 30000)
