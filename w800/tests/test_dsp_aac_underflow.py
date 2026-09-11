"""Actual AAC starvation keeps its decoder and resumes through native control."""
import json
from pathlib import Path
import re
import struct
import sys
import tempfile
import unittest
import wave

from w800.backend import QemuBackend, ROOT
from w800.tests.test_storage import Bus


@unittest.skipUnless(sys.platform == 'darwin' and (ROOT / 'build/qemu-system-arm').exists(),
                     'Local macOS QEMU decoder required')
class AACUnderflowTests(unittest.TestCase):
    def test_real_starvation_preserves_credits_and_resumes_to_actual_eof(self):
        self.exercise_starvation()

    def test_future_presentation_survives_microsecond_clock_wrap(self):
        self.exercise_starvation(first_presentation_us=0x100000000 + 100000)

    def exercise_starvation(self, first_presentation_us=None):
        fixture = json.loads((Path(__file__).with_name('fixtures') /
                              'demotour-guest-stream4.json').read_text())
        packets = [bytes.fromhex(value) for value in fixture['packets']]
        self.assertEqual(len(packets), 10)
        original_high, original_low = struct.unpack_from('<HH', packets[0], 10)
        captured_presentation = (original_high << 16) | original_low
        if first_presentation_us is None:
            first_presentation_us = captured_presentation
        else:
            # Preserve the actual compressed units and64-ms source spacing;
            # rebase only their transport timestamps around the32-bit wrap.
            for index, packet in enumerate(packets):
                packet = bytearray(packet)
                high, low = struct.unpack_from('<HH', packet, 10)
                stamp = (first_presentation_us + ((high << 16) | low) -
                         captured_presentation) & 0xffffffff
                struct.pack_into('<HH', packet, 10, stamp >> 16, stamp & 0xffff)
                packets[index] = bytes(packet)
        with tempfile.TemporaryDirectory() as directory:
            capture = Path(directory) / 'aac.wav'
            with QemuBackend(bus_test=True, audio_capture_path=capture) as q:
                bus = Bus(q)
                q.command('cont')
                try:
                    def put(offset, value): bus.put(0xfa040000 + offset, value, 'l')
                    def get(offset): return bus.get(0xfa040000 + offset, 'l')
                    def step(): bus.command('clock_step 10000000')
                    def acknowledge():
                        put(4, 0xfffffffe)
                        bus.command('clock_step 200000')
                    def send(channel, *words):
                        for i, value in enumerate([((len(words) + 1) << 11) | 10,
                                                   *words, channel]):
                            bus.put(0xfa040040 + i * 2, value, 'w')
                        put(0, 1)
                        self.assertEqual(get(0) & 1, 0)
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
                        self.assertEqual(get(0) & 1, 0)

                    kernel = {0xae41: 0xec31, 0xae42: 0xbe00, 0xae43: 0xb2dc,
                              0xae57: 0xfb41, 0xae58: 0x0202, 0xb32a: 2}
                    for pair in sorted({address & ~1 for address in kernel}):
                        put(0x28, pair)
                        put(0x2c, (kernel.get(pair, 0) << 16) | kernel.get(pair + 1, 0))
                        put(0x24, 1)
                    service(11, 0x0800, *([0] * 20), 48000)
                    for side in (0, 1):
                        service(12, 0x0904, side, side + 1)
                        service(12, 0x090a, side, 0x4000)
                        service(12, 0x0906, side, side, 1, 0)
                        service(12, 0x0908, side, side, 0x4000)
                    send(18, 0x0e04, 3)
                    acknowledge()
                    self.assertEqual(get(0x80), 0x0e05180a)
                    acknowledge()
                    service(18, 0x0e00, 3,1,1,0,3,1,3,0,0,1,0,0,32,
                            0,0,0,0xffff,0xffff,1,0,1,0,0xcc01)
                    transfer(struct.pack('<4H48h', 0x0e02,0,0,48,
                                         *fixture['filter_coefficients']), 18)
                    bus.command('clock_step 200000')
                    self.assertEqual(get(0x80), 0x0e03100a)
                    acknowledge()
                    send(20, 0x0404, 306)

                    # Starting an empty but never-fed decoder is not a
                    # completed starvation episode and must not notify.
                    service(18, 0x0e04, 0)
                    for _ in range(20): step()
                    self.assertEqual(get(4) & 1, 0)
                    self.assertNotIn('AAC_UNDERRUN', q.diagnostics())
                    now_ns = q.command('qom-get', {
                        'path': '/machine', 'property': 'virtual-time-ns'})
                    before_presentation = (first_presentation_us - 200000) * 1000
                    self.assertGreater(before_presentation, now_ns)
                    bus.command(f'clock_step {before_presentation - now_ns}')
                    for packet in packets[:8]: transfer(packet, 20)
                    # The reader owns complete, valid input200ms ahead of
                    # its actual device presentation clock. Host capacity
                    # alone must not consume it or manufacture starvation.
                    for _ in range(15): step()
                    self.assertEqual(get(4) & 1, 0, q.diagnostics())
                    self.assertNotIn('AAC_PCM', q.diagnostics())
                    self.assertNotIn('AAC_UNDERRUN', q.diagnostics())
                    for _ in range(100):
                        step()
                        if get(4) & 1: break
                    self.assertEqual(get(0x80) >> 16, 0x0504, q.diagnostics())
                    first_credit = get(0x84) & 0xffff
                    for _ in range(30): step()
                    self.assertEqual(get(0x80) >> 16, 0x0504)
                    self.assertEqual(get(0x84) & 0xffff, first_credit)
                    self.assertNotIn('AAC_UNDERRUN', q.diagnostics())

                    credits = 0
                    for _ in range(200):
                        if get(4) & 1:
                            opcode = get(0x80) >> 16
                            if opcode == 0x0e06: break
                            self.assertEqual(opcode, 0x0504, q.diagnostics())
                            credits += get(0x84) & 0xffff
                            acknowledge()
                        step()
                    self.assertEqual(get(0x80) >> 16, 0x0e06, q.diagnostics())
                    self.assertEqual(get(0x84) & 0xffff, 1)
                    acknowledge()
                    for _ in range(50):
                        step()
                        if get(4) & 1:
                            self.assertEqual(get(0x80) >> 16, 0x0504, q.diagnostics())
                            credits += get(0x84) & 0xffff
                            acknowledge()
                    self.assertEqual(credits, sum(len(p) // 2 - 2 for p in packets[:8]))
                    self.assertEqual(get(4) & 1, 0)
                    self.assertEqual(q.diagnostics().count('AAC_UNDERRUN'), 1)
                    self.assertNotIn('AAC_COMPLETE', q.diagnostics())

                    # One actual unit exhausts compressed input while its
                    # decoded PCM is still pending. State must be reported
                    # before final credits can wake the native reader.
                    transfer(packets[8], 20)
                    service(18, 0x0e04, 0)
                    credits = 0
                    for _ in range(200):
                        step()
                        if get(4) & 1:
                            opcode = get(0x80) >> 16
                            if opcode == 0x0e06: break
                            self.assertEqual(opcode, 0x0504, q.diagnostics())
                            credits += get(0x84) & 0xffff
                            acknowledge()
                    self.assertEqual(credits, 0)
                    self.assertEqual(get(0x80) >> 16, 0x0e06, q.diagnostics())
                    self.assertEqual(get(0x84) & 0xffff, 1)
                    starvation = re.findall(
                        r'AAC_UNDERRUN packets=9 decoded=9216 submitted=(\d+) pending_pcm=(\d+) credits=(\d+)',
                        q.diagnostics())
                    self.assertEqual(len(starvation), 1)
                    submitted_at_status, pending_pcm, held_words = map(int, starvation[0])
                    self.assertGreater(pending_pcm, 0)
                    self.assertEqual(submitted_at_status * 4 + pending_pcm, 9 * 1024 * 3 * 4)
                    self.assertEqual(held_words, len(packets[8]) // 2 - 2)
                    acknowledge()

                    # Queuing more input must not restart decoding or
                    # complete EOF until native selector0 resumes it.
                    transfer(packets[9], 20)
                    terminal = bytes.fromhex(fixture['terminal_packet'])
                    transfer(terminal, 20)
                    for _ in range(50):
                        step()
                        if get(4) & 1:
                            self.assertEqual(get(0x80) >> 16, 0x0504, q.diagnostics())
                            credits += get(0x84) & 0xffff
                            acknowledge()
                    self.assertEqual(credits, held_words)
                    self.assertEqual(get(4) & 1, 0)
                    self.assertEqual(q.diagnostics().count('AAC_UNDERRUN'), 2)
                    self.assertNotIn('AAC_COMPLETE', q.diagnostics())
                    service(18, 0x0e04, 0)
                    self.assertIn('AAC_ACTIVITY operation=0 running=1 submitted=27648', q.diagnostics())
                    returned = completed = 0
                    for _ in range(200):
                        step()
                        if get(4) & 1:
                            opcode = get(0x80) >> 16
                            if opcode in (0x0504, 0x0204):
                                returned += get(0x84) & 0xffff
                            else:
                                self.assertEqual(opcode, 0x0e06, q.diagnostics())
                                self.assertEqual(get(0x84) & 0xffff, 2)
                                completed += 1
                            acknowledge()
                    self.assertEqual(returned, len(packets[9]) // 2 - 2 + 6)
                    self.assertEqual(completed, 1)
                    self.assertEqual(q.diagnostics().count('AAC_UNDERRUN'), 2)
                    self.assertEqual(q.diagnostics().count('AAC_CONFIG codec=AAC-LC'), 1)
                    self.assertEqual(q.diagnostics().count('AAC_COMPLETE'), 1)
                    self.assertNotIn('DECODE_ERROR', q.diagnostics())
                    send(18, 0x0e04, 4)
                    self.assertIn('AAC_END packets=10 decoded=10240', q.diagnostics())
                    submitted = int(re.findall(
                        r'AAC_END packets=10 decoded=10240 submitted=(\d+)',
                        q.diagnostics())[-1])
                    self.assertEqual(submitted, 10 * 1024 * 3 + 31 * 3)
                finally:
                    bus.close()
            with wave.open(str(capture)) as wav:
                # WAV fwrite is buffered: live file-size growth cannot show
                # a pending tail smaller than its stdio block. Check every
                # submitted frame after close, including both starvation
                # episodes and the genuine 31-frame FIR tail. The backend's
                # rational 48k→44.1k conversion rounds at a frame boundary.
                self.assertEqual((wav.getnchannels(), wav.getsampwidth()), (2, 2))
                self.assertLessEqual(abs(wav.getnframes() -
                    submitted * wav.getframerate() / 48000), 1)
                self.assertTrue(any(wav.readframes(wav.getnframes())))
