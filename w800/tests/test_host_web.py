import threading
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from w800.host_web import HostWeb,USER_AGENT

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path=='/redirect':
            self.send_response(302);self.send_header('Location','/page');self.end_headers();return
        body=(b'x'*2048 if self.path=='/large' else self.headers.get('User-Agent','').encode())
        self.send_response(404 if self.path=='/missing' else 200)
        self.send_header('Content-Type','text/plain');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def log_message(self,*args):pass

class HostWebTests(unittest.TestCase):
    def test_fetch_redirect_error_body_limit_and_shutdown(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with HostWeb(max_bytes=1024) as web:
                url=f'http://127.0.0.1:{server.server_port}'
                result=web.fetch(url+'/redirect').result(15)
                self.assertEqual(result.body,USER_AGENT.encode())
                self.assertTrue(result.url.endswith('/page'))
                self.assertEqual(web.fetch(url+'/missing').result(15).status,404)
                with self.assertRaises(ValueError):web.fetch(url+'/large').result(15)
                with self.assertRaises(ValueError):web.fetch('file:///etc/passwd')
            with self.assertRaises(RuntimeError):web.fetch(url)
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
