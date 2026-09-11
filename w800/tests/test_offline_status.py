"""Original native message/error handling and navigation after the idle wait."""
import os
import struct
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from w800.backend import ROOT
from w800.guest_ui import OriginalUISession
from w800.offline_status import STATUS_UNAVAILABLE, REASON_INACTIVE
from w800.tests.test_menu_services import body, press


@unittest.skipUnless((ROOT / 'build/qemu-system-arm').exists(), 'Local QEMU build required')
class OfflineStatusTests(unittest.TestCase):
    def test_unavailable_native_decoder_and_correlation(self):
        with OriginalUISession(start_screen='menu', default_theme=True) as session:
            endpoint = session.offline_status
            # Ask through the original synchronous request API on MMI with
            # a nonzero correlation. Exercise both queue ownership and the
            # original reply decoder, not a host decoder reimplementation.
            descriptor = session.files.stage(16100, bytes.fromhex('0000000078563412'))
            outputs = session.files.stage(16112, b'\xaa\xbb\xcc')
            session.call_on_mmi(0x44964f14, descriptor, outputs, outputs + 1, outputs + 2)
            self.assertEqual(session.mmi_call_result, STATUS_UNAVAILABLE)
            self.assertEqual(session.d.read(outputs, 3), bytes([0, 0, REASON_INACTIVE]))
            self.assertTrue(any(row['correlation'] == 0x12345678 for row in endpoint.responses))
            self.assertEqual(endpoint.routed, endpoint.completed)
            # The separate read-only audio-state query must preserve the
            # original unavailable result, never invent valid codec records.
            outputs = session.files.stage(16112, b'\xaa' * 13)
            session.call_on_mmi(0x44948d70, descriptor, outputs, outputs + 6, outputs + 12)
            self.assertEqual(session.mmi_call_result, 7)
            self.assertEqual(session.d.read(outputs, 13), bytes(12) + b'\x2e')
            replies = [bytes.fromhex(row['bytes']) for row in endpoint.responses
                       if bytes.fromhex(row['bytes'])[:4] == struct.pack('<I', 0x59f0)]
            self.assertEqual(len(replies), 1)
            self.assertEqual(len(replies[0]), 24)
            self.assertEqual(struct.unpack_from('<I', replies[0], 4)[0], 0x12345678)
            self.assertEqual((replies[0][8], replies[0][21]), (7, 0x2e))
            # PLMN query must complete even though the radio task is inactive.
            outputs = session.files.stage(16112, b'\xaa' * 12)
            session.call_on_mmi(0x4496f048, descriptor, outputs, outputs + 8, outputs + 11)
            self.assertEqual(session.mmi_call_result, 7)
            self.assertEqual(session.d.read(outputs, 12), bytes(11) + b'\x06')
            reply = bytes.fromhex(endpoint.responses[-1]['bytes'])
            self.assertEqual(struct.unpack_from('<2I', reply), (0x5bbe, 0x12345678))
            self.assertEqual(len(reply), 24)
            self.assertFalse(endpoint.pending)
            self.assertEqual(endpoint.routed, endpoint.completed)
            self.assertEqual(session.word(0xf9030004), 0)
            self.assertTrue(session.ready)
            self.assertEqual(bytes.fromhex(session.d.packet('g')), session.files.saved)

    def test_full_services_physical_navigation_after_idle(self):
        with OriginalUISession(start_screen='startup', default_theme=True,
                               full_services=True) as session:
            endpoint = session.offline_status
            initial_etx = session.word(0xf9030004)
            self.assertEqual(initial_etx, 0)
            press(session, 'select')
            self.assertTrue(any(e['checkpoint'] == 'Start phone → original MainMenu' for e in session.events))
            press(session, 'right')
            for _ in range(35):
                session.advance(1000)
            # The original display idle mode consumes the first key to wake.
            press(session, 'left')
            walkman = body(session)
            press(session, 'left')
            messaging = body(session)
            self.assertTrue(messaging != walkman, 'Second late Left did not change the menu body')
            press(session, 'down', 'left', 'select')
            self.assertIn('Goto DataBrowserCategoryList_ViewStatus_Page', session.messages)
            categories = body(session)
            self.assertTrue(categories != messaging, 'File manager did not change the menu body')
            press(session, 'select')
            self.assertIn('Goto DataBrowser_Main_Page', session.messages)
            self.assertTrue(body(session) != categories, 'Pictures did not change the menu body')
            press(session, 'back')
            self.assertGreaterEqual(endpoint.completed, 8)
            self.assertEqual(endpoint.routed, endpoint.completed)
            self.assertFalse(endpoint.pending)
            for row in endpoint.responses:
                signal, correlation, status, _, _, reason = struct.unpack('<2I4B', bytes.fromhex(row['bytes']))
                self.assertEqual(signal, 0x5a85)
                self.assertEqual(correlation, row['correlation'])
                self.assertEqual((status, reason), (7, 1))
            self.assertEqual(session.word(0xf9030004), initial_etx)
            self.assertEqual(session.word(0x4c04a5c8), 0x44ad3d01)
            self.assertTrue(session.ready)
            self.assertFalse(session.q.pressed_keys)
            self.assertEqual(bytes.fromhex(session.d.packet('g')), session.files.saved)
            self.assertTrue(session.report()['source_unchanged'])
            self.assertFalse(endpoint.snapshot()['activation_adapter_connected'])
            self.assertFalse(endpoint.snapshot()['deactivation_adapter_connected'])


if __name__ == '__main__':
    unittest.main()
