"""Inspect original firmware printf calls through local QEMU breakpoints.

Run as .venv/bin/python -m w800.tools.trace_messages. No guest code is patched.
The JSON retains original format strings and arguments, alongside a best-effort
host decoding of common 32-bit printf formats. This is not a guest UART model.
"""
import json
import argparse
import re
import struct
import time
from w800.backend import QemuBackend, ROOT
from .debug import Debugger


def cstring(debug, address):
    if not (0x44000000 <= address < 0x46000000 or 0x4c000000 <= address < 0x4c800000
            or 0 <= address < 0x10000 or 0x42000000 <= address < 0x42010000):
        return f'<pointer {address:#x}>'
    return debug.read(address, 512).split(b'\0', 1)[0].decode('ascii', errors='replace')


def decode(debug, fmt, arguments):
    values = iter(arguments)
    def replace(match):
        spec = match.group()
        if spec == '%%': return '%'
        value = next(values)
        kind = spec[-1]
        if kind == 's': return cstring(debug, value)
        if kind == 'p': return f'0x{value:08x}'
        if kind in 'di' and value & 0x80000000: value -= 1 << 32
        return spec.replace('l', '').replace('z', '') % value
    try:
        return re.sub(r'%%|%[-+ #0]*\d*(?:\.\d+)?[lz]?[diuxXoscp]', replace, fmt)
    except (ValueError, TypeError, StopIteration):
        return fmt + ' [raw arguments in JSON]'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--platform', action='store_true', help='Also inspect the platform logger')
    parser.add_argument('--flash', help='Alternate local prepared flash image')
    parser.add_argument('--output-prefix', help='Report basename within reports/')
    options = parser.parse_args()
    if options.output_prefix and not re.fullmatch(r'[a-zA-Z0-9_-]+', options.output_prefix):
        parser.error('--output-prefix must be a simple report basename')
    messages = []
    stop = 'message limit'
    with QemuBackend(exploratory=True, debug=True, mmio_limit=200000, flash_path=options.flash) as q:
        debug = Debugger(q)
        debug.sock.settimeout(3)
        loggers = {0x45169c70}
        if options.platform:
            loggers.add(0x44ad6474)
        for printf in loggers:
            debug.breakpoint(printf, thumb=True)
        started = time.monotonic()
        try:
            for index in range(10000):
                debug.run()
                regs = debug.registers()
                printf = regs[15]
                if printf not in loggers:
                    stop = f'guest paused at {regs[15]:#x}'; break
                fmt = cstring(debug, regs[0])
                args = list(regs[1:4]) + list(struct.unpack('<8I', debug.read(regs[13], 32)))
                text = decode(debug, fmt, args)
                messages.append({'logger': hex(printf), 'caller': hex(regs[14]), 'format_address': hex(regs[0]),
                                 'format': fmt, 'arguments': args, 'decoded': text})
                if 'Uncomplete erase' not in text:
                    print(text.strip(), flush=True)
                debug.step_over_breakpoint(printf, thumb=True)
                if time.monotonic() - started > 45:
                    stop = 'host time budget'; break
        except TimeoutError:
            stop = 'no printf breakpoint within 3 seconds'
        finally:
            q.command('stop')
            report = q.snapshot()
            debug.close()
    name = options.output_prefix or ('platform-messages' if options.platform else 'guest-messages')
    (ROOT / f'reports/{name}.json').write_text(json.dumps(
        {'stop': stop, 'messages': messages, 'snapshot': report}, indent=2) + '\n')
    print(f'{len(messages)} original printf calls captured; {stop}.')


if __name__ == '__main__': main()
