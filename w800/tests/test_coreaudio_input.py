"""Host microphone FIFO preserves sample order and bounded ownership."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from w800.backend import ROOT


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('cc'), 'macOS C toolchain required')
class CoreAudioInputTests(unittest.TestCase):
    def test_mono_planar_stereo_wrap_overflow_and_missing_storage(self):
        header = ROOT / 'qemu/coreaudio-input-buffer.h'
        harness = r'''
#include <CoreAudio/CoreAudio.h>
#include <assert.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>
''' + '#include "' + str(header) + '"\n' + r'''
int main(void) {
    float storage[10], output[12];
    CoreaudioInputBuffer ring = {.data=storage,.capacity=5,.channels=1};
    float mono[4] = {.1f,.2f,.3f,.4f};
    AudioBufferList one = {1,{{1,sizeof(mono),mono}}};
    assert(coreaudio_input_push(&ring,&one)==4);
    assert(coreaudio_input_pop(&ring,output,3*sizeof(float))==3*sizeof(float));
    assert(memcmp(output,mono,3*sizeof(float))==0);
    /* The retained .4 plus four new samples crosses the physical ring end. */
    assert(coreaudio_input_push(&ring,&one)==4);
    assert(coreaudio_input_push(&ring,&one)==0);
    assert(ring.captured==8 && ring.dropped==4 && ring.used==5);
    assert(coreaudio_input_pop(&ring,output,sizeof(output))==5*sizeof(float));
    float expected[5] = {.4f,.1f,.2f,.3f,.4f};
    assert(memcmp(output,expected,sizeof(expected))==0);
    assert(ring.delivered==8 && ring.used==0);
    assert(coreaudio_input_pop(&ring,output,sizeof(output))==0);

    float left[4]={1,2,3,4}, right[3]={11,12,13};
    struct {UInt32 count;AudioBuffer buffers[2];} planar =
        {2,{{1,sizeof(left),left},{1,sizeof(right),right}}};
    ring=(CoreaudioInputBuffer){.data=storage,.capacity=5,.channels=2};
    assert(coreaudio_input_push(&ring,(AudioBufferList*)&planar)==3);
    /* A byte-short destination can take only complete stereo frames. */
    assert(coreaudio_input_pop(&ring,output,15)==8);
    assert(output[0]==1 && output[1]==11);
    assert(ring.used==2);
    planar.buffers[1].mData=NULL;
    assert(coreaudio_input_push(&ring,(AudioBufferList*)&planar)==0);
    assert(ring.used==2 && ring.dropped==0);
    planar.buffers[1].mData=right;
    assert(coreaudio_input_push(&ring,(AudioBufferList*)&planar)==3);
    assert(coreaudio_input_pop(&ring,output,sizeof(output))==40);
    float stereo[10]={2,12,3,13,1,11,2,12,3,13};
    assert(memcmp(output,stereo,sizeof(stereo))==0);
    assert(coreaudio_input_push(&ring,(AudioBufferList*)&planar)==3);
    coreaudio_input_discard(&ring);
    assert(ring.used==0 && ring.discarded==3);
    assert(coreaudio_input_pop(&ring,output,sizeof(output))==0);
    puts("PASS: mono, planar stereo, partial frame, wrap, retained overflow, missing storage");
}
'''
        with tempfile.TemporaryDirectory(prefix='w800-microphone-test-') as directory:
            directory = Path(directory)
            (directory / 'test.c').write_text(harness)
            build = subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',
                str(directory/'test.c'),'-o',str(directory/'test')], capture_output=True,text=True)
            self.assertEqual(build.returncode,0,build.stderr)
            result = subprocess.run([str(directory/'test')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('PASS: mono',result.stdout)


if __name__ == '__main__':
    unittest.main()
