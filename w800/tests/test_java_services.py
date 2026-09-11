"""Original installer ABI restoration; live native installation has a QEMU probe."""
import struct
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtGui import QImage

from w800.guest_ui import MMI_CALL
from w800.java_services import (NATIVE_JAR_INSTALLER, _call_installer_on_mmi,
                                finish_bundled_game_installation,
                                finish_bundled_application_installation,
                                provision_bundled_games, installation_dialog)


class Debugger:
    def __init__(self):
        self.memory = b'original'
        self.regs = [0] * 16
        self.regs[13], self.regs[15] = 0x4c223a40, MMI_CALL

    def registers(self):
        return list(self.regs)

    def read(self, address, size):
        assert address == 0x4c223a40 and size == 8
        return self.memory

    def packet(self, command):
        prefix, payload = command.split(':')
        assert prefix == 'M4c223a40,8'
        self.memory = bytes.fromhex(payload)
        return 'OK'


class Session:
    def __init__(self, bad_stack=False, no_return=False):
        self.d = Debugger()
        self.ready = True
        self.pending_menu = None
        self.bad_stack, self.no_return = bad_stack, no_return

    def call_on_mmi(self, function, *args):
        assert function == NATIVE_JAR_INSTALLER
        assert args == (0x4c500000, 0, 0, 0x4c501000)
        self.pending_menu = {'function': function}
        self.d.registers()
        self.native_stack_arguments = struct.unpack('<2I', self.d.memory)
        self.pending_menu['saved'] = b'original register packet'
        if self.no_return:
            raise TimeoutError('Native call did not return')
        if self.bad_stack:
            self.d.regs[13] += 4
        self.d.registers()
        self.pending_menu = None
        return 1


class InstallerABITests(unittest.TestCase):
    def test_native_arguments_and_original_stack_restored(self):
        session = Session()
        original_reader = session.d.registers
        result = _call_installer_on_mmi(session, 0x4c500000, 0, 0x4c501000)
        self.assertEqual(result, 1)
        self.assertEqual(session.native_stack_arguments, (0, 0))
        self.assertEqual(session.d.memory, b'original')
        self.assertEqual(session.d.registers, original_reader)
        self.assertTrue(session.ready)

    def test_different_return_stack_invalidates_vm(self):
        session = Session(bad_stack=True)
        with self.assertRaisesRegex(RuntimeError, 'different MMI stack'):
            _call_installer_on_mmi(session, 0x4c500000, 0, 0x4c501000)
        self.assertFalse(session.ready)

    def test_missing_return_invalidates_vm(self):
        session = Session(no_return=True)
        with self.assertRaises(TimeoutError):
            _call_installer_on_mmi(session, 0x4c500000, 0, 0x4c501000)
        self.assertFalse(session.ready)


class DialogSession:
    def __init__(self):
        self.provenance = {}
        self.keys = []

    def frame(self):
        return None

    def advance(self, milliseconds, event=None):
        if event is not None:
            self.keys.append(event)


class InstallerDialogTests(unittest.TestCase):
    def test_reviewed_original_dialogs_in_both_real_themes(self):
        fixtures = Path(__file__).parent / 'fixtures/java-dialogs'
        for theme in ('red', 'orange'):
            for name in ('save_games', 'save_applications', 'saved_games', 'start_now'):
                with self.subTest(theme=theme, dialog=name):
                    frame = QImage(str(fixtures / f'{theme}-{name}.png'))
                    self.assertFalse(frame.isNull())
                    self.assertEqual(installation_dialog(frame), name)

    def test_original_runtime_permissions_and_games_folder_are_not_installer_choices(self):
        fixtures = Path(__file__).parent / 'fixtures/java-dialogs'
        for name in ('runtime-read', 'runtime-write', 'games-folder'):
            with self.subTest(dialog=name):
                frame = QImage(str(fixtures / f'{name}.png'))
                self.assertFalse(frame.isNull())
                self.assertIsNone(installation_dialog(frame))

    def test_reviewed_original_saved_applications_dialog(self):
        path = Path(__file__).parent / 'fixtures/java-dialogs/orange-saved_applications.png'
        self.assertEqual(installation_dialog(QImage(str(path))), 'saved_applications')

    def test_application_destination_is_not_games(self):
        session = DialogSession()
        observed = ['save_games', 'save_applications', 'saved_applications',
                    'start_now', None, None, None]
        with patch('w800.java_services.installation_dialog', side_effect=observed):
            record = finish_bundled_application_installation(session, {'game': 'WorldClock3D'})
        self.assertEqual([key for key, down in session.keys if down],
                         ['up', 'select', 'soft_left', 'soft_right'])
        self.assertEqual(record['destination'], 'Applications')
        self.assertNotIn('native_game_installations', session.provenance)
        with self.assertRaises(ValueError):
            provision_bundled_games(session, ('WorldClock3D',))

    def test_native_save_and_defer_launch_with_stale_frames(self):
        session = DialogSession()
        observed = ['save_applications', 'save_applications', 'save_games',
                    'save_games', None, 'start_now', 'start_now', None, None, None]
        with patch('w800.java_services.installation_dialog', side_effect=observed):
            record = finish_bundled_game_installation(session, {'game': 'QuadraPop'})
        self.assertEqual([key for key, down in session.keys if down],
                         ['down', 'select', 'soft_right'])
        self.assertTrue(record['native_completion_observed'])

    def test_unknown_or_runtime_permission_dialog_receives_no_keys(self):
        session = DialogSession()
        with patch('w800.java_services.installation_dialog', return_value=None):
            with self.assertRaises(TimeoutError):
                finish_bundled_game_installation(session, {'game': 'PuzzleSlider'})
        self.assertEqual(session.keys, [])
        self.assertEqual(session.provenance, {})

if __name__ == '__main__':
    unittest.main()
