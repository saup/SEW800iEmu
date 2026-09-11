"""Execute the actual compatible DSP equalizer and compare independent transfers."""
import cmath
import ctypes
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from w800.backend import ROOT


@unittest.skipUnless(shutil.which('cc'), 'A local C compiler is required')
class EqualizerArithmeticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory=tempfile.TemporaryDirectory()
        directory=Path(cls.directory.name)
        source=directory/'equalizer.c'
        mixer_source=(ROOT/'qemu/w800-dsp.inc').read_text()
        mixer_source=mixer_source[mixer_source.index('typedef struct W800DspMixerTable'):mixer_source.index('static uint16_t w800_dsp_word')]
        source.write_text('#include <stdint.h>\n#include <stdbool.h>\n#include <stdlib.h>\n#include <string.h>\n'+
            (ROOT/'qemu/w800-dsp-equalizer.inc').read_text()+
            '\n#define error_report(...) ((void)0)\n'+mixer_source+r'''
void *make(void) { return calloc(1, sizeof(W800DspEqualizer)); }
void dispose(void *p) { free(p); }
int configure(void *p, unsigned enabled, unsigned count, unsigned index,
              unsigned exponent, const int16_t *c) {
    return w800_dsp_equalizer_configure(p, enabled, count, index, exponent, c);
}
void reset(void *p) { w800_dsp_equalizer_reset(p); }
void process(void *p, unsigned side, const double *in, double *out, unsigned n) {
    for (unsigned i=0;i<n;i++) out[i]=w800_dsp_equalizer_process(p,side,in[i]);
}
uint64_t limited(void *p) { return ((W800DspEqualizer *)p)->limited; }
void *make_mixer(void) { return calloc(1, sizeof(W800DspMixer)); }
void mixer_route(void *p, unsigned in, unsigned out, unsigned gain) {
    W800DspMixer *m=p;
    m->output[out].selector=1; m->output[out].gain=16384;
    m->crosspoint[in][out].enabled=1; m->crosspoint[in][out].gain=gain;
}
int mixer_filter(void *p, unsigned out, const int16_t *c) {
    return w800_dsp_equalizer_configure(&((W800DspMixer *)p)->equalizer[out],1,1,0,0,c);
}
void mixer_process(void *p, unsigned input, unsigned side,
                   const double *in, double *out, unsigned n) {
    for (unsigned i=0;i<n;i++) out[i]=w800_dsp_mixer_process_sample(p,input,side,in[i]);
}
''')
        library=directory/('equalizer.dylib' if sys.platform=='darwin' else 'equalizer.so')
        command=['cc','-std=c11','-O2','-Wall','-Wextra','-Werror',
                 '-dynamiclib' if sys.platform=='darwin' else '-shared','-fPIC',str(source),'-o',str(library)]
        subprocess.run(command,check=True,capture_output=True,text=True)
        cls.lib=ctypes.CDLL(str(library))
        cls.lib.make.restype=ctypes.c_void_p
        cls.lib.dispose.argtypes=[ctypes.c_void_p]
        cls.lib.configure.argtypes=[ctypes.c_void_p,*([ctypes.c_uint]*4),ctypes.POINTER(ctypes.c_int16)]
        cls.lib.reset.argtypes=[ctypes.c_void_p]
        cls.lib.process.argtypes=[ctypes.c_void_p,ctypes.c_uint,ctypes.POINTER(ctypes.c_double),ctypes.POINTER(ctypes.c_double),ctypes.c_uint]
        cls.lib.limited.argtypes=[ctypes.c_void_p]
        cls.lib.limited.restype=ctypes.c_uint64
        cls.lib.make_mixer.restype=ctypes.c_void_p
        cls.lib.mixer_route.argtypes=[ctypes.c_void_p,*([ctypes.c_uint]*3)]
        cls.lib.mixer_filter.argtypes=[ctypes.c_void_p,ctypes.c_uint,ctypes.POINTER(ctypes.c_int16)]
        cls.lib.mixer_process.argtypes=[ctypes.c_void_p,ctypes.c_uint,ctypes.c_uint,ctypes.POINTER(ctypes.c_double),ctypes.POINTER(ctypes.c_double),ctypes.c_uint]

    @classmethod
    def tearDownClass(cls):cls.directory.cleanup()

    def setUp(self):self.eq=self.lib.make()
    def tearDown(self):self.lib.dispose(self.eq)
    def configure(self,c,exponent=0,index=0,count=1):
        return self.lib.configure(self.eq,1,count,index,exponent,(ctypes.c_int16*5)(*c))
    def process(self,values,side=0):
        n=len(values);source=(ctypes.c_double*n)(*values);result=(ctypes.c_double*n)()
        self.lib.process(self.eq,side,source,result,n)
        return list(result)

    def test_impulse_feedback_exponent_and_channel_independence(self):
        # H(z)=0.5/(1-0.5z^-1), exponent is numerator-only.
        self.assertTrue(self.configure([4096,0,0,-8192,0],exponent=1))
        self.assertEqual(self.process([1,0,0,0,0]),[.5,.25,.125,.0625,.03125])
        self.assertEqual(self.process([0]*5,side=1),[0]*5)
        self.lib.reset(self.eq)
        self.assertEqual(self.process([1,0]),[.5,.25])
        # Replacing a stage clears that stage's retained state.
        self.assertTrue(self.configure([16384,0,0,0,0]))
        self.assertEqual(self.process([0,17,-23]),[0,17,-23])

    def test_disable_count_change_unstable_rejection_and_guard(self):
        self.assertTrue(self.configure([8192,0,0,-8192,0]))
        self.process([100])
        self.assertFalse(self.configure([8192,0,0,-32768,16384]))
        self.assertFalse(self.configure([8192,0,0,0,0],exponent=16))
        self.assertEqual(self.process([0]),[25]) # rejection retained actual filter
        self.assertTrue(self.lib.configure(self.eq,0,65535,65535,65535,None))
        self.assertEqual(self.process([100,-17]),[100,-17])
        self.assertTrue(self.configure([8192,0,0,-8192,0]))
        self.process([100])
        self.assertTrue(self.configure([16384,0,0,0,0],index=1,count=2))
        self.assertEqual(self.process([0,100]),[0,100])
        self.process([float('inf'),1e100])
        self.assertGreater(self.lib.limited(self.eq),0)
        self.assertTrue(all(math.isfinite(x) for x in self.process([0]*20)))

    def test_cascade_order_and_internal_headroom(self):
        # Gain of four then one quarter must not clip between stages.
        self.assertTrue(self.configure([16384,0,0,0,0],exponent=2,count=2,index=0))
        self.assertTrue(self.configure([4096,0,0,0,0],count=2,index=1))
        self.assertEqual(self.process([32767,-32768,20000]),[32767,-32768,20000])
        self.assertEqual(self.lib.limited(self.eq),0)

    def test_actual_mixer_applies_output_target_with_routing_and_gains(self):
        mixer=self.lib.make_mixer()
        try:
            self.lib.mixer_route(mixer,4,2,16384)
            self.lib.mixer_route(mixer,4,0,4096)
            self.assertTrue(self.lib.mixer_filter(mixer,2,(ctypes.c_int16*5)(8192,0,0,-8192,0)))
            def process(input,side,values):
                n=len(values);source=(ctypes.c_double*n)(*values);out=(ctypes.c_double*n)()
                self.lib.mixer_process(mixer,input,side,source,out,n)
                return list(out)
            # Input 4 routes to output 2's low-pass plus output 0's direct
            # quarter gain. Filtering an input-index target would fail this.
            self.assertEqual(process(4,0,[1,0,0]),[.75,.25,.125])
            self.assertEqual(process(4,1,[0,0,0]),[0,0,0])
            self.assertEqual(process(6,0,[100,100]),[0,0])
        finally:self.lib.dispose(mixer)

    def test_native_presets_impulse_frequency_response_and_flat_identity(self):
        fixture=Path(__file__).with_name('fixtures')/'walkman-equalizer-presets.json'
        presets=json.loads(fixture.read_text())
        for name,preset in presets['presets'].items():
            with self.subTest(preset=name):
                self.lib.configure(self.eq,0,0,0,0,None)
                stages=preset['stages']
                for words in stages:
                    c=[x if x<32768 else x-65536 for x in words[6:11]]
                    self.assertTrue(self.configure(c,exponent=words[5],index=words[4],count=words[3]))
                if name=='normal':
                    values=[32767,-32768,0,123.25,-444.125]*20
                    self.assertEqual(self.process(values),values)
                self.lib.reset(self.eq)
                impulse=self.process([1.0]+[0.0]*16383)
                if name!='normal':self.assertNotEqual(impulse[:20],[1.0]+[0.0]*19)
                measured_db={}
                for frequency in (31.5,63,125,250,1000,4000,12000,16000):
                    z=cmath.exp(-2j*math.pi*frequency/presets['sample_rate'])
                    theoretical=1+0j
                    for words in stages:
                        b0,b1,b2,a1,a2=[x if x<32768 else x-65536 for x in words[6:11]]
                        theoretical*=2**words[5]*(b0+b1*z+b2*z*z)/(16384+a1*z+a2*z*z)
                    measured=sum(value*z**i for i,value in enumerate(impulse))
                    self.assertAlmostEqual(abs(measured),abs(theoretical),delta=1e-7)
                    measured_db[frequency]=20*math.log10(abs(measured))
                if name=='mega-bass':
                    self.assertGreater(measured_db[31.5],10)
                    self.assertLess(abs(measured_db[1000]),1)
                self.assertEqual(self.lib.limited(self.eq),0)


if __name__=='__main__':unittest.main()
