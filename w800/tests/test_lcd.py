"""PDI bus, panel addressing, actual DMA pixels, and completion IRQ checks."""
import struct
import unittest
from PySide6.QtGui import QImage
from w800.backend import QemuBackend, ROOT
from test_storage import Bus

WRITE = bytes.fromhex('1f1d09001f2090991f289c99179800001705150517f81f001f08')
DMA = bytes.fromhex('1f1d09001f2090991f289c9917980a001705150517f813800400178802001760150117e017e81f001f0d')

@unittest.skipUnless((ROOT/'build/qemu-system-arm').exists(), 'Local QEMU build required')
class LCDTests(unittest.TestCase):
    def setUp(self):
        self.q = QemuBackend(bus_test=True); self.q.start(); self.bus = Bus(self.q)
        self.q.command('cont')
        self.write(0xf7000100, WRITE)
        self.write(0xf700011c, DMA)
        self.bus.put(0xf70001fc, 0x1c, 'l')
    def tearDown(self):
        self.bus.close(); self.q.close()
    def write(self, addr, data):
        for i, b in enumerate(data): self.bus.put(addr+i, b)
    def commands(self, pairs):
        payload = b''.join(struct.pack('>HH',r,v) for r,v in pairs)
        self.write(0xf700010e, struct.pack('<H',len(payload)))
        self.write(0xf7000180,bytes(16))
        self.write(0xf7000180-len(payload),payload)
        rs = sum(3 << (128-len(payload)+4*i+2) for i in range(len(pairs)))
        self.write(0xf7000180,rs.to_bytes(16,'little'))
        self.bus.put(0xf70001dc,1,'l')
        self.assertEqual(self.bus.get(0xf70001f4,'l') & 9,0)
    def pixel(self,x,y):
        im=QImage(str(self.q.framebuffer_path()))
        self.assertEqual((im.width(),im.height()),(176,220))
        c=im.pixelColor(x,y)
        return (c.red(),c.green(),c.blue())
    def test_panel_dma_window_pixel_order_and_interrupt(self):
        self.commands([(3,0x30),(7,0x37),(0x10,0x2020),(0x44,0x0302),(0x45,0x0201)])
        # DMA prefix is the original R61500_B descriptor; row 1, column 2.
        # Right-align the actual 10 bytes; source descriptor uses the last 10.
        self.write(0xf7000176,bytes.fromhex('00000000002101020022'))
        self.write(0xf7000180,(0x30000000 << 96).to_bytes(16,'little'))
        source=0x4c100000
        self.write(source,struct.pack('>4H',0xf800,0x07e0,0x001f,0xffff))
        self.bus.put(0xf20001a0,source,'l');self.bus.put(0xf20001a4,0xf7000308,'l')
        self.bus.put(0xf20001b0,1,'l')
        self.assertEqual(self.bus.get(0xf70001f4,'l') & 1,1)
        self.bus.command('clock_step 200000')
        self.assertEqual(self.bus.get(0xfb020200,'l') & (1<<13),1<<13)
        self.assertEqual([self.pixel(x,y) for x,y in [(2,1),(3,1),(2,2),(3,2)]],
                         [(255,0,0),(0,255,0),(0,0,255),(255,255,255)])
        self.assertEqual(self.pixel(0,0),(0,0,0))
        self.bus.put(0xf70001dc,5,'l')
        self.assertEqual(self.bus.get(0xfb020200,'l') & (1<<13),0)
        self.commands([(7,0)])
        self.assertEqual(self.pixel(2,1),(0,0,0))
        self.commands([(7,0x37)])
        self.assertEqual(self.pixel(2,1),(255,0,0))
    def test_unknown_program_reports_error_and_device_id(self):
        self.bus.put(0xf7000100,0,'l');self.bus.put(0xf70001dc,1,'l')
        self.assertEqual(self.bus.get(0xf70001f4,'l')&8,8)
        self.bus.put(0xf700017c,0,'l');self.bus.put(0xf70001dc,3,'l')
        self.assertEqual(self.bus.get(0xf70001f4,'l')>>8,0x1500)

    def test_original_source_direction_and_bgr_preserve_rgb_colors(self):
        # R61500_B uses SS=1 and BGR=1 together. Renesas tables 11–14
        # preserve RGB order for this pair; BGR alone swaps GRAM channels.
        self.commands([(1,0x011b),(3,0x7230),(7,0x37),(0x10,0x2020),
                       (0x44,0x0300),(0x45,0)])
        self.write(0xf7000176,bytes.fromhex('00000000002100000022'))
        self.write(0xf7000180,(0x30000000 << 96).to_bytes(16,'little'))
        source=0x4c100000
        self.write(source,struct.pack('>4H',0xf800,0x07e0,0x001f,0xffff))
        self.bus.put(0xf20001a0,source,'l')
        self.bus.put(0xf20001a4,0xf7000308,'l')
        self.bus.put(0xf20001b0,1,'l')
        self.bus.command('clock_step 200000')
        expected=[(255,0,0),(0,255,0),(0,0,255),(255,255,255)]
        self.assertEqual([self.pixel(x,0) for x in range(4)],expected)
        # BGR applies when writing GRAM, not retroactively during scanout.
        self.commands([(3,0x6230)])
        self.assertEqual([self.pixel(x,0) for x in range(4)],expected)

    def test_original_partial_redraw_single_byte_count_loop(self):
        # Actual startup-menu redraw: 176 columns x 147 rows, from row 47.
        # R61500_B uses 51744 x 1 bytes here, versus 38720 x 2 for full-screen.
        self.commands([(3,0x30),(7,0x37),(0x10,0x2020),
                       (0x44,0xaf00),(0x45,(193 << 8) | 47)])
        program=bytearray(DMA)
        struct.pack_into('<H',program,24,51744)
        struct.pack_into('<H',program,28,1)
        self.write(0xf700011c,program)
        self.write(0xf7000176,bytes.fromhex('0000000000212f000022'))
        self.write(0xf7000180,(0x30000000 << 96).to_bytes(16,'little'))
        source=0x4c100000
        pixels=bytes.fromhex('f800') * (176 * 147)
        self.bus.command(f'write {source:#x} {len(pixels)} 0x{pixels.hex()}')
        self.bus.put(0xf20001a0,source,'l')
        self.bus.put(0xf20001a4,0xf7000308,'l')
        self.bus.put(0xf20001b0,1,'l')
        self.assertEqual(self.bus.get(0xf70001f4,'l') & 9,1)
        self.bus.command('clock_step 200000')
        self.assertEqual(self.bus.get(0xfb020200,'l') & (1<<13),1<<13)
        self.assertEqual(self.bus.get(0xf20001a0,'l'),source+len(pixels))
        for x,y in [(0,47),(175,47),(0,193),(175,193)]:
            self.assertEqual(self.pixel(x,y),(255,0,0))
        for x,y in [(0,46),(175,194)]:
            self.assertEqual(self.pixel(x,y),(0,0,0))

    def test_video_high_color_frame_and_return_to_menu_format(self):
        # Original video selection sets R03=D230 and requests 58080 x 2
        # bus bytes: 176 x 220 RGB666 pixels. This exceeds an RGB565 frame.
        self.commands([(1,0x011b),(3,0xd230),(7,0x37),(0x10,0x2020),
                       (0x44,0xaf00),(0x45,0xdb00)])
        program=bytearray(DMA)
        struct.pack_into('<H',program,24,58080)
        self.write(0xf700011c,program)
        self.write(0xf7000176,bytes.fromhex('00000000002100000022'))
        self.write(0xf7000180,(0x30000000 << 96).to_bytes(16,'little'))
        colors=[(63,32,1),(1,2,3),(15,16,17),(0,63,0)]
        pixels=b''.join(bytes(v << 2 for v in rgb) for rgb in colors) * (176*220//4)
        source=0x4c100000
        self.bus.command(f'write {source:#x} {len(pixels)} 0x{pixels.hex()}')
        self.bus.put(0xf20001a0,source,'l')
        self.bus.put(0xf20001a4,0xf7000308,'l')
        self.bus.put(0xf20001b0,1,'l')
        self.assertEqual(self.bus.get(0xf70001f4,'l') & 9,1)
        self.bus.command('clock_step 200000')
        self.assertEqual(self.bus.get(0xfb020200,'l') & (1<<13),1<<13)
        self.assertEqual(self.bus.get(0xf20001a0,'l'),source+116160)
        expected=[tuple(v*255//63 for v in rgb) for rgb in colors]
        for y in (0,219):
            self.assertEqual([self.pixel(x,y) for x in (0,1,2,175)],
                             [expected[i] for i in (0,1,2,3)])
        # Changing format affects subsequent bus writes, not retained GRAM.
        self.commands([(3,0x7230)])
        self.assertEqual(self.pixel(0,0),expected[0])
        self.write(0xf700011c,DMA)
        self.write(0xf7000176,bytes.fromhex('00000000002100000022'))
        self.write(0xf7000180,(0x30000000 << 96).to_bytes(16,'little'))
        self.write(source,struct.pack('>4H',0xf800,0x07e0,0x001f,0xffff))
        self.bus.put(0xf20001a0,source,'l')
        self.bus.put(0xf20001b0,1,'l')
        self.bus.command('clock_step 200000')
        self.assertEqual([self.pixel(x,0) for x in range(4)],
                         [(255,0,0),(0,255,0),(0,0,255),(255,255,255)])
        self.assertEqual(self.pixel(0,219),expected[0])

@unittest.skipUnless((ROOT/'build/qemu-system-arm').exists(), 'Local QEMU build required')
class OriginalLCDTests(unittest.TestCase):
    def test_original_firmware_dma_reaches_guest_irq_and_screen(self):
        from w800.tools.debug import Debugger
        with QemuBackend(exploratory=True,debug=True,mmio_limit=1000000) as q:
            d=Debugger(q)
            try:
                # Actual IRQ_HandlerPdi. The first transfer precedes display-on;
                # the next is the firmware's visible boot background.
                handler=0x44ab88b4
                d.breakpoint(handler,thumb=True)
                for i in range(2):
                    d.run()
                    self.assertEqual(d.registers()[15],handler)
                    if not i: d.step_over_breakpoint(handler,thumb=True)
                dma_end=struct.unpack('<I',d.read(0xf20001a0,4))[0]
                pixels=struct.unpack('>16H',d.read(dma_end-176*220*2,32))
                im=QImage(str(q.framebuffer_path()))
                self.assertEqual((im.width(),im.height()),(176,220))
                # R61500_B sets both BGR and SS. Compare hardware output with
                # bytes actually fetched by the guest's DMA, not a golden UI.
                for x,p in enumerate(pixels):
                    expected=((p>>11)*255//31,((p>>5)&63)*255//63,(p&31)*255//31)
                    color=im.pixelColor(x,0)
                    self.assertEqual((color.red(),color.green(),color.blue()),expected)
                self.assertNotEqual(im.pixelColor(0,0).rgb()&0xffffff,0)
            finally: d.close()
