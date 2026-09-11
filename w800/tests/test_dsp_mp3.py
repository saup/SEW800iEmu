"""MP3 decoder configuration is a real device resource, not playback completion."""
import sys
import unittest
import struct
import tempfile
import wave
from pathlib import Path

from w800.backend import QemuBackend, ROOT
from w800.tests.test_storage import Bus


@unittest.skipUnless(sys.platform == 'darwin' and (ROOT / 'build/qemu-system-arm').exists(),
                     'Local macOS QEMU decoder required')
class MP3ResourceTests(unittest.TestCase):
    def test_actual_guest_chunks_cross_mpeg_frames_and_preserve_partial_record_credits(self):
        self._verify_actual_guest_chunks('latin',
            [0x0e00,7,1,2,1,2,0,0,0,0,0,0,0,0,0,0,0xffff,0xffff,0xffff,0,0,1,0,0xcc02])

    def test_walkman_mpeg2_chunks_filter_and_real_eof(self):
        self._verify_actual_guest_chunks('stop',
            [0x0e00,4,1,2,1,2,1,2,0,0,1,0,0,32,0,0,0,0xffff,0xffff,1,0,1,0,0xcc02],
            [-140,-350,-290,37,269,22,-303,-131,340,274,-332,-458,264,657,-111,-856,
             -139,1022,506,-1119,-999,1100,1641,-898,-2476,400,3637,675,-5612,-3470,11642,27340])

    def test_mpeg_odd_terminal_zero_padding_returns_actual_record_credits(self):
        self._verify_actual_guest_chunks('stop',
            [0x0e00,4,1,2,1,2,1,2,0,0,1,0,0,32,0,0,0,0xffff,0xffff,1,0,1,0,0xcc02],
            [-140,-350,-290,37,269,22,-303,-131,340,274,-332,-458,264,657,-111,-856,
             -139,1022,506,-1119,-999,1100,1641,-898,-2476,400,3637,675,-5612,-3470,11642,27340],
            terminal_frames=3)

    def test_mpeg_terminal_nonzero_byte_is_retained_until_original_flush(self):
        self._verify_actual_guest_chunks('stop',
            [0x0e00,4,1,2,1,2,1,2,0,0,1,0,0,32,0,0,0,0xffff,0xffff,1,0,1,0,0xcc02],
            [-140,-350,-290,37,269,22,-303,-131,340,274,-332,-458,264,657,-111,-856,
             -139,1022,506,-1119,-999,1100,1641,-898,-2476,400,3637,675,-5612,-3470,11642,27340],
            terminal_frames=3,padding_byte=0x7e)

    def _verify_actual_guest_chunks(self, asset, configuration, coefficients=None,
                                   terminal_frames=2,padding_byte=0):
        fixture = Path(__file__).with_name('fixtures') / f'{asset}-guest-stream4.bin'
        records = fixture.read_bytes()
        self.assertEqual(len(records), 3 * 512)
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
                    def acknowledge():
                        put(4, 0xfffffffe)
                        bus.command('clock_step 200000')
                    def service(channel, *words):
                        send(channel, *words)
                        bus.command('clock_step 200000')
                        self.assertEqual(get(0x80) >> 16, words[0] + 1, q.diagnostics())
                        acknowledge()
                    def transfer(packet, channel):
                        packet += bytes(-len(packet) % 32)
                        source = 0x4c100000
                        bus.command(f'write {source:#x} {len(packet)} 0x{packet.hex()}')
                        bus.put(0xf2000140, source, 'l')
                        bus.put(0xf2000144, 0xfa040020, 'l')
                        bus.put(0xf200014c, 0xa4492000 | (len(packet) // 4), 'l')
                        bus.put(0xf2000150, 0xc8c7, 'l')
                        put(0x40, 0x00021814)
                        put(0x44, (len(packet) // 2) << 16 | channel)
                        put(0, 1)
                        self.assertEqual(get(4), 0)
                    def record(payload):
                        transfer(struct.pack('<HH', 0x0804, len(payload) // 2) + payload, 20)

                    words = {0xac62: 0xec31, 0xac63: 0xbe00, 0xac64: 0xb0a6,
                             0xac78: 0xfb41, 0xac79: 0x0202, 0xb0eb: 2}
                    for pair in sorted({address & ~1 for address in words}):
                        put(0x28, pair)
                        put(0x2c, (words.get(pair, 0) << 16) | words.get(pair + 1, 0))
                        put(0x24, 1)
                    service(11, 0x0800, *([0] * 20), 44100)
                    for side in (0, 1):
                        service(12, 0x0904, side, side + 1)
                        service(12, 0x090a, side, 0x4000)
                        service(12, 0x0906, side, side, 1, 0)
                        service(12, 0x0908, side, side, 0x4000)
                    send(18, 0x0e04, 3)
                    acknowledge()
                    self.assertEqual(get(0x80), 0x0e05180a)
                    acknowledge()
                    service(18, *configuration)
                    if coefficients:
                        transfer(struct.pack('<4H32h',0x0e02,0,0,32,*coefficients),18)
                        bus.command('clock_step 200000')
                        self.assertEqual(get(0x80),0x0e03100a)
                        acknowledge()
                        self.assertIn('half_taps=32 history=32 ratio=2:1',q.diagnostics())
                    send(20, 0x0404, 306)
                    for offset in range(0, len(records), 512):
                        # The real native records contain500 compressed bytes
                        # each, with209-byte MPEG frames crossing their edges.
                        record(records[offset:offset+512])
                    service(18, 0x0e04, 0)
                    credits = 0
                    for _ in range(100):
                        bus.command('clock_step 10000000')
                        if get(4) & 1:
                            self.assertEqual(get(0x80) >> 16, 0x0504, q.diagnostics())
                            credits += get(0x84) & 0xffff
                            acknowledge()
                    self.assertEqual(credits, 512)
                    self.assertIn('AAC_PCM source=guest-stream4 rate=44100', q.diagnostics())
                    self.assertNotIn('UNSUPPORTED', q.diagnostics())
                    self.assertNotIn('AAC_COMPLETE', q.diagnostics())
                    service(18, 0x0e04, 1)
                    send(20, 0x0704)
                    bus.command('clock_step 200000')
                    self.assertEqual(get(0x80), 0x0204180a)
                    self.assertEqual(get(0x84) & 0xffff, 256)
                    acknowledge()
                    # Real209-byte MPEG frames cross the native records.
                    # Three frames are odd in bytes and require the original
                    # producer's one zero alignment byte, confirmed against
                    # the complete immutable Stop asset and final record.
                    native_data=b''.join(records[i+12:i+512] for i in range(0,len(records),512))
                    data=b''.join(native_data[i:i+2][::-1] for i in range(0,len(native_data),2))
                    data=data[:209*terminal_frames]
                    if len(data)%2:data+=bytes([padding_byte])
                    wire=b''.join(data[i:i+2][::-1] for i in range(0,len(data),2))
                    terminal_words=len(wire)//2
                    record(struct.pack('<6H',1,1,1,0,0,terminal_words)+wire)
                    service(18, 0x0e04, 0)
                    if terminal_frames%2:
                        bus.command('clock_step 200000000')
                        self.assertEqual(get(4)&1,0)
                        self.assertNotIn('MP3_ALIGNMENT',q.diagnostics())
                        self.assertNotIn('AAC_EOF',q.diagnostics())
                    send(20, 0x0804, 6, 2,0,1,0,0,0)
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
                            acknowledge()
                    if padding_byte:
                        self.assertEqual(returned,0)
                        self.assertEqual(completed,0)
                        self.assertNotIn('MP3_ALIGNMENT',q.diagnostics())
                        self.assertNotIn('AAC_EOF',q.diagnostics())
                        service(18,0x0e04,1)
                        send(20,0x0704)
                        bus.command('clock_step 200000')
                        self.assertEqual(get(0x80),0x0204180a)
                        self.assertEqual(get(0x84)&0xffff,terminal_words+12)
                        acknowledge()
                    else:
                        self.assertEqual(returned,terminal_words+12)
                        self.assertEqual(completed,1)
                        self.assertEqual(q.diagnostics().count('AAC_COMPLETE'),1)
                        self.assertEqual(q.diagnostics().count('MP3_ALIGNMENT'),terminal_frames%2)
                    send(18, 0x0e04, 4)
                    self.assertIn(f'AAC_END packets={7+terminal_frames} ',q.diagnostics())
                finally:
                    bus.close()
            with wave.open(str(capture), 'rb') as wav:
                self.assertEqual((wav.getframerate(), wav.getnchannels(), wav.getsampwidth()), (44100, 2, 2))
                self.assertGreater(wav.getnframes(), 1000)
                self.assertTrue(any(wav.readframes(wav.getnframes())))

    def test_original_latin_configuration_and_unsupported_input_ownership(self):
        with QemuBackend(bus_test=True) as q:
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
                def acknowledge():
                    put(4, 0xfffffffe)
                    bus.command('clock_step 200000')

                words = {0xac62: 0xec31, 0xac63: 0xbe00, 0xac64: 0xb0a6,
                         0xac78: 0xfb41, 0xac79: 0x0202, 0xb0eb: 2}
                for pair in sorted({address & ~1 for address in words}):
                    put(0x28, pair)
                    put(0x2c, (words.get(pair, 0) << 16) | words.get(pair + 1, 0))
                    put(0x24, 1)
                send(18, 0x0e04, 3)
                self.assertEqual(get(0x80), 0x0304180a)
                acknowledge()
                self.assertEqual(get(0x80), 0x0e05180a)
                acknowledge()
                send(18, 0x0e00,7,1,2,1,2,0,0,0,0,0,0,0,0,0,0,
                     0xffff,0xffff,0xffff,0,0,1,0,0xcc02)
                bus.command('clock_step 200000')
                self.assertEqual(get(0x80), 0x0e01280a, q.diagnostics())
                self.assertEqual(get(0x84) & 0xffff, 1)
                self.assertIn('AAC_CONFIG codec=MP3 rate=44100 channels=2', q.diagnostics())
                acknowledge()
                send(20, 0x0404, 306)
                send(18, 0x0e04, 0)
                bus.command('clock_step 200000')
                self.assertEqual(get(0x80), 0x0e05180a)
                acknowledge()
                # Copied malformed MPEG input must not be decoded, consumed,
                # credited, or treated as a successful playback completion.
                send(20, 0x0804, 11, 1,1,1,0,0,5, 0x1234,0x5678,0xabcd,0xef01,0)
                bus.command('clock_step 100000000')
                self.assertEqual(get(4), 0)
                self.assertIn('MP3_UNSUPPORTED_FRAME prefix=12345678', q.diagnostics())
                self.assertNotIn('AAC_PCM source=', q.diagnostics())
                self.assertNotIn('AAC_COMPLETE', q.diagnostics())
                send(18, 0x0e04, 1)
                bus.command('clock_step 200000')
                acknowledge()
                send(20, 0x0704)
                bus.command('clock_step 200000')
                self.assertEqual(get(0x80), 0x0204180a)
                self.assertEqual(get(0x84) & 0xffff, 11)
                acknowledge()
                send(20, 0x0104)
                bus.command('clock_step 200000')
                self.assertEqual(get(0x80), 0x0104100a)
                acknowledge()
                send(18, 0x0e04, 4)
                self.assertEqual(get(0x80), 0x0604100a)
                self.assertIn('AAC_END packets=0 decoded=0 submitted=0 nonzero=0', q.diagnostics())
            finally:
                bus.close()
