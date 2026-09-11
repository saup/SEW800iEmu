"""DSP host-port transfers preserve guest halfwords and acknowledge real copies."""
import unittest
import struct
import tempfile
import re
import wave
from pathlib import Path
from w800.backend import QemuBackend, ROOT
from w800.tests.test_storage import Bus
from w800.tools.debug import Debugger


@unittest.skipUnless((ROOT/'build/qemu-system-arm').exists(), 'Local QEMU build required')
class DspTests(unittest.TestCase):
    def test_walkman_equalizer_stores_stages_accepts_nonflat_and_rejects_unstable(self):
        with QemuBackend(bus_test=True) as q:
            bus = Bus(q)
            q.command('cont')
            try:
                def put(offset, value): bus.put(0xfa040000 + offset, value, 'l')
                def get(offset): return bus.get(0xfa040000 + offset, 'l')
                def send(words):
                    for i, value in enumerate([((len(words)+1)<<11)|10, *words, 12]):
                        bus.put(0xfa040040 + i*2, value, 'w')
                    put(0, 1)
                    self.assertEqual(get(0) & 1, 0)
                    bus.command('clock_step 200000')
                words = {0xae41:0xec31, 0xae42:0xbe00, 0xae43:0xb2dc,
                         0xae57:0xfb41, 0xae58:0x0202, 0xb32a:2}
                for pair in sorted({address & ~1 for address in words}):
                    put(0x28,pair);put(0x2c,(words.get(pair,0)<<16)|words.get(pair+1,0));put(0x24,1)
                # Actual nine stages sent by native Walkman before Stop.mp3.
                poles = [(49795,0),(34411,14746),(34426,14746),(34485,14746),
                         (34722,14746),(35662,14746),(39326,14746),
                         (52531,14746),(15741,0)]
                for stage, (first, second) in enumerate(poles):
                    packet = [0x090e,0,1,9,stage,0,0x4000,first,second,first,second]
                    send(packet)
                    self.assertEqual(get(4) & 1, 1)
                    answer = [bus.get(0xfa040082+i*2,'w') for i in range(len(packet))]
                    self.assertEqual(answer, [0x090f,*packet[1:]])
                    put(4,0xfffffffe)
                self.assertIn('enabled=1 stages=9 stage=8 mask=1ff',q.diagnostics())
                # Disable has no initialized configuration tail in the
                # original builder. Ignore stale allocated tail words.
                send([0x090e,0,0,0xffff,0xffff,0xffff,0xffff])
                self.assertEqual(get(0x80) >> 16,0x090f)
                put(4,0xfffffffe)
                self.assertIn('enabled=0 stages=0 stage=0 mask=000',q.diagnostics())
                # Stable nonflat coefficients now install a real filter.
                send([0x090e,0,1,9,0,0,0x4100,49795,0,49795,0])
                self.assertEqual(get(0x80) >> 16,0x090f)
                put(4,0xfffffffe)
                # Unstable poles never get a false successful reply.
                send([0x090e,0,1,9,0,0,0x4000,0,0,0x8000,0x4000])
                self.assertEqual(get(4) & 1,0)
                self.assertIn('SERVICE_PENDING channel=12 opcode=090e',q.diagnostics())
            finally:
                bus.close()

    def test_guest_midi_fifo_renders_pauses_flushes_and_completes_at_real_eof(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = Path(directory) / 'device.wav'
            with QemuBackend(bus_test=True, audio_capture_path=capture) as q:
                bus = Bus(q)
                q.command('cont')
                try:
                    def put(offset, value): bus.put(0xfa040000 + offset, value, 'l')
                    def get(offset): return bus.get(0xfa040000 + offset, 'l')
                    def ack(): put(4, 0xfffffffe)
                    def send(words, channel):
                        for i, value in enumerate([((len(words)+1)<<11)|10, *words, channel]):
                            bus.put(0xfa040040 + i*2, value, 'w')
                        put(0, 1)
                        self.assertEqual(get(0) & 1, 0)
                    def service(words, channel):
                        send(words, channel)
                        bus.command('clock_step 200000')
                        self.assertEqual(get(4) & 1, 1)
                        count = (get(0x80) & 0xffff) >> 11
                        reply = [bus.get(0xfa040082+i*2, 'w') for i in range(count-1)]
                        self.assertEqual(reply[0], words[0]+1)
                        ack()
                        return reply
                    words = {0xae41:0xec31, 0xae42:0xbe00, 0xae43:0xb2dc,
                             0xae57:0xfb41, 0xae58:0x0202, 0xb32a:2}
                    for pair in sorted({address & ~1 for address in words}):
                        put(0x28,pair);put(0x2c,(words.get(pair,0)<<16)|words.get(pair+1,0));put(0x24,1)
                    service([0x0800, *([0]*20), 48000], 11)
                    for side in (0, 1):
                        service([0x0904, side, side+1], 12)
                        service([0x090a, side, 0x4000], 12)
                        service([0x0906, side, side, 1, 0], 12)
                        service([0x0908, side, side, 0x4000], 12)
                    service([0x0600, 2, 0, 4, 16, 0x7fff, 0, 1], 9)
                    send([0x0604], 9)
                    self.assertEqual(get(0x80), 0x0301180a)
                    ack();bus.command('clock_step 200000')
                    self.assertEqual(get(0x80), 0x0605100a)
                    ack()
                    send([0x0401, 750], 20)
                    # Native flush restores the words actually held by the
                    # consumer, with0201 credits and reset ring indices.
                    send([0x0801, 5, 0x90, 60, 90, 48000, 0], 20)
                    service([0x0608, 2], 9)
                    send([0x0701], 20)
                    bus.command('clock_step 200000')
                    self.assertEqual(get(0x80), 0x0201180a)
                    self.assertEqual(get(0x84) & 0xffff, 5)
                    ack()
                    self.assertIn('SYNTH_FLUSH discarded=5 returned=5', q.diagnostics())
                    # Cancellation while an EOF operation holds its credit
                    # must restore that capacity through the native flush.
                    send([0x0801, 5, 0, 0, 0, 0, 0], 20)
                    service([0x0608, 0], 9)
                    bus.command('clock_step 10000000')
                    self.assertIn('W800_DSP_SYNTH_EOF', q.diagnostics())
                    self.assertEqual(get(4) & 1, 0)
                    service([0x0608, 2], 9)
                    cancelled_frames = int(re.findall(r'PCM_ACTIVITY running=0 writer=1 frames=(\d+)', q.diagnostics())[-1])
                    send([0x0701], 20)
                    bus.command('clock_step 200000')
                    self.assertEqual(get(0x80), 0x0201180a)
                    self.assertEqual(get(0x84) & 0xffff, 5)
                    ack()
                    self.assertIn('SYNTH_FLUSH discarded=0 returned=5', q.diagnostics())
                    send([0x0801, 5, 0x90, 69, 100, 0, 0], 20)
                    service([0x0608, 0], 9)
                    send([0x060a], 9)
                    bus.command('clock_step 200000')
                    self.assertEqual(get(4) & 1, 0)  # completion waits for actual playback end
                    for _ in range(50):
                        bus.command('clock_step 10000000')
                        if get(4) & 1:
                            self.assertEqual(get(0x80), 0x0501180a)
                            self.assertEqual(get(0x84) & 0xffff, 5)
                            ack()
                    self.assertIn('W800_DSP_PCM source=guest-midi-stream1', q.diagnostics())
                    service([0x0608, 1], 9)
                    positions = lambda: re.findall(r'PCM_ACTIVITY running=0 writer=1 frames=(\d+)', q.diagnostics())
                    position = positions()[-1]
                    self.assertGreater(int(position), 1000)
                    bus.command('clock_step 500000000')
                    service([0x0608, 1], 9)
                    self.assertEqual(positions()[-1], position)
                    # The zero timestamp of the actual EOF marker does not
                    # overtake a future note-off or rewind the audio clock.
                    # Repeated markers consume real FIFO capacity/credits,
                    # while producing one eventual completion only.
                    send([0x0801, 15, 0x80, 69, 0, 48000, 0,
                          0, 0, 0, 0, 0, 0, 0, 0, 0, 0], 20)
                    bus.command('clock_step 200000000')
                    self.assertEqual(get(4) & 1, 0)
                    self.assertEqual(q.diagnostics().count('W800_DSP_SYNTH_EOF'), 1)
                    service([0x0608, 0], 9)
                    eof_credits = quiet_eof_credits = completions = 0
                    for tick in range(2000):
                        bus.command('clock_step 10000000')
                        if not get(4) & 1:
                            continue
                        opcode = get(0x80) >> 16
                        if opcode == 0x0501:
                            eof_credits += get(0x84) & 0xffff
                        elif opcode == 0x0201:
                            quiet_eof_credits += get(0x84) & 0xffff
                            eof_credits += get(0x84) & 0xffff
                        elif opcode == 0x060b:
                            self.assertGreater(tick, 25)
                            finish_frame = get(0x84)
                            self.assertGreater(finish_frame, 48000)
                            completions += 1
                        else:
                            self.fail(f'Unexpected natural EOF reply {opcode:04x}')
                        ack()
                        if completions:
                            break
                    self.assertEqual(eof_credits, 15)
                    self.assertEqual(quiet_eof_credits, 10)
                    self.assertEqual(completions, 1)
                    diagnostic = q.diagnostics()
                    self.assertIn('W800_DSP_SYNTH_TAIL_DRAINED', diagnostic)
                    self.assertRegex(diagnostic, r'SYNTH_COMPLETE frames=\d+ markers=2 pcm_submitted=\d+')
                    submitted = int(re.findall(r'SYNTH_COMPLETE frames=\d+ markers=2 pcm_submitted=(\d+)', diagnostic)[-1])
                    self.assertEqual(finish_frame, submitted - cancelled_frames)
                    send([0x060a], 9)
                    bus.command('clock_step 100000000')
                    self.assertEqual(get(4) & 1, 0)
                    send([0x0606], 9)
                    self.assertEqual(get(0x80), 0x0601100a)
                    ack();bus.command('clock_step 200000')
                    self.assertEqual(get(0x80), 0x0607100a)
                    ack()
                    service([0x0602], 9)
                    self.assertIn('midi_events=2', q.diagnostics())
                finally:
                    bus.close()
            with wave.open(str(capture), 'rb') as wav:
                self.assertEqual(wav.getnchannels(), 2)
                self.assertEqual(wav.getsampwidth(), 2)
                pcm = wav.readframes(wav.getnframes())
                self.assertGreater(len(pcm), 4000)
                self.assertTrue(any(pcm))

    def test_aac_version_uses_uploaded_unaligned_responder(self):
        self._assert_relocated_version(0x15ced, 0xb2a6, 0x15d19, 0xb2eb, 'AAC_RELOCATED_TEST')

    def test_mp3_version_uses_uploaded_responder_and_metadata(self):
        self._assert_relocated_version(0x158c4, 0xb0a6, 0x158f0, 0xb0eb, 'MP3_UPLOADED_TEST')

    def test_amr_version_uses_actual_metadata_and_channel_address(self):
        self._assert_relocated_version(0x153fc, 0xaeba, 0x15428, 0xaf03, 'AMR_UPLOADED_TEST')

    def test_recorder_version_uses_uploaded_encoder_metadata(self):
        self._assert_relocated_version(0x1595d, 0xb30c, 0x15989, 0xb37a,
                                       'RECORDER_UPLOADED_TEST')

    def _assert_relocated_version(self, pc, metadata_address, reply_pc, channel_address, metadata):
        with QemuBackend(bus_test=True) as q:
            bus = Bus(q)
            q.command('cont')
            try:
                def put(offset,value):bus.put(0xfa040000+offset,value,'l')
                def get(offset):return bus.get(0xfa040000+offset,'l')
                words = {channel_address:2}
                for address,data in [(pc,bytes.fromhex('ec31be00') + struct.pack('>H',metadata_address)),
                                     (reply_pc,bytes.fromhex('fb410202'))]:
                    for i,value in enumerate(data):
                        slot=(address+i)//2
                        words[slot]=words.get(slot,0)|(value<<(0 if (address+i)&1 else 8))
                words.update({metadata_address+i:ord(c) for i,c in enumerate(metadata+'\0')})
                for pair in sorted({address&~1 for address in words}):
                    put(0x28,pair);put(0x2c,(words.get(pair,0)<<16)|words.get(pair+1,0));put(0x24,1)
                source,dest,count=0x4c100000,0x4c101000,224
                request=struct.pack('<H',0x0201)+bytes(count-2)
                bus.command(f'write {source:#x} {count} 0x{request.hex()}')
                bus.put(0xf2000140,source,'l');bus.put(0xf2000144,0xfa040020,'l')
                bus.put(0xf200014c,0xa4492038,'l');bus.put(0xf2000150,0xc8c7,'l')
                put(0x40,0x00021814);put(0x44,0x00700002);put(0,1)
                bus.command('clock_step 200000')
                self.assertEqual(get(4)&1,1)
                dsp_address=(get(0x88)&0xffff)<<16|get(0x88)>>16
                put(4,0xfffffffe)
                bus.put(0xf2000140,0xfa040020,'l');bus.put(0xf2000144,dest,'l')
                bus.put(0xf200014c,0xa4492038,'l');bus.put(0xf2000150,0xc8c7,'l')
                put(0x18,dsp_address);put(0x1c,count//4);put(0x14,0x19)
                reply=bytes.fromhex(bus.command(f'read {dest:#x} {count}')[2:])
                expected=struct.pack('<H',0x0202)+(metadata+'\0').encode('utf-16le')
                self.assertEqual(reply[:len(expected)],expected)
            finally:bus.close()

    def test_version_hle_uses_uploaded_metadata_and_actual_dma(self):
        with QemuBackend(bus_test=True) as q:
            bus = Bus(q)
            q.command('cont')
            try:
                def put(offset, value): bus.put(0xfa040000 + offset, value, 'l')
                def get(offset): return bus.get(0xfa040000 + offset, 'l')
                words = {0xae41:0xec31, 0xae42:0xbe00, 0xae43:0xb2dc,
                         0xae57:0xfb41, 0xae58:0x0202, 0xb32a:2}
                version = 'TEST_UPLOADED_DSP_METADATA'
                words.update({0xb2dc+i:ord(c) for i,c in enumerate(version+'\0')})
                for pair in sorted({address & ~1 for address in words}):
                    put(0x28,pair);put(0x2c,(words.get(pair,0)<<16)|words.get(pair+1,0));put(0x24,1)
                source, destination, count = 0x4c100000, 0x4c101000, 224
                request = struct.pack('<H',0x0201) + bytes(count-2)
                bus.command(f'write {source:#x} {count} 0x{request.hex()}')
                bus.put(0xf2000140,source,'l');bus.put(0xf2000144,0xfa040020,'l')
                bus.put(0xf200014c,0xa4492038,'l');bus.put(0xf2000150,0xc8c7,'l')
                # DMA waits for ownership transfer of the original header.
                self.assertEqual(bus.get(0xf2000140,'l'),source)
                put(0x40,0x00021814);put(0x44,0x00700002);put(0,1)
                self.assertEqual(bus.get(0xf2000140,'l'),source+count)
                self.assertEqual(bus.get(0xf2000150,'l')&1,0)
                self.assertEqual(get(0)&1,0)
                self.assertEqual(bus.get(0xf2000004,'l'),4)
                self.assertEqual(bus.get(0xfb020300,'l') & (1<<17),1<<17)
                bus.put(0xf2000008,4,'l')
                self.assertEqual(bus.get(0xf2000004,'l'),0)
                self.assertEqual(get(0x10)&9,9)
                self.assertEqual(get(0x10),0)
                bus.command('clock_step 200000')
                self.assertEqual(get(4)&1,1)
                self.assertEqual(get(0x80),0x00023014)
                self.assertEqual(get(0x84),0x00700002)
                # Original ARM4490d028 supplies DSP block address and count.
                dsp_address = (get(0x88)&0xffff)<<16 | get(0x88)>>16
                put(4,0xfffffffe)
                self.assertEqual(get(4),0)
                bus.put(0xf2000140,0xfa040020,'l');bus.put(0xf2000144,destination,'l')
                bus.put(0xf200014c,0xa4492038,'l');bus.put(0xf2000150,0xc8c7,'l')
                put(0x18,dsp_address);put(0x1c,count//4);put(0x14,0x19)
                reply = bytes.fromhex(bus.command(f'read {destination:#x} {count}')[2:])
                expected=struct.pack('<H',0x0202)+(version+'\0').encode('utf-16le')
                self.assertEqual(reply[:len(expected)],expected)
                self.assertEqual(bus.get(0xf2000144,'l'),destination+count)
                self.assertEqual(get(0x10)&0x10,0x10)
                # Actual stream open creates capacity before acknowledging;
                # closing releases it. Unknown operations retain ownership.
                put(0x40,0x0300180a);put(0x44,0x00140065);put(0,1)
                self.assertEqual(get(0)&1,0)
                self.assertEqual(get(0x80),0x0300180a)
                self.assertEqual(get(0x84),0x00140065)
                put(4,0xfffffffe)
                put(0x40,0x0600100a);put(0x44,0x00000014);put(0,1)
                self.assertEqual(get(0)&1,0)
                self.assertEqual(get(0x80),0x0600100a)
                put(4,0xfffffffe)
                acoustic = struct.pack('<HHHHH',0x090c,0,1,256,0x80)
                acoustic += struct.pack('<256H',*range(256)) + bytes(22)
                self.assertEqual(len(acoustic),544)
                bus.command(f'write {source:#x} {len(acoustic)} 0x{acoustic.hex()}')
                bus.put(0xf2000140,source,'l');bus.put(0xf2000144,0xfa040020,'l')
                bus.put(0xf200014c,0xa4492088,'l');bus.put(0xf2000150,0xc8c7,'l')
                put(0x40,0x00021814);put(0x44,0x0110000c);put(0,1)
                self.assertEqual(bus.get(0xf2000140,'l'),source+544)
                self.assertEqual(get(0)&1,0)
                bus.command('clock_step 200000')
                self.assertEqual(get(0x84),0x0110000c)
                dsp_address = (get(0x88)&0xffff)<<16 | get(0x88)>>16
                put(4,0xfffffffe)
                bus.put(0xf2000140,0xfa040020,'l');bus.put(0xf2000144,destination,'l')
                bus.put(0xf200014c,0xa4492088,'l');bus.put(0xf2000150,0xc8c7,'l')
                put(0x18,dsp_address);put(0x1c,136);put(0x14,0x19)
                reply = bytes.fromhex(bus.command(f'read {destination:#x} 544')[2:])
                self.assertEqual(reply,struct.pack('<H',0x090d)+acoustic[2:])
                self.assertIn('target=0 selector=1 words=258 first=0100 last=00ff',q.diagnostics())
                # Selector0 releases the actual table; the 6-byte result fits
                # a native short mailbox (no padded DMA allocation required).
                put(0x40,0x090c200a);put(0x44,0);put(0x48,12);put(0,1)
                bus.command('clock_step 200000')
                self.assertEqual(get(0x80),0x090d200a)
                self.assertEqual(get(0x84),0)
                self.assertEqual(get(0x88)&0xffff,12)
                put(4,0xfffffffe)
                self.assertIn('target=0 selector=0 words=0',q.diagnostics())
                # Exact output settings are stored before0801. Varying the
                # final word demonstrates sample rate comes from the packet.
                output = struct.pack('<22H',0x0800,*range(20),16000)
                for i in range(22):
                    bus.put(0xfa040042+i*2,struct.unpack_from('<H',output,i*2)[0],'w')
                bus.put(0xfa04006e,11,'w');bus.put(0xfa040040,0xb80a,'w');put(0,1)
                bus.command('clock_step 200000')
                self.assertEqual(get(0x80),0x0801b80a)
                self.assertEqual(bus.get(0xfa0400ac,'w'),16000)
                self.assertIn('words=21 rate=16000 reply=0801',q.diagnostics())
                put(4,0xfffffffe)
                def short_service(words, response_words=None, channel=12):
                    for i,value in enumerate([((len(words)+1)<<11)|10,*words,channel]):
                        bus.put(0xfa040040+i*2,value,'w')
                    put(0,1)
                    self.assertEqual(get(0)&1,0)
                    bus.command('clock_step 200000')
                    self.assertEqual(get(4)&1,1)
                    n = response_words or len(words)
                    answer=[bus.get(0xfa040082+i*2,'w') for i in range(n)]
                    put(4,0xfffffffe)
                    return answer
                # Occupancy follows actual assignment, and separate input
                # and output banks cannot alias. Query garbage is ignored.
                self.assertEqual(short_service([0x0904,1,2]),[0x0905,1,2])
                status=short_service([0x0914,1]+[0xdead]*20)
                self.assertEqual(status[-1],1)
                self.assertEqual(status[2:-1],[0]*19)
                self.assertEqual(short_service([0x0912,1]+[0xdead]*15)[-1],0)
                short_service([0x0904,1,0])
                self.assertEqual(short_service([0x0914,1]+[0xdead]*20)[-1],0)
                synth=[0x0600,2,0,13,16,0x7fff,0,1]
                self.assertEqual(short_service(synth,channel=9),[0x0601,*synth[1:]])
                self.assertIn('W800_DSP_SYNTH_INIT voices=13',q.diagnostics())
                put(0x40,0x0604100a);put(0x44,9);put(0,1)
                self.assertEqual(get(0x80),0x0301180a)
                self.assertEqual(get(0x84),0x00140400)
                put(4,0xfffffffe);bus.command('clock_step 200000')
                self.assertEqual(get(0x80),0x0605100a)
                put(4,0xfffffffe)
                put(0x40,0x0401180a);put(0x44,0x001402ee);put(0,1)
                self.assertEqual(get(0)&1,0)
                self.assertEqual(get(4),0)  # writer-start is not an echoed read-start
                events=[0x0801,5,0x90,60,90,480,0]
                for i,value in enumerate([0x400a,*events,20]):
                    bus.put(0xfa040040+i*2,value,'w')
                put(0,1)
                self.assertEqual(get(0)&1,0)
                self.assertEqual(get(4),0)  # no fabricated consumption credits
                self.assertIn('SYNTH_EVENTS words=5 queued=5 first=0090,003c,005a',q.diagnostics())
                put(0x40,0x0606100a);put(0x44,9);put(0,1)
                self.assertEqual(get(0x80),0x0601100a)
                put(4,0xfffffffe);bus.command('clock_step 200000')
                self.assertEqual(get(0x80),0x0607100a)
                put(4,0xfffffffe)
                self.assertEqual(short_service([0x0602],channel=9),[0x0603])
                self.assertIn('W800_DSP_SYNTH_RELEASE',q.diagnostics())
                # Addressed instrument/data uploads use the original type3
                # header. Their completion represents actual DSP RAM writes.
                upload=struct.pack('<4H',0x81fe,0x0123,0x4567,0xabcd)
                bus.command(f'write {source:#x} 8 0x{upload.hex()}')
                bus.put(0xf2000140,source,'l');bus.put(0xf2000144,0xfa040020,'l')
                bus.put(0xf200014c,0xa4492002,'l');bus.put(0xf2000150,0xc8c7,'l')
                put(0x40,0x00032014);put(0x44,0x00200009);put(0x48,0x40);put(0,1)
                self.assertEqual(bus.get(0xf2000140,'l'),source+8)
                self.assertEqual(get(0)&1,0)
                self.assertEqual(get(4),0)
                put(0x28,0x200040);put(0x24,2)
                self.assertEqual(get(0x2c),0x81fe0123)
                put(0x28,0x200042);put(0x24,2)
                self.assertEqual(get(0x2c),0x4567abcd)
                # Codec-kernel replacement can relocate the version code.
                # The already established device protocol must still accept
                # real mixer state changes; it does not invent a new version.
                put(0x28,0xae40);put(0x2c,0);put(0x24,1)
                self.assertEqual(short_service([0x0900,2]),[0x0901,2])
                put(0x40,0x1101380a);put(0x44,0x00000004)
                put(0x4c,0x00160000);put(0,1)
                self.assertEqual(get(0)&1,0)
                self.assertEqual(get(4),0)  # physical copy is not a service response
                for _ in range(15):
                    put(0,1)
                    self.assertEqual(get(0)&1,0)
                put(0,1)
                self.assertEqual(get(0)&1,1)  # real bounded queue backpressure
            finally:
                bus.close()

    def test_original_mailbox_halfwords_remain_pending_without_dsp(self):
        with QemuBackend(bus_test=True) as q:
            bus = Bus(q)
            try:
                # ITCM79c writes halfwords into the 64-byte host TX bank.
                # Distinct values detect aliasing between this bank, RX and
                # the command registers, including the final halfword.
                for i in range(32):
                    bus.put(0xfa040040 + i * 2, 0x8100 + i, 'w')
                for i in range(32):
                    self.assertEqual(bus.get(0xfa040040 + i * 2, 'w'), 0x8100 + i)
                    self.assertEqual(bus.get(0xfa040080 + i * 2, 'w'), 0)
                self.assertEqual(bus.get(0xfa040040, 'l'), 0x81018100)
                self.assertEqual(bus.get(0xfa04007e, 'b'), 0x1f)
                self.assertEqual(bus.get(0xfa04007f, 'b'), 0x81)
                bus.put(0xfa040000, 1, 'l')  # original ITCM800 commit
                for _ in range(4):
                    self.assertEqual(bus.get(0xfa040000, 'l') & 1, 1)
                    self.assertEqual(bus.get(0xfa040004, 'l') & 1, 0)
                    self.assertEqual(bus.get(0xfa040010, 'l'), 0)
                self.assertEqual(bus.get(0xfa04007c, 'l'), 0x811f811e)
                bus.put(0xfa040004, 0xfffffffe, 'l')  # original ITCM818 ACK
                self.assertEqual(bus.get(0xfa040004, 'l'), 0)
            finally:
                bus.close()

    def test_host_memory_port_pair_order_alias_and_boundaries(self):
        with QemuBackend(bus_test=True) as q:
            bus = Bus(q)
            try:
                def put(offset, value): bus.put(0xfa040000+offset,value,'l')
                def get(offset): return bus.get(0xfa040000+offset,'l')
                for address,value in [(0x8001,0xec318e00),(0x8003,0x800eec31),(0x7fffff,0xabcd0123)]:
                    put(0x28,address);put(0x2c,value);put(0x24,1)
                    self.assertEqual(get(0x24),0)
                for address,value in [(0x8000,0xec318e00),(0x8002,0x800eec31),(0x7ffffe,0xabcd0123),(0x8004,0)]:
                    put(0x28,address);put(0x24,2)
                    self.assertEqual(get(0x24),0)
                    self.assertEqual(get(0x2c),value)
                put(0x24,3)  # unknown commands must not falsely complete
                self.assertEqual(get(0x24),3)
            finally: bus.close()

    @unittest.skipUnless((ROOT/'reports/gdfs-candidate.bin').exists(), 'Prepared GDFS candidate required')
    def test_original_first_dsp_download_returns(self):
        with QemuBackend(exploratory=True, debug=True, mmio_limit=1000000,
                         flash_path=ROOT/'reports/gdfs-candidate.bin') as q:
            debug=Debugger(q)
            try:
                debug.breakpoint(0x449ff6f4,thumb=True)
                debug.run()
                registers=debug.registers()
                self.assertEqual(registers[0],0x8000)
                self.assertEqual(registers[1],17)
                self.assertEqual(debug.read(registers[2],4),bytes.fromhex('31ec008e'))
                debug.breakpoint(0x449ff6f4,False,thumb=True)
                debug.breakpoint(0x449ff754,thumb=True)
                debug.run()
                self.assertEqual(debug.registers()[15],0x449ff754)
            finally: debug.close()
