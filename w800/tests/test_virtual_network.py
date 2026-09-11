"""Native standby text ownership, offline-state preservation and navigation."""
import os
import unittest
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from w800.backend import ROOT
from w800.guest_ui import OriginalUISession
from w800.virtual_network import validate_name


def press(session, key):
    session.advance(120, (key, True))
    session.advance(450, (key, False))


def pixels(frame, top=20, height=175):
    crop = frame.copy(0, top, 176, height)
    return crop.constBits().tobytes()


class VirtualNetworkValidationTests(unittest.TestCase):
    def test_bounded_phone_text(self):
        self.assertEqual(validate_name('  My local network  '), 'My local network')
        self.assertIsNone(validate_name(''))
        self.assertIsNone(validate_name(None))
        self.assertEqual(validate_name('Žen offline'), 'Žen offline')
        for value in ('x' * 33, 'line\nline', 'x\0x', '😀', 1):
            with self.assertRaises(ValueError):
                validate_name(value)


@unittest.skipUnless((ROOT / 'build/qemu-system-arm').exists(), 'Local QEMU build required')
class VirtualNetworkNativeTests(unittest.TestCase):
    def test_original_label_replacement_clear_and_menu(self):
        directory = ROOT / 'reports/virtual-network-regression'
        directory.mkdir(exist_ok=True)
        with OriginalUISession(default_theme=True) as session:
            process = session.q.process
            startup = pixels(session.frame())
            press(session, 'select')
            press(session, 'back')
            context = session.radio_rejection.context
            radio = session.d.read(context, 6)
            network = session.d.read(0x4c04c9a0, 0x20)
            self.assertEqual(session.word(0xf9030004), 0)
            session.set_virtual_network_name('Creative Zen')
            frame = session.frame()
            frame.save(str(directory / '01-native-name.png'))
            named = pixels(frame, 20, 28)
            self.assertNotEqual(pixels(frame), startup)
            self.assertEqual(session.word(session.virtual_network.book + 0x64) >> 28, 8)
            press(session, 'soft_right')
            frame = session.frame()
            frame.save(str(directory / '02-native-menu.png'))
            self.assertIn('Goto Menu_First_Page', session.messages)
            self.assertNotEqual(pixels(frame, 20, 28), named)
            press(session, 'back')
            self.assertEqual(pixels(session.frame(), 20, 28), named)
            session.set_virtual_network_name('Offline Zen')
            changed = session.frame()
            changed.save(str(directory / '03-native-replaced.png'))
            self.assertTrue(pixels(changed, 20, 28) != named, 'Original displayed label did not change')
            session.set_virtual_network_name('Creative Zen')
            self.assertEqual(pixels(session.frame(), 20, 28), named)
            session.set_virtual_network_name(None)
            restored = session.frame()
            restored.save(str(directory / '04-startup-restored.png'))
            self.assertEqual(pixels(restored), startup)
            self.assertIsNone(session.virtual_network.original_text)
            self.assertEqual(session.d.read(context, 6), radio)
            self.assertEqual(session.d.read(0x4c04c9a0, 0x20), network)
            self.assertEqual(session.word(0xf9030004), 0)
            press(session, 'select')
            self.assertNotEqual(pixels(session.frame()), startup)
            self.assertFalse(session.q.pressed_keys)
            self.assertTrue(session.report()['source_unchanged'])
        self.assertIsNotNone(process.poll())


if __name__ == '__main__': unittest.main()
