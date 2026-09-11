"""Exercise actual patched IOProc code with finite PCM and host buffer layouts."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from w800.backend import ROOT


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('cc'), 'macOS C toolchain required')
class CoreAudioBufferTests(unittest.TestCase):
    def test_actual_ioproc_partial_wrapped_and_split_channel_output(self):
        source = (ROOT / 'reference/qemu-10.2.2/audio/coreaudio.m').read_text()
        a = source.index('/* CoreAudio PCM layout helpers:')
        b = source.index('/* End CoreAudio PCM layout helpers. */', a)
        helpers = source[a:b]
        a = source.index('static OSStatus audioDeviceIOProc(')
        b = source.index('static OSStatus init_out_device(', a)
        callback = source[a:b]
        audio = (ROOT / 'reference/qemu-10.2.2/audio/audio_int.h').read_text()
        a = audio.index('static inline size_t audio_ring_posb(')
        b = audio.index('\n}\n', a) + 3
        ring = audio[a:b]
        harness = r'''
#include <CoreAudio/CoreAudio.h>
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define MIN(a,b) ((a)<(b)?(a):(b))
typedef struct HWVoiceOut {
    struct { unsigned bytes_per_frame, nchannels; } info;
    size_t pos_emul, pending_emul, size_emul;
    uint8_t *buf_emul;
} HWVoiceOut;
typedef struct coreaudioVoiceOut {
    HWVoiceOut hw;
    AudioDeviceID outputDeviceID;
    uint64_t io_callbacks, io_frames, io_underruns, io_underrun_frames;
} coreaudioVoiceOut;
static unsigned locked;
static int coreaudio_buf_lock(coreaudioVoiceOut *c, const char *name) {
    assert(!locked); locked = 1; return 0;
}
static int coreaudio_buf_unlock(coreaudioVoiceOut *c, const char *name) {
    assert(locked); locked = 0; return 0;
}
'''+helpers+ring+callback+r'''
static coreaudioVoiceOut voice(float *pcm, size_t capacity, size_t pos, size_t pending) {
    coreaudioVoiceOut c = { .outputDeviceID = 7 };
    c.hw.info.bytes_per_frame = 8; c.hw.info.nchannels = 2;
    c.hw.buf_emul = (void *)pcm; c.hw.size_emul = capacity * 8;
    c.hw.pos_emul = pos * 8; c.hw.pending_emul = pending * 8;
    return c;
}
static void run(coreaudioVoiceOut *c, AudioBufferList *b) {
    assert(audioDeviceIOProc(7, NULL, NULL, NULL, b, NULL, c) == 0);
    assert(!locked);
}
int main(void) {
    /* Exact live failure:470real frames left in a512-frame callback. */
    float input[1024], output[1026];
    for (unsigned i=0; i<1024; i++) input[i] = (float)i + .25f;
    for (unsigned i=0; i<1026; i++) output[i] = -99.f;
    AudioBufferList stereo = { 1, {{2, 4096, output}} };
    coreaudioVoiceOut c = voice(input, 512, 470, 470);
    run(&c, &stereo);
    assert(c.hw.pending_emul == 0 && c.io_frames == 470);
    assert(c.io_callbacks == 1 && c.io_underruns == 1 && c.io_underrun_frames == 42);
    for (unsigned i=0; i<940; i++) assert(output[i] == input[i]);
    for (unsigned i=940; i<1024; i++) assert(output[i] == 0.f);
    assert(output[1024] == -99.f && output[1025] == -99.f);
    memset(output, 0x7f, sizeof(output));
    run(&c, &stereo);
    for (unsigned i=0; i<1024; i++) assert(output[i] == 0.f);
    assert(c.io_frames == 470 && c.io_underrun_frames == 554);

    /* A three-frame source wraps at ring end; copied order must survive. */
    float wrapped[10] = {2,12, 3,13, 0,0, 0,0, 1,11};
    float interleaved[10];
    AudioBufferList small = { 1, {{2, sizeof(interleaved), interleaved}} };
    c = voice(wrapped, 5, 2, 3); run(&c, &small);
    float expected[10] = {1,11, 2,12, 3,13, 0,0, 0,0};
    assert(memcmp(interleaved, expected, sizeof(expected)) == 0);
    assert(c.hw.pending_emul == 0 && c.io_frames == 3 && c.io_underrun_frames == 2);

    /* Split mono buffers preserve L/R; unrelated output channels are zero. */
    float left[5], right[5], spare[5];
    memset(left, 0x7f, sizeof(left)); memset(right, 0x7f, sizeof(right));
    memset(spare, 0x7f, sizeof(spare));
    struct { UInt32 count; AudioBuffer buffers[3]; } split =
        {3, {{1,sizeof(left),left},{1,sizeof(right),right},{1,sizeof(spare),spare}}};
    c = voice(wrapped, 5, 2, 3); run(&c, (AudioBufferList *)&split);
    float expected_left[5] = {1,2,3,0,0}, expected_right[5] = {11,12,13,0,0};
    assert(memcmp(left, expected_left, sizeof(left)) == 0);
    assert(memcmp(right, expected_right, sizeof(right)) == 0);
    for (unsigned i=0; i<5; i++) assert(spare[i] == 0.f);
    assert(c.hw.pending_emul == 0 && c.io_frames == 3);

    /* A wider interleaved host group gets silence in its spare channels. */
    float wider[20]; AudioBufferList group = {1,{{4,sizeof(wider),wider}}};
    c = voice(wrapped, 5, 2, 3); run(&c, &group);
    for (unsigned frame=0; frame<5; frame++) {
        assert(wider[frame*4] == expected_left[frame]);
        assert(wider[frame*4+1] == expected_right[frame]);
        assert(wider[frame*4+2] == 0.f && wider[frame*4+3] == 0.f);
    }

    /* Missing host storage must not drop queued PCM or expose stale output. */
    split.buffers[1].mData = NULL;
    memset(left, 0x7f, sizeof(left));
    c = voice(wrapped, 5, 2, 3); run(&c, (AudioBufferList *)&split);
    assert(c.hw.pending_emul == 24 && c.io_frames == 0);
    for (unsigned i=0; i<5; i++) assert(left[i] == 0.f);
    puts("PASS: partial470/512, empty, ring wrap, split mono, spare channels, missing storage");
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix='w800-coreaudio-test-') as directory:
            root = Path(directory)
            (root / 'test.c').write_text(harness)
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra',
                '-Wno-unused-parameter', '-Wno-unused-but-set-parameter',
                str(root / 'test.c'), '-o', str(root / 'test')], check=True,
                capture_output=True, text=True)
            result = subprocess.run([str(root / 'test')], check=True,
                                    capture_output=True, text=True)
        self.assertIn('PASS: partial470/512', result.stdout)
        (ROOT / 'reports/coreaudio-buffer-test.txt').write_text(result.stdout)


if __name__ == '__main__':
    unittest.main()
