"""Original Walkman MP3 playback, physical controls and track resource handoff."""
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
class OriginalWalkmanTests(unittest.TestCase):
    def test_stop_mpeg2_pause_volume_next_and_background_back(self):
        commands = [
            dict(label='playing',keys=['down','select','down','down','select','select'],seconds=2),
            dict(label='pause',keys=['soft_left'],seconds=1),
            dict(label='paused-held',seconds=2),
            dict(label='resume',keys=['soft_left'],seconds=1),
            dict(label='volume-up',keys=['volume_up'],seconds=1),
            dict(label='volume-down',keys=['volume_down'],seconds=1),
            dict(label='next',keys=['right'],seconds=2),
            dict(label='back',keys=['back'],seconds=1),
            dict(finish=True),
        ]
        with tempfile.TemporaryDirectory() as directory:
            process = subprocess.run([sys.executable,'-m','w800.tools.probe_walkman',
                '--directory',directory],cwd=ROOT.parent,
                env=dict(os.environ,QT_QPA_PLATFORM='offscreen'),
                input=''.join(json.dumps(command)+'\n' for command in commands),
                capture_output=True,text=True,timeout=180)
            self.assertEqual(process.returncode,0,process.stdout[-4000:]+process.stderr[-4000:])
            report = json.loads((Path(directory)/'report.json').read_text())
            self.assertNotIn('error',report)
            self.assertTrue(report['source_unchanged'])
            self.assertFalse(report['guest_instruction_patches'])
            self.assertFalse(report['validation_result_changes'])
            first = next(row for row in report['audio_trace']
                         if row['event']=='Original stream write' and row['registers'][0]=='0x4')
            fixture = Path(__file__).with_name('fixtures')/'stop-guest-stream4.bin'
            self.assertEqual(bytes.fromhex(first['payload'])[12:],fixture.read_bytes()[12:512])
            actions = {row['label']:row for row in report['actions']}
            # Paused firmware neither feeds the stream nor submits more PCM.
            self.assertEqual(actions['pause']['capture_bytes'],actions['paused-held']['capture_bytes'])
            self.assertEqual(actions['pause']['event_counts']['Original stream write'],
                             actions['paused-held']['event_counts']['Original stream write'])
            self.assertGreater(actions['resume']['capture_bytes'],actions['paused-held']['capture_bytes'])
            log = report['diagnostics']
            self.assertIn('AAC_CONFIG codec=MP3 rate=22050 channels=2',log)
            self.assertIn('half_taps=32 history=32 ratio=2:1',log)
            paused = re.search(r'AAC_ACTIVITY operation=2 running=0 submitted=(\d+)',log)
            self.assertIsNotNone(paused)
            self.assertGreater(int(paused[1]),10000)
            self.assertIn(f'AAC_ACTIVITY operation=0 running=1 submitted={paused[1]}',log)
            self.assertIn('input=0 output=0 value=02d4',log)
            self.assertIn('input=0 output=0 value=0200',log)
            # Next releases Stop's actual decoder and starts Musical Medley.
            self.assertIn('AAC_RESOURCE operation=4 capacity=0',log)
            self.assertGreaterEqual(log.count('AAC_PCM source=guest-stream4'),2)
            controls = [row for row in report['audio_trace']
                        if row['event']=='Original compressed decoder control']
            self.assertTrue(any(row['phase']=='next' and row['registers'][0]=='0x4' for row in controls))
            # Native Back returns to Walkman while continuing background play.
            self.assertFalse(any(row['phase']=='back' for row in controls))
            self.assertGreater(actions['back']['event_counts']['Original stream write'],
                               actions['next']['event_counts']['Original stream write'])
            for error in ('UNSUPPORTED','CONFIG_ERROR','DECODE_ERROR'):
                self.assertNotIn(error,log)
            pcm = json.loads((Path(directory)/'pcm.json').read_text())
            self.assertEqual((pcm['rate'],pcm['channels']),(44100,2))
            self.assertGreater(pcm['frames'],30000)
            self.assertGreater(pcm['nonzero_bytes'],30000)
