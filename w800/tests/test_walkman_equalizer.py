"""Original Walkman presets must filter PCM and keep playing through changes."""
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import unittest

from w800.backend import ROOT


@unittest.skipUnless(sys.platform=='darwin' and (ROOT/'build/qemu-system-arm').exists(),
                     'Local macOS QEMU decoder required')
class WalkmanEqualizerTests(unittest.TestCase):
    def test_original_mega_bass_voice_and_return_to_normal(self):
        directory=ROOT/'reports/walkman-equalizer-native'
        menu=['soft_right','down','down','down','select']
        actions=[
            dict(label='playing',keys=['down','select','down','down','select','select'],seconds=1),
            dict(label='equalizer',keys=menu),
            dict(label='mega-bass',keys=['down','down','select'],seconds=2),
            dict(label='mega-bass-playing',seconds=3),
            dict(label='equalizer-again',keys=menu),
            dict(label='voice',keys=['down','select'],seconds=2),
            dict(label='voice-playing',seconds=3),
            # Use the original next-track handoff before the short Stop
            # asset reaches its independent compressed-stream EOF boundary.
            dict(label='next-track',keys=['right'],seconds=1),
            dict(label='equalizer-final',keys=menu),
            dict(label='normal',keys=['up','up','up','select'],seconds=2),
            dict(label='normal-playing',seconds=3),
            dict(label='pause',keys=['soft_left'],seconds=1),
            dict(label='paused',seconds=1),
            dict(label='resume',keys=['soft_left'],seconds=1),
            dict(label='back',keys=['back'],seconds=1),
            dict(finish=True),
        ]
        process=subprocess.run([sys.executable,'-m','w800.tools.probe_walkman_equalizer',
            '--directory',str(directory)],cwd=ROOT.parent,
            env=dict(os.environ,QT_QPA_PLATFORM='offscreen'),
            input=''.join(json.dumps(action)+'\n' for action in actions),
            capture_output=True,text=True,timeout=180)
        self.assertEqual(process.returncode,0,process.stdout[-3000:]+process.stderr[-3000:])
        report=json.loads((directory/'result.json').read_text())
        session=report['session']
        self.assertTrue(session['source_unchanged'])
        self.assertFalse(session['guest_instruction_patches'])
        self.assertFalse(session['validation_result_changes'])
        phases={row['phase']:row for row in report['actions']}
        fixture=json.loads((Path(__file__).with_name('fixtures')/'walkman-equalizer-presets.json').read_text())
        for name in ('mega-bass','voice','normal'):
            with self.subTest(preset=name):
                bands=bytes.fromhex(phases[name]['native_bands'])
                settings=[struct.unpack_from('<b',bands,index*6+2)[0] for index in range(5)]
                self.assertEqual(settings,fixture['presets'][name]['bands_db'])
                stages={packet['words'][4]:packet['words'] for packet in report['packets']
                        if packet['phase']==name and packet['words'][2]}
                self.assertEqual([stages[index] for index in range(9)],fixture['presets'][name]['stages'])
                self.assertGreater(phases[name+'-playing']['capture_bytes'],phases[name]['capture_bytes']+100000)
        log=report['diagnostics']
        self.assertIn('W800_DSP_EQUALIZER_PCM target=0 input=0 side=0 stages=9',log)
        self.assertNotIn('SERVICE_PENDING channel=12 opcode=090e',log)
        self.assertNotIn('UNSUPPORTED',log)
        self.assertNotIn('DECODE_ERROR',log)
        self.assertIn('AAC_RESOURCE operation=4 capacity=0',log)
        self.assertGreaterEqual(log.count('AAC_PCM source=guest-stream4'),2)
        self.assertEqual(phases['paused']['capture_bytes'],phases['pause']['capture_bytes'])
        self.assertGreater(phases['resume']['capture_bytes'],phases['paused']['capture_bytes'])
        self.assertGreater(phases['back']['capture_bytes'],phases['resume']['capture_bytes'])
        self.assertTrue(all(not row['pressed_keys'] for row in phases.values()))
        pcm=json.loads((directory/'pcm.json').read_text())
        self.assertEqual((pcm['rate'],pcm['channels']),(44100,2))
        self.assertGreater(pcm['nonzero_bytes'],100000)
        summary=dict(result='PASS',qemu_sha256=session['qemu_sha256'],source_unchanged=True,
                     presets=['mega-bass','voice','normal'],actions=report['actions'],pcm=pcm,
                     runtime_permission_or_validation_changes=False)
        (directory/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')


if __name__=='__main__':unittest.main()
