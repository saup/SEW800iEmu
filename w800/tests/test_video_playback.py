"""Original MPEG-4 frames, AAC PCM and physical stop/replay in real QEMU."""
import array
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import wave

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from w800.backend import ROOT
from w800.guest_ui import OriginalUISession
from w800.tests.test_menu_services import press


def frame_hash(session, top=50, height=144):
    # Only the movie area, excluding time/progress and status/softkey bars.
    image = session.frame().copy(0, top, 176, height)
    return hashlib.sha256(image.constBits().tobytes()).hexdigest()


@unittest.skipUnless(sys.platform == 'darwin' and (ROOT / 'build/qemu-system-arm').exists(),
                     'Local QEMU build and AudioConverter required')
class VideoPlaybackTests(unittest.TestCase):
    def test_original_movie_pcm_stop_replay_and_back(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = Path(directory) / 'video.wav'
            first_capture = Path(directory) / 'first-play.wav'
            with OriginalUISession(default_theme=True, audio_capture_path=capture) as session:
                try:
                    for key in ('select', 'down', 'left', 'select', 'down', 'select', 'down'):
                        press(session, key)
                    listing = frame_hash(session, 20, 175)
                    press(session, 'select')
                    session.advance(500)
                    preview = frame_hash(session)
                    press(session, 'soft_left')
                    frames = []
                    for _ in range(4):
                        session.advance(1000)
                        frames.append(frame_hash(session))
                    session.frame().save(str(ROOT / 'reports/video-playback-test-movie.png'))
                    self.assertGreaterEqual(len(set(frames)), 3,
                        'Original movie pixels did not animate independently of the progress bar')
                    diagnostics = session.q.diagnostics()
                    self.assertIn('AAC_PCM source=guest-stream4 rate=48000', diagnostics)
                    press(session, 'soft_left')  # Native Stop returns preview.
                    session.advance(500)
                    self.assertEqual(frame_hash(session), preview)
                    self.assertIn('AAC_RESOURCE operation=4 capacity=0', session.q.diagnostics())
                    # QEMU's WAV backend reopens/truncates its file when
                    # the next decoder voice opens. Preserve the completed
                    # first playback before exercising native replay.
                    shutil.copyfile(capture, first_capture)
                    press(session, 'soft_left')  # Native Play creates a new decoder.
                    session.advance(1000)
                    replay = frame_hash(session)
                    session.advance(1000)
                    self.assertNotEqual(frame_hash(session), replay)
                    press(session, 'back')
                    session.advance(500)
                    self.assertEqual(frame_hash(session), preview)
                    press(session, 'back')
                    session.advance(500)
                    self.assertEqual(frame_hash(session, 20, 175), listing)
                    diagnostics = session.q.diagnostics()
                    self.assertEqual(diagnostics.count('AAC_RESOURCE operation=3 capacity=1024'), 2)
                    self.assertEqual(diagnostics.count('AAC_RESOURCE operation=4 capacity=0'), 2)
                    self.assertNotIn('AAC_DECODE_ERROR', diagnostics)
                    self.assertNotIn('AAC_UNSUPPORTED_HEADER', diagnostics)
                    self.assertFalse(session.q.pressed_keys)
                    self.assertTrue(session.report()['source_unchanged'])
                finally:
                    report = session.report()
                    report['diagnostics'] = session.q.diagnostics()
                    (ROOT / 'reports/video-playback-test.json').write_text(json.dumps(report, indent=2))
            for path, minimum_seconds in ((first_capture, 2), (capture, 1)):
                with wave.open(str(path)) as output:
                    self.assertEqual(output.getnchannels(), 2)
                    self.assertEqual(output.getsampwidth(), 2)
                    self.assertGreater(output.getnframes(), output.getframerate() * minimum_seconds)
                    pcm = array.array('h', output.readframes(output.getnframes()))
                    self.assertGreater(sum(sample != 0 for sample in pcm), 1000)
