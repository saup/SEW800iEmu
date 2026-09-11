"""Native original rejection, correlation and unchanged radio context."""
import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from w800.backend import ROOT
from w800.guest_ui import OriginalUISession


@unittest.skipUnless((ROOT / 'build/qemu-system-arm').exists(), 'Local QEMU build required')
class OriginalRadioRejectionTests(unittest.TestCase):
    def test_original_negative_reply_and_nonzero_correlation(self):
        with OriginalUISession(start_screen='menu') as session:
            endpoint = session.radio_rejection
            before = session.d.read(endpoint.context, 6)
            self.assertEqual(before[3:5], b'\x01\x01')
            self.assertEqual(session.word(0xf9030004), 0)
            descriptor = session.files.stage(16100, bytes.fromhex('0000000078563412'))
            output = session.files.stage(16112, b'\xaa')
            # Original synchronous request API, original handler, and
            # original decoder. API return zero only means reply decoded;
            # the actual radio-operation result must remain error one.
            result = session.call_on_mmi(0x4496e4c4, descriptor, output)
            self.assertEqual(result, 0)
            self.assertEqual(session.d.read(output, 1), b'\x01')
            self.assertTrue(any(row['correlation'] == 0x12345678 for row in endpoint.rows))
            self.assertEqual(endpoint.completed, 1)
            self.assertFalse(endpoint.pending)
            self.assertEqual(session.d.read(endpoint.context, 6), before)
            self.assertEqual(session.word(0xf9030004), 0)
            self.assertTrue(session.ready)
            self.assertEqual(bytes.fromhex(session.d.packet('g')), session.files.saved)
            self.assertTrue(session.report()['source_unchanged'])


if __name__ == '__main__':
    unittest.main()
