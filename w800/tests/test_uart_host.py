import socket
import struct
import time
import unittest
from w800.backend import QemuBackend
from w800.tools.debug import Debugger

class HostUARTTests(unittest.TestCase):
    def test_each_port_tx_rx_irq_and_reset(self):
        with QemuBackend(exploratory=True,debug=True,uart=True) as q:
            d=Debugger(q)
            try:
                for port in (0,1,4):
                    base={0:0xfb060000,1:0xfc040000,4:0xfb040000}[port]
                    def write(off,value):
                        self.assertEqual(d.packet(f'M{base+off:x},2:'+struct.pack('<H',value).hex()),'OK')
                    def read(off):return int.from_bytes(d.read(base+off,2),'little')
                    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as peer:
                        peer.settimeout(2);peer.connect(str(q.uart_paths[port]))
                        time.sleep(.02)
                        write(2,ord('X'))
                        self.assertEqual(peer.recv(1),b'X')
                        write(0x12,0x41)
                        peer.sendall(b'AT\r')
                        deadline=time.monotonic()+2
                        while read(0x2e)!=3 and time.monotonic()<deadline:time.sleep(.01)
                        self.assertEqual(read(0x2e),3)
                        self.assertEqual(read(0x14),0x41)
                        # Native ISR uses bit12 to drain input before a
                        # protocol client has posted a receive buffer.
                        write(0x12,0x1000)
                        self.assertEqual(read(0x14),0x1000)
                        self.assertTrue(int.from_bytes(d.read(0xfb020200,4),'little')&(1<<(44+port-28)))
                        self.assertEqual(bytes(read(0) for _ in range(3)),b'AT\r')
                        self.assertEqual(read(0x14),0)
                        self.assertEqual(read(0x2c),256)
                        gpio, pin = {0:(0xf900000a,1),1:(0xf900000a,0x10),4:(0xf900001e,1)}[port]
                        self.assertTrue(int.from_bytes(d.read(gpio,2),'little') & pin)
                        # Native autobaud completion supplies 115200/8N1
                        # and remains pending until its W1C acknowledge.
                        write(0x12,0x20)
                        write(0x0a,0x102)
                        peer.sendall(b'A')
                        deadline=time.monotonic()+2
                        while not read(0x14) and time.monotonic()<deadline:time.sleep(.01)
                        self.assertEqual(read(0x14),0x20)
                        self.assertEqual(read(0x20),4)
                        self.assertEqual(read(0x0c)&0x1f,1)
                        self.assertEqual(read(0x0a)&7,0)
                        write(0x14,0x20)
                        self.assertEqual(read(0x14),0)
                        self.assertEqual(read(0),ord('A'))

                q.command('system_reset')
                self.assertEqual(int.from_bytes(d.read(0xfc040012,2),'little'),0)
            finally:d.close()

if __name__=='__main__':unittest.main()
