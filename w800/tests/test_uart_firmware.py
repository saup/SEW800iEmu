"""Exercise the original AT service through actual host UART sockets."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import socket
import unittest
from w800.guest_ui import OriginalUISession
from w800.tests.test_menu_services import press


class NativeUARTTests(unittest.TestCase):
    def test_native_attention_and_manufacturer_roundtrip(self):
        with OriginalUISession(default_theme=True,start_phone_standby=True,uart=True) as session:
            press(session,'select')
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as peer:
                peer.connect(str(session.q.uart_paths[0]))
                peer.setblocking(False)
                for command, expected in ((b'AT\r',b'OK'),(b'AT+CGMI\r',b'Sony Ericsson')):
                    peer.sendall(command)
                    response=bytearray()
                    for _ in range(12):
                        session.advance(500)
                        try:response.extend(peer.recv(4096))
                        except BlockingIOError:pass
                        if b'OK\r\n' in response:break
                    self.assertIn(expected,response)
                    self.assertIn(b'OK\r\n',response)
            press(session,'soft_right')
            self.assertTrue(session.ready)
            self.assertTrue(session.report()['source_unchanged'])

if __name__=='__main__':unittest.main()
