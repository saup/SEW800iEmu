"""Native Timer respects Silent mode through the real early AudioControl provider."""
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
                     'Local macOS QEMU synthesizer required')
class OriginalSoundSettingsTests(unittest.TestCase):
    def test_timer_muted_then_audible_after_native_silent_setting_and_dismissal(self):
        enter_timer = ['down'] * 6 + ['select','0','0','0','0','0','5','select']
        commands = [
            dict(label='muted-timer',keys=['select','down','down','select']+enter_timer,seconds=8),
            dict(label='muted-dismiss',keys=['select'],seconds=1),
            dict(label='ring-volume',keys=['select','right','down','down','select','right','select'],seconds=0),
            dict(label='silent-off',keys=['soft_left'],seconds=1),
            # The original save acknowledgement is dismissed before leaving
            # Settings; no property bytes or volume state are written by us.
            dict(label='volume-saved',keys=['soft_left','back','back'],seconds=0),
            dict(label='audible-timer',keys=['left','select']+enter_timer,seconds=8),
            dict(label='audible-dismiss',keys=['select'],seconds=1),
            dict(finish=True),
        ]
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable,'-m','w800.tools.probe_walkman','--directory',directory],
                cwd=ROOT.parent,env=dict(os.environ,QT_QPA_PLATFORM='offscreen'),
                input=''.join(json.dumps(row)+'\n' for row in commands),
                capture_output=True,text=True,timeout=180)
            self.assertEqual(result.returncode,0,result.stdout[-6000:]+result.stderr[-4000:])
            report = json.loads((Path(directory)/'report.json').read_text())
            self.assertTrue(report['source_unchanged'])
            self.assertFalse(report['guest_instruction_patches'])
            self.assertFalse(report['validation_result_changes'])
            self.assertFalse(report['early_audio_control'])  # exercise production ordering
            init = [r for r in report['audio_trace']
                    if r['event']=='Original short sound interface initialization result']
            self.assertEqual(len(init),1)
            self.assertEqual(init[0]['registers'][0],'0x0')
            self.assertNotEqual(init[0]['short_sound_interface'],'0x0')
            requests = [r for r in report['audio_trace'] if r['event']=='Original short sound request']
            self.assertEqual([r['registers'][0] for r in requests],['0x1e','0x1e'])
            self.assertEqual([r['phase'] for r in requests],['muted-timer','audible-timer'])
            dispatch = [r for r in report['audio_trace'] if r['event']=='Original short sound dispatch']
            self.assertEqual(len(dispatch),2)
            log = report['diagnostics']
            ends = re.findall(r'PCM_END rendered=(\d+) submitted=(\d+) midi_events=(\d+) nonzero_samples=(\d+)',log)
            self.assertEqual(len(ends),2,log)
            self.assertTrue(all(int(row[0])>20000 and row[0]==row[1] and int(row[2])>100 for row in ends))
            self.assertEqual(int(ends[0][3]),0)
            self.assertGreater(int(ends[1][3]),20000)
            self.assertIn('input=0 output=0 value=0000',log)
            self.assertIn('input=0 output=0 value=0800',log)
            # Both physical dismissals follow native stream stop/release.
            releases = [r for r in report['audio_trace'] if r['event']=='Original synthesizer release call']
            self.assertEqual([r['phase'] for r in releases],['muted-dismiss','audible-dismiss'])
            pcm = json.loads((Path(directory)/'pcm.json').read_text())
            self.assertGreater(pcm['nonzero_bytes'],20000)
            self.assertEqual((pcm['rate'],pcm['channels']),(44100,2))
            self.assertNotIn('DECODE_ERROR',log)
            self.assertNotIn('User called assert','\n'.join(report['messages']))
