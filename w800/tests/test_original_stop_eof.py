"""Original odd-length Stop MP3 completion and automatic next-track lifecycle."""
import os
import subprocess
import sys
import unittest

from w800.backend import ROOT


@unittest.skipUnless(sys.platform=='darwin' and (ROOT/'build/qemu-system-arm').exists(),
                     'Local macOS QEMU decoder required')
class OriginalStopEOFTests(unittest.TestCase):
    def test_stop_finishes_and_native_walkman_starts_next_track(self):
        directory=ROOT/'reports/walkman-stop-eof-fixed'
        result=subprocess.run([sys.executable,'-m','w800.tools.probe_stop_eof',
            '--directory',str(directory),'--verify'],cwd=ROOT.parent,
            env=dict(os.environ,QT_QPA_PLATFORM='offscreen'),capture_output=True,
            text=True,timeout=180)
        self.assertEqual(result.returncode,0,result.stdout[-4000:]+result.stderr[-4000:])


if __name__=='__main__':unittest.main()
