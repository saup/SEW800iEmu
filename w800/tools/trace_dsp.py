"""Record original DSP uploads and reconstruct their memory image.

Breakpoints inspect ARM function arguments; guest instructions are not changed.
Only private local Unix sockets are used. No network requests are made.
"""
import argparse
import json
import socket
import time
from w800.backend import QemuBackend, ROOT
from w800.tools.debug import Debugger


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--flash', help='Local flash including prepared GDFS')
    args = parser.parse_args()
    rows = []
    image = bytearray(0x1000000)
    highest = 0
    with QemuBackend(exploratory=True, debug=True, mmio_limit=1000000,
                     flash_path=args.flash) as q:
        debug = Debugger(q)
        debug.sock.settimeout(3)
        debug.breakpoint(0x449ff6f4, thumb=True)
        deadline = time.monotonic() + 30
        try:
            while time.monotonic() < deadline and len(rows) < 2000:
                debug.run()
                regs = debug.registers()
                if regs[15] != 0x449ff6f4:
                    break
                address, words, source = regs[:3]
                if address >= 0x800000 or words > 0x800000 - address:
                    raise ValueError('DSP upload outside the 23-bit word space')
                data = debug.read(source, words * 2)
                start, end = address * 2, (address + words) * 2
                # ARM storage is little-endian halfwords; C55x program bytes
                # are read in big-endian order within each DSP halfword.
                image[start:end] = b''.join(data[n:n+2][::-1]
                                            for n in range(0, len(data), 2))
                highest = max(highest, end)
                row = {'dsp_address': hex(address), 'words': words,
                       'arm_source': hex(source), 'caller': hex(regs[14])}
                rows.append(row)
                print(row, flush=True)
                debug.step_over_breakpoint(0x449ff6f4, thumb=True)
        except socket.timeout:
            print('No new upload within three seconds.')
        finally:
            q.command('stop')
            debug.close()
    (ROOT/'reports/dsp-uploads.json').write_text(json.dumps(rows, indent=2)+'\n')
    (ROOT/'reports/dsp-loaded.bin').write_bytes(image[:highest])


if __name__ == '__main__':
    main()
