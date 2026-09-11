"""Original renderer navigation using two pages from a local HTTP server."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest
from unittest.mock import patch
from concurrent.futures import Future
from urllib.error import URLError
from w800.backend import ROOT
from w800.guest_ui import OriginalUISession
from w800.tests.test_menu_services import press, body
from w800.host_web import USER_AGENT


class Pages(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.requests.append((self.path, self.headers.get('User-Agent')))
        text = ('<html><head><title>First page</title></head><body>One '
                '<a href="next.html">Next page</a></body></html>' if self.path == '/' else
                '<html><head><title>Second page</title></head><body>Two</body></html>')
        data = text.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'image/png' if self.path == '/unsupported' else 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def log_message(self, *args): pass


@unittest.skipUnless((ROOT/'build/qemu-system-arm').exists(), 'Local QEMU required')
class BrowserHostTests(unittest.TestCase):
    def test_native_url_relative_link_and_back(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Pages)
        server.requests = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with OriginalUISession(default_theme=True, start_phone_standby=True, host_web=True) as s:
                press(s, 'select')
                self.assertTrue(s.host_browser.profile_ready)
                self.assertEqual(s.provenance['internet_profile']['name'], 'Host Internet')
                self.assertGreater(s.provenance['internet_profile']['account_id'], 0)
                press(s, 'soft_right', 'up', 'select')
                for _ in range(5): s.advance(1000)
                self.assertTrue(s.host_browser.rows[-1]['local_start_page'])
                self.assertNotIn('Goto WAP_Error_Dialog_Page', s.messages)
                s.frame().save(str(ROOT/'reports/internet-services-start.png'))
                press(s, 'soft_right', 'down', 'down', 'select', 'select')
                self.assertIn('Goto EUH_Enter_URL_Page', s.messages)
                s.frame().save(str(ROOT/'reports/internet-services-address.png'))
                press(s, 'back', 'back')
                url = f'http://127.0.0.1:{server.server_port}/'
                pointer = s.files.stage(14000, url.encode() + b'\0')
                s.call_on_mmi(0x450bec44, 0x44245520, 0, 6, pointer)
                for _ in range(8): s.advance(1000)
                self.assertEqual(len(s.host_browser.rows), 2)
                self.assertEqual(s.host_browser.rows[-1]['status'], 200)
                first = body(s)
                first_title = s.frame().copy(0,20,176,28)
                s.frame().save(str(ROOT/'reports/host-browser-first.png'))
                press(s, 'select')
                for _ in range(8): s.advance(1000)
                s.frame().save(str(ROOT/'reports/host-browser-second.png'))
                self.assertEqual([row[0] for row in server.requests], ['/', '/next.html'])
                self.assertTrue(all(row[1] == USER_AGENT for row in server.requests))
                self.assertEqual(len(s.host_browser.rows), 3)
                self.assertTrue(s.host_browser.rows[-1]['url'].endswith('/next.html'))
                self.assertNotEqual(body(s), first)
                self.assertNotEqual(s.frame().copy(0,20,176,28), first_title,
                                    "Browser still displays the first document title")
                self.assertEqual(s.browser_unavailable.displayed, 0)
                self.assertNotIn('Goto WAP_CreateProfileQuestion_Page', s.messages)
                self.assertNotIn('Goto WAP_AddProfile_Question_Page', s.messages)
                s.host_browser.open(url + 'unsupported')
                for _ in range(8): s.advance(1000)
                self.assertIn('error', s.host_browser.rows[-1])
                s.frame().save(str(ROOT/'reports/host-browser-error.png'))
                press(s, 'soft_right')
                self.assertIn('Goto CardOptionMenu_Page', s.messages)
                press(s, 'back')
                failed = Future()
                failed.set_exception(URLError('hostname lookup failed'))
                with patch.object(s.host_browser.host, 'fetch', return_value=failed):
                    pointer = s.files.stage(14000, b'http://missing.invalid/\0')
                    s.call_on_mmi(0x450bec44, 0x44245520, 0, 6, pointer)
                    for _ in range(5): s.advance(1000)
                self.assertIn('hostname lookup failed', s.host_browser.rows[-1]['error'])
                self.assertNotIn('Goto WAP_Error_Dialog_Page', s.messages)
                press(s, 'soft_right')
                self.assertIn('Goto CardOptionMenu_Page', s.messages)
                press(s, 'back')
                self.assertTrue(s.ready)
                self.assertFalse(s.q.pressed_keys)
                self.assertTrue(s.report()['source_unchanged'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

if __name__ == '__main__': unittest.main()
