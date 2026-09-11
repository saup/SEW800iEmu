"""AMR resources retain real coefficient slots and bounded guest input."""
import struct
import sys
import unittest

from w800.backend import QemuBackend, ROOT
from w800.tests.test_storage import Bus


@unittest.skipUnless(sys.platform=='darwin' and (ROOT/'build/qemu-system-arm').exists(),
                     'Local macOS QEMU decoder required')
class AMRResourceTests(unittest.TestCase):
    def test_original_amr_configuration_two_filters_and_incomplete_input_ownership(self):
        with QemuBackend(bus_test=True) as q:
            bus=Bus(q)
            q.command('cont')
            try:
                def put(offset,value):bus.put(0xfa040000+offset,value,'l')
                def get(offset):return bus.get(0xfa040000+offset,'l')
                def send(channel,*words):
                    for i,value in enumerate([((len(words)+1)<<11)|10,*words,channel]):
                        bus.put(0xfa040040+i*2,value,'w')
                    put(0,1)
                    self.assertEqual(get(0)&1,0)
                def ack():
                    put(4,0xfffffffe)
                    bus.command('clock_step 200000')
                def service(channel,*words):
                    send(channel,*words)
                    bus.command('clock_step 200000')
                    self.assertEqual(get(0x80)>>16,words[0]+1,q.diagnostics())
                    ack()
                words={0xa9fe:0xec31,0xa9ff:0xbe00,0xaa00:0xaeba,
                       0xaa14:0xfb41,0xaa15:0x0202,0xaf03:2}
                for pair in sorted({address&~1 for address in words}):
                    put(0x28,pair);put(0x2c,(words.get(pair,0)<<16)|words.get(pair+1,0));put(0x24,1)
                send(18,0x0e04,3)
                self.assertEqual(get(0x80),0x0304180a)
                ack();self.assertEqual(get(0x80),0x0e05180a);ack()
                service(18,0x0e00,0,0,5,0,3,2,2,3,0,1,1,0,32,32,0,0,1,0xffff,2,
                        0,0xffff,3,1,0,0,0xcc03)
                self.assertIn('AAC_CONFIG codec=AMR-NB rate=8000 channels=1',q.diagnostics())
                for slot,count in [(0,32),(1,48)]:
                    coefficients=[((i*701)%22000)-11000 for i in range(count)]
                    packet=struct.pack('<4H',0x0e02,0,slot,count)+struct.pack(f'<{count}h',*coefficients)
                    packet+=bytes(-len(packet)%32)
                    source=0x4c100000
                    bus.command(f'write {source:#x} {len(packet)} 0x{packet.hex()}')
                    bus.put(0xf2000140,source,'l');bus.put(0xf2000144,0xfa040020,'l')
                    bus.put(0xf200014c,0xa4492000|(len(packet)//4),'l');bus.put(0xf2000150,0xc8c7,'l')
                    put(0x40,0x00021814);put(0x44,(len(packet)//2)<<16|18);put(0,1)
                    bus.command('clock_step 200000')
                    self.assertEqual(get(0x80),0x0e03100a,q.diagnostics());ack()
                self.assertIn('AMR_FILTER slot=0 half_taps=32 history=32 ratio=2:1',q.diagnostics())
                self.assertIn('AMR_FILTER slot=1 half_taps=48 history=32 ratio=3:1',q.diagnostics())
                send(20,0x0404,300)
                send(20,0x0804,5,1,1,1,0,0) # incomplete six-word transport record
                service(18,0x0e04,0)
                bus.command('clock_step 100000000')
                self.assertEqual(get(4),0)
                self.assertNotIn('AAC_PCM source=',q.diagnostics())
                self.assertNotIn('AAC_COMPLETE',q.diagnostics())
                service(18,0x0e04,1)
                send(20,0x0704);bus.command('clock_step 200000')
                self.assertEqual(get(0x80),0x0204180a)
                self.assertEqual(get(0x84)&0xffff,5);ack()
                send(18,0x0e04,4)
                self.assertEqual(get(0x80),0x0604100a)
                self.assertIn('AAC_END packets=0 decoded=0 submitted=0 nonzero=0',q.diagnostics())
            finally:
                bus.close()

    def test_captured_native_amr_frames_decode_through_two_filters_and_drain(self):
        import tempfile
        import wave
        from pathlib import Path
        fixtures = Path(__file__).with_name('fixtures')
        records = (fixtures / 'rock-star-guest-stream4.bin').read_bytes()
        filters = (fixtures / 'rock-star-guest-filters.bin').read_bytes()
        self.assertEqual(len(records), 14 * 46)
        for offset in range(0, len(records), 46):
            self.assertEqual(struct.unpack_from('<3H', records, offset), (1, 1, 1))
            self.assertEqual(struct.unpack_from('<H', records, offset + 10)[0], 17)
            self.assertEqual(records[offset + 13], 0x3c)  # native halfword byte order
        with tempfile.TemporaryDirectory() as directory:
            capture = Path(directory) / 'device.wav'
            with QemuBackend(bus_test=True, audio_capture_path=capture) as q:
                bus = Bus(q)
                q.command('cont')
                try:
                    def put(offset, value): bus.put(0xfa040000 + offset, value, 'l')
                    def get(offset): return bus.get(0xfa040000 + offset, 'l')
                    def send(channel, *words):
                        for i, value in enumerate([((len(words) + 1) << 11) | 10, *words, channel]):
                            bus.put(0xfa040040 + i * 2, value, 'w')
                        put(0, 1)
                        self.assertEqual(get(0) & 1, 0)
                    def ack():
                        put(4, 0xfffffffe)
                        bus.command('clock_step 200000')
                    def service(channel, *words):
                        send(channel, *words)
                        bus.command('clock_step 200000')
                        self.assertEqual(get(0x80) >> 16, words[0] + 1, q.diagnostics())
                        ack()
                    def transfer(packet, channel):
                        packet += bytes(-len(packet) % 32)
                        source = 0x4c100000
                        bus.command(f'write {source:#x} {len(packet)} 0x{packet.hex()}')
                        bus.put(0xf2000140, source, 'l'); bus.put(0xf2000144, 0xfa040020, 'l')
                        bus.put(0xf200014c, 0xa4492000 | (len(packet) // 4), 'l')
                        bus.put(0xf2000150, 0xc8c7, 'l')
                        put(0x40, 0x00021814); put(0x44, (len(packet) // 2) << 16 | channel)
                        put(0, 1)
                    words = {0xa9fe: 0xec31, 0xa9ff: 0xbe00, 0xaa00: 0xaeba,
                             0xaa14: 0xfb41, 0xaa15: 0x0202, 0xaf03: 2}
                    for pair in sorted({address & ~1 for address in words}):
                        put(0x28, pair)
                        put(0x2c, (words.get(pair, 0) << 16) | words.get(pair + 1, 0))
                        put(0x24, 1)
                    service(11, 0x0800, *([0] * 20), 48000)
                    service(12, 0x0904, 0, 1)
                    service(12, 0x090a, 0, 0x4000)
                    service(12, 0x0906, 0, 0, 1, 0)
                    service(12, 0x0908, 0, 0, 0x4000)
                    send(18, 0x0e04, 3)
                    ack(); self.assertEqual(get(0x80), 0x0e05180a); ack()
                    service(18, 0x0e00, 0,0,5,0,3,2,2,3,0,1,1,0,32,32,0,0,1,0xffff,2,
                            0,0xffff,3,1,0,0,0xcc03)
                    for packet in (filters[:72], filters[72:]):
                        transfer(packet, 18)
                        bus.command('clock_step 200000')
                        self.assertEqual(get(0x80), 0x0e03100a, q.diagnostics()); ack()
                    send(20, 0x0404, 300)
                    transfer(struct.pack('<HH', 0x0804, len(records) // 2) + records, 20)
                    service(18, 0x0e04, 0)
                    credits = 0
                    for _ in range(100):
                        bus.command('clock_step 10000000')
                        if get(4) & 1:
                            self.assertEqual(get(0x80) >> 16, 0x0504, q.diagnostics())
                            credits += get(0x84) & 0xffff
                            ack()
                    self.assertEqual(credits, len(records) // 2, q.diagnostics())
                    self.assertIn('AAC_PCM source=guest-stream4 rate=48000', q.diagnostics())
                    self.assertNotIn('AAC_COMPLETE', q.diagnostics())
                    self.assertNotIn('UNSUPPORTED', q.diagnostics())
                    # Exercise the established shared decoder terminal protocol;
                    # native AMR end-to-end provenance is verified separately.
                    send(20, 0x0804, 6, 2,1,1,0,0,0)
                    returned = completed = 0
                    for _ in range(100):
                        bus.command('clock_step 10000000')
                        if get(4) & 1:
                            opcode = get(0x80) >> 16
                            if opcode in (0x0504, 0x0204):
                                returned += get(0x84) & 0xffff
                            else:
                                self.assertEqual(opcode, 0x0e06, q.diagnostics())
                                self.assertEqual(get(0x84) & 0xffff, 2)
                                completed += 1
                            ack()
                    self.assertEqual((returned, completed), (6, 1), q.diagnostics())
                    self.assertEqual(q.diagnostics().count('AAC_COMPLETE'), 1)
                    send(18, 0x0e04, 4)
                    # Independent raw-converter output equals afconvert after
                    # its 40 leading zero samples (rock-star-sample-accounting.json); both FIR stages add 279 tail frames.
                    self.assertIn('AAC_END packets=14 decoded=2200 submitted=13479', q.diagnostics())
                    self.assertNotIn('DECODE_ERROR', q.diagnostics())
                finally:
                    bus.close()
            with wave.open(str(capture), 'rb') as wav:
                self.assertEqual((wav.getframerate(), wav.getnchannels(), wav.getsampwidth()), (44100, 2, 2))
                self.assertGreater(wav.getnframes(), 10000)
                data = wav.readframes(wav.getnframes())
                self.assertTrue(any(data))
                # A real mono speaker signal is represented equally on both
                # host sides, each with its own output filter history.
                samples = struct.unpack(f'<{len(data)//2}h', data)
                self.assertEqual(samples[::2], samples[1::2])
