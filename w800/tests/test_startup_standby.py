"""The GUI's Start phone destination is the original native standby book."""
import json
import os
import unittest
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from w800.backend import ROOT
from w800.guest_ui import OriginalUISession
from w800.tests.test_menu_services import press, body


def standby_header(session):
    # The Walkman wallpaper animates independently of navigation. Compare the
    # stable original status/title area, not its animation frame or the clock.
    frame = session.frame().copy(0, 20, 176, 28)
    return frame.constBits().tobytes()


class StartupStandbyTests(unittest.TestCase):
    def test_music_then_start_phone_standby_menu_and_name(self):
        with OriginalUISession(default_theme=True, start_phone_standby=True) as session:
            process = session.q.process
            press(session, 'down', 'select')
            self.assertIn('Goto MM_Browser_Toplevel_Bk_MainPage', session.messages)
            press(session, 'back')
            self.assertTrue(session.word(0x4c289034) & (1 << 22))
            press(session, 'up', 'select')
            self.assertFalse(session.word(0x4c289034) & (1 << 22))
            self.assertTrue(session._phone_mode_started)
            self.assertFalse(session._standby_pending)
            self.assertIn('Start phone → original Standby', [e['checkpoint'] for e in session.events])
            standby = body(session)
            header = standby_header(session)
            session.frame().save(str(ROOT / 'reports/start-phone-standby.png'))
            press(session, 'soft_right')
            self.assertIn('Goto Menu_First_Page', session.messages)
            self.assertNotEqual(body(session), standby)
            press(session, 'back')
            self.assertEqual(standby_header(session), header)
            session.set_virtual_network_name('My Network')
            session.set_virtual_network_name(None)
            self.assertEqual(standby_header(session), header)
            press(session, 'soft_right')
            self.assertNotEqual(body(session), standby)
            self.assertFalse(session.q.pressed_keys)
            self.assertTrue(session.ready)
            report = session.report()
            self.assertTrue(report['source_unchanged'])
            (ROOT / 'reports/startup-standby.json').write_text(json.dumps(report, indent=2) + '\n')
        self.assertIsNotNone(process.poll())


if __name__ == '__main__': unittest.main()
