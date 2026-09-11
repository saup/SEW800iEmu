"""Same-VM file changes, original live view and uploaded-JAR execution."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from w800.backend import ROOT
from w800.guest_ui import OriginalUISession
from w800.java_services import installation_dialog, observe_java_state
from w800.live_filesystem import LiveFilesystem

app = QApplication.instance() or QApplication([])
FIXTURES = {}
for name in ('live-filesystem-native.json', 'java-native-games.json'):
    FIXTURES.update(json.loads((Path(__file__).parent / 'fixtures' / name).read_text())['regions'])


@unittest.skipUnless((ROOT / 'build/qemu-system-arm').exists(), 'Local QEMU build required')
class LiveFilesystemTests(unittest.TestCase):
    def press(self, key):
        self.keys.append(key)
        self.session.advance(120, (key, True))
        self.session.advance(450, (key, False))

    def capture(self, label):
        self.session.frame().save(str(self.output / (label + '.png')))

    def expect(self, label):
        row = FIXTURES[label]
        x, y, width, height = row['rect']
        for _ in range(12):
            frame = self.session.frame()
            if row['mask'] == 'dark':
                foreground = lambda rgb: max(rgb) < 128
            elif row['mask'] == 'light':
                foreground = lambda rgb: min(rgb) > 235
            else:
                foreground = lambda rgb: max(rgb) < 60 or min(rgb) > 235
            mask = bytes(int(foreground(frame.pixelColor(xx, yy).getRgb()[:3]))
                         for yy in range(y, y + height) for xx in range(x, x + width))
            if hashlib.sha256(mask).hexdigest() == row['sha256']:
                self.capture(label)
                return
            self.session.advance(250)
        self.capture('failure-' + label)
        self.fail('Original LCD did not match ' + label)

    def expect_dialog(self, dialog):
        for _ in range(20):
            if installation_dialog(self.session.frame()) == dialog:
                self.capture(dialog)
                return
            self.session.advance(250)
        self.capture('failure-' + dialog)
        self.fail('Original installer did not show ' + dialog)

    def test_live_refresh_upload_roundtrip_native_install_and_game(self):
        self.output = ROOT / 'reports/live-filesystem-test'
        self.output.mkdir(exist_ok=True)
        self.keys = []
        checks = []
        result = dict(status='FAIL')
        with OriginalUISession(default_theme=True, audio_output='none') as session:
            self.session = session
            files = LiveFilesystem(session)
            process = session.q.process
            try:
                self.press('select')
                files.upload_bytes('/tpa/user/other/Before.txt', b'Before refresh.\n')
                for key in ('down', 'left', 'select', 'up', 'select'):
                    self.press(key)
                self.capture('before-upload')
                # No physical keys between writing and the expected new name.
                key_count = session.q.key_events_sent
                record = files.upload_bytes('/tpa/user/other/After.txt', b'After refresh.\n')
                self.assertEqual(record['native_content_change_event'], '0x0f6e')
                self.assertEqual(session.q.key_events_sent, key_count)
                self.expect('native_after_upload')
                checks.append('Already-open native folder refreshes after real content-change event without keys')
                with tempfile.TemporaryDirectory(prefix='w800-live-files-') as temporary:
                    host = Path(temporary)
                    content = bytes(range(256)) * 129 + b'final-three'
                    source = host / 'Roundtrip.bin'
                    source.write_bytes(content)
                    target = '/tpa/user/other/Roundtrip.bin'
                    files.execute(dict(operation='upload', path=target, source=str(source)))
                    self.assertEqual(files.read_file(target, len(content)), content)
                    with self.assertRaises(OSError):
                        files.read_file(target, len(content) - 1)
                    with self.assertRaises(FileExistsError):
                        files.upload_bytes(target, b'do not overwrite')
                    self.assertEqual(files.read_file(target), content)
                    export = host / 'export.bin'
                    files.execute(dict(operation='export', path=target, destination=str(export)))
                    self.assertEqual(export.read_bytes(), content)
                    files.upload_bytes(target, b'explicit replacement', replace=True)
                    self.assertEqual(files.read_file(target), b'explicit replacement')
                    checks.append('Multi-chunk binary upload/read/export, exact-size EOF, collision protection and explicit truncating replacement')
                    jar = files.read_file('/tpa/preset/default/java/QuadraPop.jar')
                    self.assertEqual(hashlib.sha256(jar).hexdigest(),
                                     '00a59577234124697f5a6746179473049b83d2ade8c0afe3e30eac7a87f1eabf')
                    source = host / 'UploadedQuadra.jar'
                    source.write_bytes(jar)
                    jar_path = '/tpa/user/other/UploadedQuadra.jar'
                    files.execute(dict(operation='upload', path=jar_path, source=str(source)))
                # Delete the existing game through its own menu. The upload is
                # then a real fresh installation, not merely an accepted API.
                for key in ('back', 'up', 'up', 'select', 'down', 'soft_right', 'down', 'down', 'select'):
                    self.press(key)
                self.expect('delete_quadra')
                self.press('soft_left')
                session.advance(500)
                self.capture('quadra-deleted')
                record = files.begin_installation(jar_path)
                self.assertEqual(record['automatic_dialog_answers'], 0)
                self.expect_dialog('save_games')
                result['installer_dialog_state'] = observe_java_state(session)
                self.press('select')
                self.expect_dialog('saved_games')
                self.press('soft_left')
                self.expect_dialog('start_now')
                self.press('soft_right')
                session.advance(750)
                for key in ('back', 'select'):
                    self.press(key)
                self.expect('games_puzzle')
                self.expect('games_quadra')
                checks.append('Uploaded unmodified JAR installed through normal physical dialogs and visible after reopening Games')
                self.press('down')
                self.press('select')
                self.expect('quadra_new_game')
                self.press('select')
                self.expect('quadra_score')
                self.press('6')
                self.press('5')
                self.expect('quadra_score')
                self.capture('uploaded-game-input')
                checks.append('Reinstalled original Java game starts and handles physical gameplay keys')
                self.press('back')
                self.capture('uploaded-game-quit-menu')
                self.press('soft_right')
                session.advance(750)
                self.capture('uploaded-game-exited')
                self.press('select')
                self.expect('quadra_resume_game')
                checks.append('Original game Quit returns to Games and the uploaded title relaunches')
                self.assertIs(session.q.process, process)
                self.assertTrue(session.ready)
                self.assertFalse(session.q.pressed_keys)
                self.assertEqual(bytes.fromhex(session.d.packet('g')), session.files.saved)
                self.assertTrue(session.report()['source_unchanged'])
                result['status'] = 'PASS'
            finally:
                result.update(checks=checks, keys=self.keys, qemu_pid=process.pid,
                              session=session.report())
                (self.output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        self.assertIsNotNone(process.poll())


if __name__ == '__main__':
    unittest.main()
