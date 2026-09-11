"""Native unavailable-browser feedback, dismissal, and request observation."""
import os
import struct
import unittest
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from w800.backend import ROOT
from w800.browser_unavailable import (
    BrowserUnavailable, DEFAULT_ERROR_TEXT, ERROR_DISPLAY,
    URL_LOAD_GUARD, URL_LOAD_REQUEST, WAP_STATE,
)
from w800.guest_ui import OriginalUISession
from w800.tests.test_menu_services import body, press, settled_body


class BrowserObservationTests(unittest.TestCase):
    def test_only_rejected_request_triggers_deferred_native_error(self):
        memory = {
            URL_LOAD_GUARD: bytes.fromhex('042829d3'),
            0x44b75374: bytes.fromhex('f1b5'),
            ERROR_DISPLAY: bytes.fromhex('30b5041c0d1c'),
            WAP_STATE: b'\x02\x00',
            0x4c600000: struct.pack('<4I', URL_LOAD_REQUEST, 0x12345678, 0, 0),
            0x4c218000: struct.pack('<I', 0x4c600000),
        }
        calls = []
        session = SimpleNamespace(
            d=SimpleNamespace(read=lambda address, size: memory[address][:size]),
            provenance={}, ready=True, pending_menu=None,
            q=SimpleNamespace(pressed_keys={'select'}),
        )
        adapter = BrowserUnavailable(session)

        def native_call(*args):
            calls.append(args)
            adapter.poll()  # Original calls advance internally.

        session.call_on_mmi = native_call
        regs = [0] * 16
        regs[0], regs[13], regs[15] = 2, 0x4c218000, URL_LOAD_GUARD
        original_memory = memory.copy()
        self.assertTrue(adapter.observe(regs))
        adapter.poll()
        self.assertFalse(calls, 'A held Select would dismiss a newly created native dialog')
        session.q.pressed_keys.clear()
        adapter.poll()
        adapter.poll()
        self.assertEqual(calls, [(ERROR_DISPLAY, DEFAULT_ERROR_TEXT, 0)])
        self.assertEqual(memory, original_memory)
        self.assertEqual((adapter.observed, adapter.displayed), (1, 1))
        regs[0] = 4
        memory[WAP_STATE] = b'\x04\x01'
        self.assertFalse(adapter.observe(regs))
        regs[0] = 2
        memory[WAP_STATE] = b'\x02\x00'
        memory[0x4c600000] = struct.pack('<4I', 0x2593, 0, 0, 0)
        with self.assertRaisesRegex(RuntimeError, 'unrelated traffic'):
            adapter.observe(regs)
        self.assertEqual(len(calls), 1)


@unittest.skipUnless((ROOT / 'build/qemu-system-arm').exists(), 'Local QEMU build required')
class OriginalBrowserUnavailableTests(unittest.TestCase):
    def test_original_browser_initializes_and_requests_profile(self):
        with OriginalUISession(default_theme=True, start_phone_standby=True) as session:
            press(session, 'select')
            for _ in range(3): session.advance(1000)
            self.assertEqual(session.d.read(WAP_STATE, 2), b'\x04\x01')
            self.assertTrue(session.browser_startup.completed)
            url = session.files.stage(14000, b'http://example.com/\0')
            session.call_on_mmi(0x450bec44, 0x44245520, 0, 6, url)
            for _ in range(8): session.advance(1000)
            self.assertIn('Goto BrowseCard_Page', session.messages)
            self.assertIn('Goto WAP_CreateProfileQuestion_Page', session.messages)
            self.assertEqual(session.browser_unavailable.displayed, 0)
            press(session, 'back', 'back')
            self.assertTrue(session.ready)
            self.assertFalse(session.q.pressed_keys)
            self.assertTrue(session.report()['source_unchanged'])


if __name__ == '__main__':
    unittest.main()
