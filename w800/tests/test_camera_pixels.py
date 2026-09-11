"""Compile the actual camera conversion helper; validate native memory layout."""
import ctypes
from pathlib import Path
import subprocess
import tempfile
import unittest
from w800.backend import ROOT


class CameraPixelsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='w800-camera-pixels-')
        root = Path(cls.temporary.name)
        source = root / 'wrapper.c'
        source.write_text('#include "w800-camera-pixels.h"\n'
                          'int convert(const unsigned char*s, unsigned long sn, unsigned sw, unsigned sh, unsigned stride, unsigned char*d, unsigned long dn, unsigned w, unsigned h) { return w800_camera_bgra_yuyv(s,sn,sw,sh,stride,d,dn,w,h); }\n')
        output = root / 'camera.dylib'
        subprocess.run(['clang', '-shared', '-fPIC', '-O2', '-I', str(ROOT / 'qemu'),
                        str(source), '-o', str(output)], check=True, capture_output=True)
        cls.library = ctypes.CDLL(str(output))
        cls.convert = cls.library.convert
        cls.convert.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint,
                                ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p,
                                ctypes.c_size_t, ctypes.c_uint, ctypes.c_uint]
        cls.convert.restype = ctypes.c_int

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_packed_pair_black_white_and_primaries(self):
        for bgra, expected in [(bytes([0,0,0,255])*2, bytes([16,128,16,128])),
                               (bytes([255,255,255,255])*2, bytes([235,128,235,128])),
                               (bytes([0,0,255,255])*2, bytes([82,90,82,240])),
                               (bytes([0,255,0,255])*2, bytes([144,54,144,34])),
                               (bytes([255,0,0,255])*2, bytes([41,240,41,110]))]:
            output = ctypes.create_string_buffer(4)
            self.assertEqual(self.convert(bgra, len(bgra), 2, 1, 8, output, 4, 2, 1), 1)
            self.assertEqual(output.raw, expected)

    def test_requested_resize_uses_source_pixels_and_chroma_pair_average(self):
        # 4x2 source;2x1 output samples the central source positions(1,1),(3,1).
        source = bytes([0,0,0,255])*4 + bytes([0,0,0,255, 0,0,255,255,
                                             0,0,0,255, 255,0,0,255])
        output = ctypes.create_string_buffer(4)
        self.assertEqual(self.convert(source, len(source), 4, 2, 16, output, 4, 2, 1), 1)
        self.assertEqual(output.raw, bytes([82,165,41,175]))

    def test_rejects_bad_dimensions_strides_and_capacity_without_writes(self):
        source = bytes(32)
        for sw, sh, stride, sn, dn, w, h in [(0,2,8,32,8,2,2), (2,2,7,32,8,2,2),
                                             (2,2,8,15,8,2,2), (2,2,8,32,7,2,2),
                                             (2,2,8,32,8,3,1), (2,2,8,32,8,2,0)]:
            output = ctypes.create_string_buffer(b'12345678', 8)
            self.assertEqual(self.convert(source, sn, sw, sh, stride, output, dn, w, h), 0)
            self.assertEqual(output.raw, b'12345678')


if __name__ == '__main__': unittest.main()
